"""Private original-time connection of native capture and serving-v2 P11 truth."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0097_native_atomic_capture"
down_revision = "0096_native_listen_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the independently qualified legacy writer. The new private writer
    # adds strict origin gates and uses a same-transaction original creation clock.
    definition = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef("
            "'app_private.commit_native_served_authority(uuid,uuid,uuid,uuid,bigint,jsonb)'::regprocedure)"
        )
    )
    if not isinstance(definition, str) or definition.count("created:=clock_timestamp();") != 1:
        raise RuntimeError("native atomic writer anchor changed")
    definition = definition.replace(
        "app_private.commit_native_served_authority(", "app_private.commit_native_atomic_served(", 1
    )
    definition = definition.replace("doc jsonb)", "doc jsonb, p_original uuid)", 1)
    atomic_start = """
    PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
    PERFORM 1 FROM account.training_consent WHERE user_id=p_owner FOR UPDATE;
    SELECT r.created_at INTO created FROM ml.recommendation_request r
     JOIN ml.sona_capture_bundle b USING(recommendation_request_id)
     WHERE r.recommendation_request_id=p_original AND r.user_id=p_owner AND b.user_id=p_owner
      AND r.serving_contract_version=1
      AND r.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint
      AND b.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint;
    IF created IS NULL OR created>clock_timestamp() OR created+interval
 '180 days'<=clock_timestamp()
     OR request_id=p_original OR current_setting('transaction_isolation')<>'repeatable read' THEN
      RAISE EXCEPTION 'NATIVE_ATOMIC_ORIGINAL_TRANSACTION_REQUIRED' USING ERRCODE='23514'; END IF;
    IF NOT EXISTS(SELECT 1 FROM app_private.native_atomic_capture_context c
      JOIN ml.recommendation_request r ON r.recommendation_request_id=p_original
      JOIN ml.sona_capture_bundle b USING(recommendation_request_id)
      JOIN account.training_consent t ON t.user_id=p_owner
      WHERE c.transaction_id=pg_current_xact_id() AND c.owner_user_id=p_owner
       AND r.created_at>=c.started_at-interval '30 seconds' AND r.created_at<=clock_timestamp()
       AND t.decision='GRANTED' AND t.revision=b.consent_generation)
     OR EXISTS(SELECT 1 FROM (VALUES('recommendation_request',p_original),
       ('sona_capture_bundle',p_original),('recommendation_input_snapshot',
         (SELECT recommendation_input_snapshot_id FROM ml.recommendation_request
           WHERE recommendation_request_id=p_original))) AS required(table_name,row_id)
       WHERE NOT EXISTS(SELECT 1 FROM app_private.native_atomic_capture_insert_witness w
         WHERE w.transaction_id=pg_current_xact_id() AND w.owner_user_id=p_owner
          AND w.table_name=required.table_name AND w.row_id=required.row_id)) THEN
      RAISE EXCEPTION 'NATIVE_ATOMIC_INSERT_WITNESS_REQUIRED' USING ERRCODE='23514'; END IF;
    """
    prior_anchor = " SELECT * INTO prior FROM ml.recommendation_request_replay_payload WHERE"
    retry_anchor = "RETURN app_private.read_native_served_authority(p_owner,request_id);"
    if definition.count(prior_anchor) != 1 or definition.count(retry_anchor) != 1:
        raise RuntimeError("native atomic origin/retry anchor changed")
    definition = definition.replace(prior_anchor, atomic_start + prior_anchor, 1)
    definition = definition.replace("created:=clock_timestamp();", "", 1)
    definition = definition.replace(
        retry_anchor,
        "RAISE EXCEPTION 'NATIVE_ATOMIC_SERVED_RETRY_FORBIDDEN' USING ERRCODE='23514';",
        1,
    )
    op.execute(definition)
    snapshot = (
        Path(__file__).with_name("0097_native_atomic_snapshot.sql").read_text(encoding="utf-8")
    )
    op.execute(_INSTALL_SQL.replace("-- INITIAL_SQL_SNAPSHOT", snapshot))


def downgrade() -> None:
    op.execute("""
LOCK TABLE app_private.native_atomic_capture_context,
 app_private.native_atomic_capture_insert_witness,ml.recommendation_input_snapshot,
 ml.recommendation_request,ml.sona_capture_bundle,ml.native_atomic_capture_connection
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection) THEN
 RAISE EXCEPTION 'refusing native atomic downgrade with retained evidence' USING ERRCODE='55000';
END IF; END $$;
DROP TRIGGER z_native_atomic_served_retention ON ml.recommendation_request_replay_payload;
DROP TRIGGER z_native_atomic_item_closure ON ml.recommendation_item;
DROP TRIGGER z_native_atomic_root_closure ON ml.recommendation_request;
DROP TRIGGER z_native_atomic_snapshot_insert ON ml.recommendation_input_snapshot;
DROP TRIGGER z_native_atomic_request_insert ON ml.recommendation_request;
DROP TRIGGER z_native_atomic_bundle_insert ON ml.sona_capture_bundle;
DROP TABLE ml.native_atomic_capture_connection;
DROP TABLE app_private.native_atomic_capture_context;
DROP TABLE app_private.native_atomic_capture_insert_witness;
DROP FUNCTION app_private.arm_native_atomic_capture(uuid);
DROP FUNCTION app_private.witness_native_atomic_insert();
DROP FUNCTION app_private.check_native_atomic_context();
DROP FUNCTION app_private.expire_native_atomic_capture();
DROP FUNCTION app_private.check_native_atomic_capture();
DROP FUNCTION app_private.protect_native_atomic_capture();
DROP FUNCTION app_private.assert_native_atomic_capture(uuid,boolean);
DROP FUNCTION app_private.commit_native_atomic_capture(uuid,uuid,uuid,uuid,uuid,bigint,jsonb);
DROP FUNCTION app_private.commit_native_atomic_served(uuid,uuid,uuid,uuid,bigint,jsonb,uuid);
""")


_INSTALL_SQL = r"""
CREATE TABLE app_private.native_atomic_capture_context (
 transaction_id xid8 NOT NULL,owner_user_id uuid NOT NULL,started_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,owner_user_id)
);
CREATE TABLE app_private.native_atomic_capture_insert_witness (
 transaction_id xid8 NOT NULL,owner_user_id uuid NOT NULL,table_name text NOT NULL,
 row_id uuid NOT NULL,PRIMARY KEY(transaction_id,table_name,row_id)
);
CREATE FUNCTION app_private.arm_native_atomic_capture(owner_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_ORIGINAL_TRANSACTION_REQUIRED' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_atomic_capture_context
  VALUES(pg_current_xact_id(),owner_id,clock_timestamp());
END $$;
CREATE FUNCTION app_private.witness_native_atomic_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row_id uuid;
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.native_atomic_capture_context
  WHERE transaction_id=pg_current_xact_id() AND owner_user_id=NEW.user_id) THEN
  row_id:=CASE WHEN TG_TABLE_NAME='recommendation_input_snapshot' THEN
   (to_jsonb(NEW)->>'recommendation_input_snapshot_id')::uuid ELSE
   (to_jsonb(NEW)->>'recommendation_request_id')::uuid END;
  INSERT INTO app_private.native_atomic_capture_insert_witness
   VALUES(pg_current_xact_id(),NEW.user_id,TG_TABLE_NAME,row_id);
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.check_native_atomic_context() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.native_atomic_capture_context
   WHERE transaction_id=NEW.transaction_id AND owner_user_id=NEW.owner_user_id)
  AND NOT EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection
   WHERE created_transaction=NEW.transaction_id AND owner_user_id=NEW.owner_user_id) THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_ARMED_GRAPH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE TABLE ml.native_atomic_capture_connection (
 original_request_id uuid PRIMARY KEY REFERENCES ml.sona_capture_bundle ON DELETE CASCADE,
 served_request_id uuid NOT NULL UNIQUE REFERENCES ml.recommendation_request ON DELETE CASCADE,
 owner_user_id uuid NOT NULL REFERENCES account.user_account ON DELETE CASCADE,
 created_transaction xid8 NOT NULL,
 capture_sha256 bytea NOT NULL CHECK(octet_length(capture_sha256)=32),
 served_document_sha256 bytea NOT NULL CHECK(octet_length(served_document_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 consent_generation bigint NOT NULL CHECK(consent_generation>=1),
 consent_receipt_sha256 bytea NOT NULL CHECK(octet_length(consent_receipt_sha256)=32),
 CHECK(original_request_id<>served_request_id)
);

CREATE FUNCTION app_private.assert_native_atomic_capture(original uuid,initial boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c ml.native_atomic_capture_connection%ROWTYPE; b ml.sona_capture_bundle%ROWTYPE;
 r ml.recommendation_request%ROWTYPE; v ml.recommendation_request%ROWTYPE;
 p ml.recommendation_request_replay_payload%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
 s ml.recommendation_input_snapshot%ROWTYPE; capture jsonb; candidates jsonb; item_count bigint;
BEGIN
 SELECT * INTO c FROM ml.native_atomic_capture_connection WHERE original_request_id=original;
 IF c.original_request_id IS NULL THEN RETURN; END IF;
 IF initial THEN
  PERFORM 1 FROM account.user_account WHERE user_id=c.owner_user_id FOR UPDATE;
  PERFORM 1 FROM account.training_consent WHERE user_id=c.owner_user_id FOR UPDATE;
 END IF;
 SELECT * INTO b FROM ml.sona_capture_bundle WHERE recommendation_request_id=original;
 SELECT * INTO r FROM ml.recommendation_request WHERE recommendation_request_id=original;
 SELECT * INTO v FROM ml.recommendation_request WHERE recommendation_request_id=c.served_request_id;
 SELECT * INTO p FROM ml.recommendation_request_replay_payload WHERE
 recommendation_request_id=c.served_request_id;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE
 recommendation_request_id=c.served_request_id;
 SELECT * INTO s FROM ml.recommendation_input_snapshot
  WHERE recommendation_input_snapshot_id=r.recommendation_input_snapshot_id;
 capture:=convert_from(b.bundle_document,'UTF8')::jsonb;
 IF b.recommendation_request_id IS NULL OR r.recommendation_request_id IS NULL
  OR v.recommendation_request_id IS NULL OR p.recommendation_request_id IS NULL
  OR d.recommendation_request_id IS NULL OR s.recommendation_input_snapshot_id IS NULL
  OR capture->>'kind' IS DISTINCT FROM 'SONA_CAPTURE_BUNDLE_V2'
  OR capture->'schema_version' IS DISTINCT FROM '2'::jsonb
  OR r.serving_contract_version IS DISTINCT FROM 1 OR v.serving_contract_version IS DISTINCT FROM 2
  OR r.user_id IS DISTINCT FROM c.owner_user_id OR b.user_id IS DISTINCT FROM c.owner_user_id
 OR s.user_id IS DISTINCT FROM c.owner_user_id
  OR v.user_id IS DISTINCT FROM c.owner_user_id OR p.owner_user_id IS DISTINCT FROM c.owner_user_id
  OR r.surface IS DISTINCT FROM 'recommendations' OR v.surface IS DISTINCT FROM r.surface
  OR r.shadow OR v.shadow
  OR r.created_at IS DISTINCT FROM v.created_at OR r.created_at IS DISTINCT FROM b.created_at
  OR b.cutoff_at_ms IS DISTINCT FROM p.cutoff_at_ms OR b.expires_at IS DISTINCT FROM
 p.replay_expires_at
  OR b.expires_at IS DISTINCT FROM d.served_truth_expires_at OR s.retained_until<b.expires_at
  OR r.context IS DISTINCT FROM v.context OR r.seed IS DISTINCT FROM v.seed
  OR r.pipeline_key IS DISTINCT FROM v.pipeline_key OR r.pipeline_version IS DISTINCT FROM
 v.pipeline_version
  OR r.pipeline_manifest_sha256 IS DISTINCT FROM v.pipeline_manifest_sha256
  OR r.model_bundle_version IS DISTINCT FROM v.model_bundle_version
  OR r.candidate_policy_version IS DISTINCT FROM v.candidate_policy_version
  OR r.filter_policy_version IS DISTINCT FROM v.filter_policy_version OR r.reranker_version IS
 DISTINCT FROM v.reranker_version
  OR r.interaction_watermark IS DISTINCT FROM v.interaction_watermark OR r.catalog_snapshot IS
 DISTINCT FROM v.catalog_snapshot
  OR p.document->'snapshot_document' IS DISTINCT FROM s.snapshot_document
  OR r.input_snapshot_sha256 IS DISTINCT FROM s.input_snapshot_sha256
  OR r.input_snapshot_sha256 IS DISTINCT FROM b.baseline_snapshot_sha256
  OR r.interaction_watermark IS DISTINCT FROM s.interaction_watermark
  OR r.catalog_snapshot IS DISTINCT FROM s.catalog_snapshot
  OR r.availability_snapshot_ref IS DISTINCT FROM s.availability_snapshot
  OR r.policy_snapshot_sha256 IS DISTINCT FROM s.policy_snapshot_sha256
  OR p.document->'effective_limit' IS DISTINCT FROM r.request_document->'limit'
  OR (p.document->'request_features'->>'exploration_units_e8')::numeric IS DISTINCT FROM
      (r.request_document->>'exploration')::numeric*100000000
  OR c.capture_sha256<>b.bundle_sha256 OR c.served_document_sha256<>p.document_sha256
  OR c.decision_sha256<>d.decision_sha256 OR c.consent_generation IS DISTINCT FROM
 b.consent_generation
  OR c.consent_receipt_sha256<>b.consent_receipt_sha256
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR d.serving_provenance IS DISTINCT FROM 'P11' THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_ROOT_BINDING' USING ERRCODE='23514'; END IF;
 IF r.request_schema_version IS DISTINCT FROM 1 OR
  r.request_canonicalization_version IS DISTINCT FROM 1 OR
  r.request_features IS DISTINCT FROM jsonb_build_object('limit',p.effective_limit,
   'exploration',(p.document->'request_features'->>'exploration_units_e8')::numeric/100000000)
  OR r.request_document IS DISTINCT FROM (SELECT convert_from(request_document,'UTF8')::jsonb
   FROM ml.sona_capture_replay_payload WHERE recommendation_request_id=original)
  OR r.request_sha256 IS DISTINCT FROM (SELECT sha256(request_document)
   FROM ml.sona_capture_replay_payload WHERE recommendation_request_id=original)
  OR r.request_document IS DISTINCT FROM jsonb_build_object(
   'schema_version',1,'canonicalization_version',1,'user_id',r.user_id,
   'surface',r.surface,'context',r.context,'limit',p.effective_limit,
   'exploration',(p.document->'request_features'->>'exploration_units_e8')::numeric/100000000,
   'seed',r.seed,'shadow',false,'pipeline',jsonb_build_object(
    'key',r.pipeline_key,'version',r.pipeline_version,
    'manifest_sha256',encode(r.pipeline_manifest_sha256,'hex')),
   'snapshot',jsonb_build_object('snapshot_id',r.recommendation_input_snapshot_id,
    'input_snapshot_sha256',encode(r.input_snapshot_sha256,'hex'),
    'interaction_watermark',r.interaction_watermark,'catalog_snapshot',r.catalog_snapshot,
    'availability_snapshot',r.availability_snapshot_ref,
    'policy_snapshot_sha256',encode(r.policy_snapshot_sha256,'hex'))) THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_CAPTURE_REQUEST_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.assert_native_capture_closure(original,initial);
 PERFORM app_private.assert_native_served_authority(c.served_request_id);
 SELECT coalesce(jsonb_agg(x->'recording_id' ORDER BY x->>'recording_id'),'[]'::jsonb) INTO
 candidates
  FROM jsonb_array_elements(s.snapshot_document->'tracks') x
  WHERE x->'authorized'='true'::jsonb AND x->'excluded'='false'::jsonb
   AND x->>'identity_status'='ACTIVE' AND x->>'preference'<>'DISLIKED'
   AND x->>'availability' IN ('LOCAL','VAULT','EXTERNAL');
 IF candidates IS DISTINCT FROM capture->'candidate_recording_ids'
  OR jsonb_array_length(s.snapshot_document->'tracks')<>b.universe_count
  OR jsonb_array_length(candidates)<>b.eligible_count THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_CANDIDATE_MEMBERSHIP' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO item_count FROM ml.recommendation_item WHERE
 recommendation_request_id=original;
 IF item_count<>d.p11_item_count OR item_count<>d.served_item_count OR EXISTS(
  SELECT 1 FROM (SELECT * FROM ml.recommendation_item WHERE recommendation_request_id=original)
 a FULL JOIN
   (SELECT * FROM ml.recommendation_item WHERE recommendation_request_id=c.served_request_id) z
   ON z.rank=a.rank WHERE (
   a.rank IS NULL OR z.rank IS NULL OR a.recording_id IS DISTINCT FROM z.recording_id
   OR a.score IS DISTINCT FROM z.score OR a.candidate_sources IS DISTINCT FROM z.candidate_sources
   OR a.explanation_code IS DISTINCT FROM z.explanation_code
   OR a.reason_codes IS DISTINCT FROM z.reason_codes OR a.item_provenance IS DISTINCT FROM
 z.item_provenance
   OR a.contributions IS DISTINCT FROM (SELECT coalesce(jsonb_agg((x-'raw_score_units_e8')||
     jsonb_build_object('raw_score',(x->>'raw_score_units_e8')::numeric/100000000) ORDER BY
 ord),'[]'::jsonb)
     FROM jsonb_array_elements(z.contributions) WITH ORDINALITY AS con(x,ord))
   OR a.availability_snapshot IS DISTINCT FROM
 jsonb_build_object('snapshot_sha256',r.availability_snapshot_ref)
   OR z.availability_snapshot IS DISTINCT FROM
 jsonb_build_object('snapshot_sha256',v.availability_snapshot_ref)
  )) THEN RAISE EXCEPTION 'NATIVE_ATOMIC_ITEM_BINDING' USING ERRCODE='23514'; END IF;
 IF (SELECT convert_from(ranking_document,'UTF8')::jsonb
    FROM ml.sona_capture_replay_payload WHERE recommendation_request_id=original)
   IS DISTINCT FROM jsonb_build_object('schema_version',1,'kind','RECOMMENDATION_RANKING_V1',
    'items',(SELECT coalesce(jsonb_agg(jsonb_build_object(
      'recording_id',i.recording_id,'source_rank',i.rank,'score',i.score,
      'section',i.item_provenance->'section',
      'reason_code',i.explanation_code,'reason_codes',to_jsonb(i.reason_codes),
      'contributions',i.contributions,'artist_key',i.item_provenance->'artist_key',
      'release_key',i.item_provenance->'release_key') ORDER BY i.rank),'[]'::jsonb)
     FROM ml.recommendation_item i WHERE recommendation_request_id=original)) THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_CAPTURE_RANKING_BINDING' USING ERRCODE='23514'; END IF;
 IF initial THEN
  IF NOT EXISTS(SELECT 1 FROM app_private.native_atomic_capture_context
    WHERE transaction_id=pg_current_xact_id() AND owner_user_id=c.owner_user_id
     AND r.created_at>=started_at-interval '30 seconds' AND r.created_at<=clock_timestamp())
   OR NOT EXISTS(SELECT 1 FROM account.training_consent
    WHERE user_id=c.owner_user_id AND decision='GRANTED' AND revision=c.consent_generation)
   OR EXISTS(SELECT 1 FROM (VALUES('recommendation_request',original),
     ('sona_capture_bundle',original),
     ('recommendation_input_snapshot',s.recommendation_input_snapshot_id))
     AS required(table_name,row_id) WHERE NOT EXISTS(
      SELECT 1 FROM app_private.native_atomic_capture_insert_witness w
       WHERE w.transaction_id=pg_current_xact_id() AND w.owner_user_id=c.owner_user_id
        AND w.table_name=required.table_name AND w.row_id=required.row_id)) THEN
   RAISE EXCEPTION 'NATIVE_ATOMIC_INSERT_WITNESS_REQUIRED' USING ERRCODE='23514'; END IF;
  IF c.created_transaction<>pg_current_xact_id() OR
 current_setting('transaction_isolation')<>'repeatable read'
   OR NOT EXISTS(SELECT 1 FROM ml.sona_capture_bundle WHERE recommendation_request_id=original
    AND xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint)
   OR NOT EXISTS(SELECT 1 FROM ml.recommendation_input_snapshot
    WHERE recommendation_input_snapshot_id=s.recommendation_input_snapshot_id
    AND xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint)
   OR EXISTS(SELECT 1 FROM ml.recommendation_request WHERE recommendation_request_id IN
    (original,c.served_request_id) AND xmin::text::bigint<>
     mod(pg_current_xact_id()::text::numeric,4294967296)::bigint)
   OR b.expires_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'NATIVE_ATOMIC_ORIGINAL_TRANSACTION_REQUIRED' USING ERRCODE='23514'; END IF;
  -- INITIAL_SQL_SNAPSHOT
 END IF;
END $$;

CREATE FUNCTION app_private.protect_native_atomic_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' OR (TG_OP='DELETE' AND
  EXISTS(SELECT 1 FROM ml.sona_capture_bundle WHERE
 recommendation_request_id=OLD.original_request_id)
  AND EXISTS(SELECT 1 FROM ml.recommendation_request WHERE
 recommendation_request_id=OLD.served_request_id)
  AND NOT EXISTS(SELECT 1 FROM app_private.native_served_authority_context
   WHERE transaction_id=txid_current() AND recommendation_request_id=OLD.served_request_id AND
 operation='PURGE'))
 THEN RAISE EXCEPTION 'NATIVE_ATOMIC_CONNECTION_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP='INSERT' AND (NEW.created_transaction<>pg_current_xact_id() OR
  NOT EXISTS(SELECT 1 FROM ml.sona_capture_bundle WHERE
 recommendation_request_id=NEW.original_request_id
   AND xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint)) THEN
  RAISE EXCEPTION 'NATIVE_ATOMIC_ORIGINAL_TRANSACTION_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE FUNCTION app_private.check_native_atomic_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE original uuid;
BEGIN
 IF TG_TABLE_NAME='native_atomic_capture_connection' THEN
  PERFORM app_private.assert_native_atomic_capture(NEW.original_request_id,true);
  DELETE FROM app_private.native_atomic_capture_insert_witness
   WHERE transaction_id=pg_current_xact_id() AND owner_user_id=NEW.owner_user_id;
  DELETE FROM app_private.native_atomic_capture_context
   WHERE transaction_id=pg_current_xact_id() AND owner_user_id=NEW.owner_user_id;
 ELSE
  FOR original IN SELECT original_request_id FROM ml.native_atomic_capture_connection WHERE
   original_request_id=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE
 NEW.recommendation_request_id END
   OR served_request_id=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE
 NEW.recommendation_request_id END
  LOOP PERFORM app_private.assert_native_atomic_capture(original,false); END LOOP;
 END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.expire_native_atomic_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM ml.sona_capture_bundle b USING ml.native_atomic_capture_connection c
  WHERE c.original_request_id=b.recommendation_request_id AND
 c.served_request_id=OLD.recommendation_request_id;
 RETURN OLD;
END $$;

CREATE FUNCTION app_private.commit_native_atomic_capture(original uuid,p_owner uuid,p_device uuid,
 p_epoch uuid,p_server uuid,p_identity bigint,doc jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; served uuid:=(doc->>'request_id')::uuid;
BEGIN
 result:=app_private.commit_native_atomic_served(p_owner,p_device,p_epoch,p_server,p_identity,doc,original);
 INSERT INTO ml.native_atomic_capture_connection SELECT
 original,served,p_owner,pg_current_xact_id(),
  b.bundle_sha256,p.document_sha256,d.decision_sha256,b.consent_generation,b.consent_receipt_sha256
  FROM ml.sona_capture_bundle b,ml.recommendation_request_replay_payload
 p,ml.recommendation_serving_decision d
  WHERE b.recommendation_request_id=original AND p.recommendation_request_id=served AND
 d.recommendation_request_id=served;
 PERFORM app_private.assert_native_atomic_capture(original,true);
 RETURN result;
END $$;
CREATE TRIGGER z_native_atomic_immutable BEFORE INSERT OR UPDATE OR DELETE ON
 ml.native_atomic_capture_connection
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_native_atomic_capture();
CREATE CONSTRAINT TRIGGER z_native_atomic_complete AFTER INSERT ON
 ml.native_atomic_capture_connection
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
 app_private.check_native_atomic_capture();
CREATE CONSTRAINT TRIGGER z_native_atomic_root_closure AFTER UPDATE OR DELETE ON
 ml.recommendation_request
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
 app_private.check_native_atomic_capture();
CREATE CONSTRAINT TRIGGER z_native_atomic_item_closure AFTER INSERT OR UPDATE OR DELETE ON
 ml.recommendation_item
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
 app_private.check_native_atomic_capture();
CREATE TRIGGER z_native_atomic_served_retention BEFORE DELETE ON
 ml.recommendation_request_replay_payload
 FOR EACH ROW EXECUTE FUNCTION app_private.expire_native_atomic_capture();
REVOKE ALL ON ml.native_atomic_capture_connection FROM PUBLIC;
CREATE TRIGGER z_native_atomic_snapshot_insert AFTER INSERT ON ml.recommendation_input_snapshot
 FOR EACH ROW EXECUTE FUNCTION app_private.witness_native_atomic_insert();
CREATE TRIGGER z_native_atomic_request_insert AFTER INSERT ON ml.recommendation_request
 FOR EACH ROW EXECUTE FUNCTION app_private.witness_native_atomic_insert();
CREATE TRIGGER z_native_atomic_bundle_insert AFTER INSERT ON ml.sona_capture_bundle
 FOR EACH ROW EXECUTE FUNCTION app_private.witness_native_atomic_insert();
CREATE CONSTRAINT TRIGGER z_native_atomic_context_complete AFTER INSERT ON
 app_private.native_atomic_capture_context DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.check_native_atomic_context();
REVOKE ALL ON app_private.native_atomic_capture_context,
 app_private.native_atomic_capture_insert_witness FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.arm_native_atomic_capture(uuid),
 app_private.witness_native_atomic_insert(),app_private.check_native_atomic_context() FROM PUBLIC;
REVOKE ALL ON FUNCTION
 app_private.commit_native_atomic_served(uuid,uuid,uuid,uuid,bigint,jsonb,uuid),
 app_private.commit_native_atomic_capture(uuid,uuid,uuid,uuid,uuid,bigint,jsonb),
 app_private.assert_native_atomic_capture(uuid,boolean),app_private.protect_native_atomic_capture(),
 app_private.check_native_atomic_capture(),app_private.expire_native_atomic_capture() FROM PUBLIC;
"""
