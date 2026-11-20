"""Private fresh impression attribution to committed v2 P11 truth.

No wire producer, runtime grant, public reader or serving cutover is installed.
The original-request digest is a trusted seam's idempotency input, not authority.
"""

from alembic import op
from sqlalchemy import text

revision = "0093_native_causal_attribution"
down_revision = "0092_native_served_authority"
branch_labels = None
depends_on = None


def _extend(name: str, anchor: str, addition: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name}
    )
    if not isinstance(definition, str) or definition.count(anchor) != 1:
        raise RuntimeError("native_attribution_prior_function_changed")
    op.get_bind().execute(
        text("INSERT INTO app_private.native_attribution_prior_definition VALUES(:name,:ddl)"),
        {"name": name, "ddl": definition},
    )
    op.execute(definition.replace(anchor, anchor + addition, 1))


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE library.user_interaction_event
 ADD COLUMN attribution_contract_version integer NOT NULL DEFAULT 1,
 ADD COLUMN attribution_closed boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT ck_interaction_attribution_version CHECK(attribution_contract_version IN (1,2)),
 ADD CONSTRAINT ck_interaction_attribution_closed CHECK(
  attribution_contract_version=2 OR NOT attribution_closed),
 ADD CONSTRAINT ck_interaction_attribution_v2 CHECK(attribution_contract_version=1 OR
  (event_type='RECOMMENDATION_IMPRESSION_RECORDED' AND recording_id IS NOT NULL
   AND presentation_id IS NOT NULL AND recommendation_request_id IS NULL
   AND source_rank IS NULL AND impression_interaction_id IS NULL
   AND payload='{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}'::jsonb)),
 ADD CONSTRAINT uq_interaction_owner_recording UNIQUE(user_id,interaction_id,recording_id);

CREATE TABLE ml.recommendation_attribution_association (
 association_id uuid PRIMARY KEY DEFAULT uuidv7(),
 owner_user_id uuid NOT NULL,
 recommendation_request_id uuid NOT NULL,
 kind text NOT NULL
  CONSTRAINT native_attr_kind_check CHECK(kind='IMPRESSION'),
 served_rank integer NOT NULL
  CONSTRAINT native_attr_served_rank_check CHECK(served_rank BETWEEN 1 AND 1000),
 recording_id uuid NOT NULL,
 serving_provenance text NOT NULL
  CONSTRAINT native_attr_serving_provenance_check CHECK(serving_provenance='P11'),
 request_surface text NOT NULL
  CONSTRAINT native_attr_request_surface_check
   CHECK(request_surface IN ('recommendations','home','offline_pack')),
 presentation_source text NOT NULL
  CONSTRAINT native_attr_presentation_source_check
   CHECK(presentation_source ~ '^[a-z][a-z0-9_]{0,99}$'),
 presentation_surface text NOT NULL
  CONSTRAINT native_attr_presentation_surface_check
   CHECK(presentation_surface ~ '^[a-z][a-z0-9_]{0,99}$'),
 wire_attribution_version integer NOT NULL
  CONSTRAINT native_attr_wire_attribution_version_check CHECK(wire_attribution_version=1),
 presentation_id uuid NOT NULL,
 interaction_id uuid NOT NULL UNIQUE,
 attribution_expires_at timestamptz NOT NULL,
 original_request_sha256 bytea NOT NULL
  CONSTRAINT native_attr_original_request_sha256_check
   CHECK(octet_length(original_request_sha256)=32),
 acceptance_sha256 bytea NOT NULL
  CONSTRAINT native_attr_acceptance_sha256_check CHECK(octet_length(acceptance_sha256)=32),
 decision_sha256 bytea NOT NULL
  CONSTRAINT native_attr_decision_sha256_check CHECK(octet_length(decision_sha256)=32),
 served_item_sha256 bytea NOT NULL
  CONSTRAINT native_attr_served_item_sha256_check CHECK(octet_length(served_item_sha256)=32),
 association_sha256 bytea NOT NULL
  CONSTRAINT native_attr_association_sha256_check CHECK(octet_length(association_sha256)=32),
 CONSTRAINT fk_attribution_owner_request FOREIGN KEY(owner_user_id,recommendation_request_id)
  REFERENCES ml.recommendation_request(user_id,recommendation_request_id) ON DELETE RESTRICT,
 CONSTRAINT fk_attribution_served_item
  FOREIGN KEY(recommendation_request_id,served_rank,recording_id)
  REFERENCES ml.recommendation_served_item(recommendation_request_id,source_rank,recording_id)
  ON DELETE RESTRICT,
 CONSTRAINT fk_attribution_owner_interaction FOREIGN KEY(owner_user_id,interaction_id,recording_id)
  REFERENCES library.user_interaction_event(user_id,interaction_id,recording_id)
  ON DELETE RESTRICT);
CREATE UNIQUE INDEX uq_attribution_impression_presentation
 ON ml.recommendation_attribution_association
 (owner_user_id,presentation_id,recommendation_request_id,served_rank) WHERE kind='IMPRESSION';
CREATE INDEX ix_attribution_request_expiry ON ml.recommendation_attribution_association
 (attribution_expires_at,recommendation_request_id,association_id);

-- No request/item FK: a minimal content-bound receipt must outlive their expiry.
CREATE TABLE ml.native_attribution_purge_receipt (
 association_id uuid PRIMARY KEY,
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 interaction_id uuid NOT NULL UNIQUE,
 source_server_sequence bigint NOT NULL
  CONSTRAINT native_attr_purge_source_server_sequence_check CHECK(source_server_sequence>0),
 original_request_sha256 bytea NOT NULL
  CONSTRAINT native_attr_purge_original_request_sha256_check
   CHECK(octet_length(original_request_sha256)=32),
 acceptance_sha256 bytea NOT NULL
  CONSTRAINT native_attr_purge_acceptance_sha256_check CHECK(octet_length(acceptance_sha256)=32),
 association_sha256 bytea NOT NULL
  CONSTRAINT native_attr_purge_association_sha256_check CHECK(octet_length(association_sha256)=32),
 target_sha256 bytea NOT NULL
  CONSTRAINT native_attr_purge_target_sha256_check CHECK(octet_length(target_sha256)=32),
 reason text NOT NULL
  CONSTRAINT native_attr_purge_reason_check CHECK(reason IN
  ('SERVING_TRUTH_EXPIRED','OWNER_EVENT_ERASURE','ACCOUNT_DELETION')),
 attribution_expires_at timestamptz NOT NULL,
 purged_at timestamptz NOT NULL,
 retain_until timestamptz NOT NULL,
 CONSTRAINT native_attr_purge_retain_until_check
  CHECK(retain_until=attribution_expires_at+interval '220 days'));
CREATE INDEX ix_native_attribution_receipt_expiry
 ON ml.native_attribution_purge_receipt(retain_until,association_id);

CREATE TABLE app_private.native_attribution_context (
 transaction_id xid8 NOT NULL,
 event_id uuid NOT NULL,
 owner_id uuid NOT NULL,
 request_id uuid,
 action text NOT NULL CHECK(action IN ('ACCEPT','PURGE','RECEIPT_PURGE')),
 content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
 PRIMARY KEY(transaction_id,event_id));
REVOKE ALL ON app_private.native_attribution_context FROM PUBLIC;
CREATE TABLE app_private.native_attribution_prior_definition(
 name text PRIMARY KEY,ddl text NOT NULL);
REVOKE ALL ON app_private.native_attribution_prior_definition FROM PUBLIC;
REVOKE ALL ON ml.recommendation_attribution_association,ml.native_attribution_purge_receipt
 FROM PUBLIC;

-- Legacy inline attribution cannot become an alternate edge to a v2 request.
-- FOR KEY SHARE also fails closed at older transaction snapshots: a missing
-- request is never presumed legacy while a concurrent v2 insertion commits.
CREATE FUNCTION app_private.guard_native_inline_request() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE contract integer;
BEGIN
 IF NEW.recommendation_request_id IS NOT NULL THEN
  SELECT serving_contract_version INTO contract FROM ml.recommendation_request
   WHERE recommendation_request_id=NEW.recommendation_request_id FOR KEY SHARE;
  IF contract IS DISTINCT FROM 1 THEN
   RAISE EXCEPTION 'NATIVE_INLINE_ATTRIBUTION_REFUSED' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_native_inline_listening BEFORE INSERT OR UPDATE OF recommendation_request_id
 ON library.listening_event FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_inline_request();
CREATE TRIGGER guard_native_inline_interaction BEFORE INSERT OR UPDATE OF recommendation_request_id
 ON library.user_interaction_event FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_inline_request();
CREATE TRIGGER guard_native_inline_temporal BEFORE INSERT OR UPDATE OF recommendation_request_id
 ON ml.recommendation_temporal_event FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_inline_request();
CREATE TRIGGER guard_native_inline_pack BEFORE INSERT OR UPDATE OF recommendation_request_id
 ON ml.offline_recommendation_pack FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_inline_request();

CREATE FUNCTION app_private.guard_native_request_legacy_collision() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.serving_contract_version=2 AND
  (EXISTS(SELECT 1 FROM library.listening_event
    WHERE recommendation_request_id=NEW.recommendation_request_id)
   OR EXISTS(SELECT 1 FROM library.user_interaction_event
    WHERE recommendation_request_id=NEW.recommendation_request_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_temporal_event
    WHERE recommendation_request_id=NEW.recommendation_request_id)
   OR EXISTS(SELECT 1 FROM ml.offline_recommendation_pack
    WHERE recommendation_request_id=NEW.recommendation_request_id)) THEN
  RAISE EXCEPTION 'NATIVE_INLINE_ATTRIBUTION_REFUSED' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_native_request_legacy_collision BEFORE INSERT OR UPDATE
 ON ml.recommendation_request FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_request_legacy_collision();

CREATE FUNCTION app_private.native_attribution_hash_v1(domain text,fields bytea[]) RETURNS bytea
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE encoded bytea; field bytea;
BEGIN
 encoded:=convert_to(domain,'UTF8')||decode('00','hex')||int4send(cardinality(fields));
 FOREACH field IN ARRAY fields LOOP
  encoded:=encoded||CASE WHEN field IS NULL THEN decode('00','hex')
   ELSE decode('01','hex')||int4send(octet_length(field))||field END;
 END LOOP;
 RETURN sha256(encoded);
END $$;

CREATE FUNCTION app_private.native_impression_command_hash(
 owner_id uuid,device uuid,epoch uuid,eid uuid,seq bigint,happened timestamptz,
 original_hash bytea,request_id uuid,rank integer,recording uuid,presentation uuid,
 source_label text,surface_label text,wire_version integer) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-impression-command.v1',ARRAY[
 uuid_send(owner_id),uuid_send(device),uuid_send(epoch),uuid_send(eid),int8send(seq),
 timestamptz_send(happened),original_hash,uuid_send(request_id),int8send(rank::bigint),
 uuid_send(recording),uuid_send(presentation),convert_to(source_label,'UTF8'),
 convert_to(surface_label,'UTF8'),int8send(wire_version::bigint)]) $$;

CREATE FUNCTION app_private.native_impression_target_hash(t library.user_interaction_event)
 RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-impression-target.v1',ARRAY[
 uuid_send(t.interaction_id),uuid_send(t.user_id),uuid_send(t.device_id),
 convert_to(t.event_type,'UTF8'),uuid_send(t.recording_id),uuid_send(t.presentation_id),
 timestamptz_send(t.created_at),int8send(t.attribution_contract_version::bigint),
 boolsend(t.attribution_closed)]) $$;

CREATE FUNCTION app_private.native_impression_association_hash(
 a ml.recommendation_attribution_association) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-impression-association.v1',ARRAY[
 uuid_send(a.association_id),uuid_send(a.owner_user_id),uuid_send(a.recommendation_request_id),
 convert_to(a.kind,'UTF8'),int8send(a.served_rank::bigint),uuid_send(a.recording_id),
 convert_to(a.serving_provenance,'UTF8'),convert_to(a.request_surface,'UTF8'),
 convert_to(a.presentation_source,'UTF8'),convert_to(a.presentation_surface,'UTF8'),
 int8send(a.wire_attribution_version::bigint),uuid_send(a.presentation_id),uuid_send(a.interaction_id),
 timestamptz_send(a.attribution_expires_at),a.original_request_sha256,a.acceptance_sha256,
 a.decision_sha256,a.served_item_sha256]) $$;

CREATE FUNCTION app_private.native_impression_ack(eid uuid,seq bigint) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('event_id',eid,'device_sequence',seq,
 'aggregate_type','USER_INTERACTION_EVENT','aggregate_local_id',eid,'aggregate_server_id',eid,
 'outcome','APPLIED','server_row_version',1) $$;

CREATE FUNCTION app_private.native_impression_erasure_allowed(owner_id uuid,eid uuid)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt r
  JOIN sync.sync_event s ON s.user_id=r.user_id AND s.event_id=r.source_event_id
   AND s.server_sequence=r.source_server_sequence
  WHERE r.user_id=owner_id AND r.source_event_id=eid AND s.ingress_validation_version=2
   AND s.event_type='RECOMMENDATION_IMPRESSION_RECORDED') $$;

CREATE FUNCTION app_private.assert_native_impression_v2(eid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s sync.sync_event%ROWTYPE; i sync.device_event_inbox%ROWTYPE;
 t library.user_interaction_event%ROWTYPE; a ml.recommendation_attribution_association%ROWTYPE;
 r ml.native_attribution_purge_receipt%ROWTYPE; er ml.sona_event_erasure_receipt%ROWTYPE;
 q ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
 item ml.recommendation_served_item%ROWTYPE;
BEGIN
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 SELECT * INTO i FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;
 SELECT * INTO t FROM library.user_interaction_event WHERE interaction_id=eid FOR UPDATE;
 SELECT * INTO a FROM ml.recommendation_attribution_association WHERE interaction_id=eid;
 SELECT * INTO r FROM ml.native_attribution_purge_receipt WHERE interaction_id=eid;
 IF i.event_id IS NULL AND s.event_id IS NULL AND t.interaction_id IS NULL
  AND a.association_id IS NULL THEN RETURN; END IF;
 IF i.ingress_validation_version IS DISTINCT FROM 2 AND s.ingress_validation_version
  IS DISTINCT FROM 2 AND t.attribution_contract_version IS DISTINCT FROM 2
  AND a.association_id IS NULL THEN RETURN; END IF;
 IF i.apply_status='RECEIVED' AND t.interaction_id IS NULL AND a.association_id IS NULL THEN
  RETURN;
 END IF;
 IF i.apply_status='ERASED' THEN
  SELECT * INTO er FROM ml.sona_event_erasure_receipt
   WHERE user_id=i.user_id AND source_event_id=eid;
  IF s.event_id IS NOT NULL OR t.interaction_id IS NOT NULL OR a.association_id IS NOT NULL
   OR er.source_event_id IS NULL OR i.event_type<>'RECOMMENDATION_IMPRESSION_RECORDED'
   OR i.terminal_ack IS DISTINCT FROM jsonb_build_object('event_id',eid,'outcome','EVENT_ERASED')
   OR i.error_code IS DISTINCT FROM 'EVENT_ERASED' OR i.payload<>'{}'::jsonb
   OR (r.association_id IS NOT NULL AND
    (r.owner_user_id IS DISTINCT FROM i.user_id OR r.original_request_sha256<>i.request_hash
     OR r.source_server_sequence<>er.source_server_sequence))
   OR (clock_timestamp()<er.erased_at+interval '400 days' AND NOT EXISTS(
    SELECT 1 FROM sync.tombstone ts JOIN sync.sync_event deletion
     ON deletion.user_id=ts.user_id AND deletion.event_id=ts.deleted_by_event_id
      AND deletion.aggregate_id=ts.aggregate_id
    WHERE ts.user_id=i.user_id AND ts.aggregate_id=eid
     AND ts.deleted_by_event_id=er.erasure_event_id
     AND ts.aggregate_type='USER_INTERACTION_EVENT' AND deletion.operation='DELETE'
     AND deletion.event_type='RECOMMENDATION_INTERACTION_ERASED' AND deletion.schema_version=2
     AND deletion.aggregate_type='USER_INTERACTION_EVENT' AND deletion.payload='{}'::jsonb
     AND ts.projection_event_type='RECOMMENDATION_INTERACTION_ERASED'
     AND ts.projection_schema_version=2 AND ts.deleted_at=er.erased_at
     AND ts.retain_until=er.erased_at+interval '400 days')) THEN
   RAISE EXCEPTION 'NATIVE_IMPRESSION_ERASURE_BINDING' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 IF i.event_id IS NULL OR s.event_id IS NULL OR t.interaction_id IS NULL
  OR i.ingress_validation_version IS DISTINCT FROM 2 OR s.ingress_validation_version<>2
  OR t.attribution_contract_version<>2 OR i.apply_status IS DISTINCT FROM 'APPLIED'
  OR i.error_code IS NOT NULL OR i.idempotency_key IS DISTINCT FROM 'native-impression-v2:'||eid
  OR i.user_id<>s.user_id OR t.user_id<>i.user_id OR t.device_id<>i.device_id
  OR s.origin_device_id<>i.device_id OR s.event_type<>i.event_type
  OR i.event_type<>'RECOMMENDATION_IMPRESSION_RECORDED' OR t.event_type<>i.event_type
  OR i.schema_version<>1 OR s.schema_version<>1 OR s.operation<>'UPSERT'
  OR s.server_row_version IS DISTINCT FROM 1 OR s.aggregate_id<>eid OR i.aggregate_id<>eid
  OR s.aggregate_type<>'USER_INTERACTION_EVENT' OR i.aggregate_type<>s.aggregate_type
  OR t.created_at<>i.occurred_at OR s.ingress_device_sequence<>i.device_sequence
  OR s.ingress_journal_epoch<>i.journal_epoch OR i.payload<>'{}'::jsonb
  OR t.recommendation_request_id IS NOT NULL OR t.source_rank IS NOT NULL
  OR t.impression_interaction_id IS NOT NULL OR t.recording_id IS NULL
  OR t.presentation_id IS NULL OR t.payload IS DISTINCT FROM s.payload
  OR s.payload IS DISTINCT FROM '{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}'::jsonb
  OR i.normalized_payload_sha256 IS DISTINCT FROM s.normalized_payload_sha256
  OR s.normalized_payload_sha256 IS DISTINCT FROM app_private.sync_payload_sha256_pg_v1(s.payload)
  OR i.terminal_ack IS DISTINCT FROM app_private.native_impression_ack(eid,i.device_sequence) THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_ACCEPTED_BINDING' USING ERRCODE='23514';
 END IF;
 IF a.association_id IS NULL THEN
  IF NOT t.attribution_closed THEN
   RAISE EXCEPTION 'NATIVE_IMPRESSION_ASSOCIATION_REQUIRED' USING ERRCODE='23514';
  END IF;
  -- The lifecycle-only noncausal closed marker survives the receipt horizon.
  IF r.association_id IS NULL THEN RETURN; END IF;
  IF r.reason<>'SERVING_TRUTH_EXPIRED'
   OR r.owner_user_id<>t.user_id OR r.source_server_sequence<>s.server_sequence
   OR r.original_request_sha256<>i.request_hash
   OR r.target_sha256<>app_private.native_impression_target_hash(t)
   OR r.purged_at<r.attribution_expires_at THEN
   RAISE EXCEPTION 'NATIVE_IMPRESSION_ASSOCIATION_REQUIRED' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 SELECT * INTO q FROM ml.recommendation_request
  WHERE recommendation_request_id=a.recommendation_request_id;
 PERFORM app_private.assert_native_served_authority(a.recommendation_request_id);
 SELECT * INTO d FROM ml.recommendation_serving_decision
  WHERE recommendation_request_id=a.recommendation_request_id;
 SELECT * INTO item FROM ml.recommendation_served_item
  WHERE recommendation_request_id=a.recommendation_request_id AND source_rank=a.served_rank;
 IF q.recommendation_request_id IS NULL OR q.serving_contract_version<>2 OR q.user_id<>t.user_id
  OR t.attribution_closed
  OR d.recommendation_request_id IS NULL OR d.state<>'COMMITTED_P11'
  OR item.recommendation_request_id IS NULL OR item.recording_id<>t.recording_id
  OR a.owner_user_id<>t.user_id OR a.recording_id<>t.recording_id
  OR a.presentation_id<>t.presentation_id
  OR a.serving_provenance<>item.attribution_source OR a.serving_provenance<>'P11'
  OR a.request_surface IS DISTINCT FROM q.surface OR a.decision_sha256<>d.decision_sha256
  OR a.served_item_sha256<>item.served_item_sha256 OR a.original_request_sha256<>i.request_hash
  OR a.attribution_expires_at<>q.created_at+interval '180 days'
  OR a.attribution_expires_at<>d.served_truth_expires_at
  OR t.created_at<q.created_at OR t.created_at>=a.attribution_expires_at
  OR a.association_sha256<>app_private.native_impression_association_hash(a)
  OR a.acceptance_sha256<>app_private.native_impression_command_hash(t.user_id,t.device_id,
   i.journal_epoch,eid,i.device_sequence,i.occurred_at,i.request_hash,a.recommendation_request_id,
   a.served_rank,a.recording_id,a.presentation_id,a.presentation_source,a.presentation_surface,
   a.wire_attribution_version) THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_CAUSAL_BINDING' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION app_private.check_native_impression_v2() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE eid uuid;
BEGIN
 eid:=COALESCE((to_jsonb(NEW)->>'interaction_id')::uuid,(to_jsonb(OLD)->>'interaction_id')::uuid);
 PERFORM app_private.assert_native_impression_v2(eid);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_native_impression_target AFTER INSERT OR UPDATE OR DELETE
 ON library.user_interaction_event DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_native_impression_v2();
CREATE CONSTRAINT TRIGGER check_native_impression_association AFTER INSERT OR UPDATE OR DELETE
 ON ml.recommendation_attribution_association DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_native_impression_v2();

CREATE FUNCTION app_private.guard_native_attribution() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_IMMUTABLE' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NOT EXISTS(SELECT 1 FROM app_private.native_attribution_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.interaction_id
    AND c.owner_id=NEW.owner_user_id AND c.request_id=NEW.recommendation_request_id
    AND c.action='ACCEPT' AND c.content_sha256=NEW.acceptance_sha256) THEN
   RAISE EXCEPTION 'NATIVE_ATTRIBUTION_WRITER_REQUIRED' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM app_private.native_attribution_context c
  JOIN ml.native_attribution_purge_receipt r ON r.interaction_id=c.event_id
  WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=OLD.interaction_id
   AND c.owner_id=OLD.owner_user_id AND c.request_id=OLD.recommendation_request_id
   AND c.action='PURGE' AND c.content_sha256=OLD.association_sha256
   AND r.association_sha256=OLD.association_sha256 AND r.association_id=OLD.association_id) THEN
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_PURGE_REQUIRED' USING ERRCODE='55000';
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER guard_native_attribution BEFORE INSERT OR UPDATE OR DELETE
 ON ml.recommendation_attribution_association FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_attribution();

CREATE FUNCTION app_private.guard_native_attribution_receipt() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM app_private.native_attribution_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.interaction_id
   AND c.owner_id=NEW.owner_user_id AND c.action='PURGE'
   AND c.content_sha256=NEW.association_sha256) THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' AND (app_private.privacy_owner_allowed(OLD.owner_user_id)
  OR NOT EXISTS(SELECT 1 FROM account.user_account WHERE user_id=OLD.owner_user_id)
  OR (clock_timestamp()>=OLD.retain_until AND EXISTS(
   SELECT 1 FROM app_private.native_attribution_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=OLD.interaction_id
    AND c.owner_id=OLD.owner_user_id AND c.action='RECEIPT_PURGE'
    AND c.content_sha256=OLD.association_sha256))) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'NATIVE_ATTRIBUTION_RECEIPT_IMMUTABLE' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_native_attribution_receipt BEFORE INSERT OR UPDATE OR DELETE
 ON ml.native_attribution_purge_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_attribution_receipt();

CREATE FUNCTION app_private.lock_native_attribution_owner(owner_id uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM account.user_account WHERE user_id=owner_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'ATTRIBUTION_NOT_FOUND' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(owner_id)),
  'hex'),1,16))::bit(64)::bigint);
END $$;

CREATE FUNCTION app_private.purge_recommendation_attribution_v2(
 owner_id uuid,eid uuid,p_reason text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a ml.recommendation_attribution_association%ROWTYPE;
 t library.user_interaction_event%ROWTYPE; s sync.sync_event%ROWTYPE; actual_time timestamptz;
BEGIN
 IF p_reason IS NULL OR p_reason NOT IN
  ('SERVING_TRUTH_EXPIRED','OWNER_EVENT_ERASURE','ACCOUNT_DELETION') THEN
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_PURGE_REASON' USING ERRCODE='22023';
 END IF;
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 SELECT * INTO a FROM ml.recommendation_attribution_association
  WHERE owner_user_id=owner_id AND interaction_id=eid;
 IF a.association_id IS NULL THEN RETURN false; END IF;
 PERFORM app_private.lock_recommendation_request_v2(a.recommendation_request_id);
 SELECT * INTO a FROM ml.recommendation_attribution_association
  WHERE owner_user_id=owner_id AND interaction_id=eid FOR UPDATE;
 IF a.association_id IS NULL THEN RETURN false; END IF;
 SELECT * INTO t FROM library.user_interaction_event WHERE interaction_id=eid FOR UPDATE;
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 PERFORM app_private.assert_native_impression_v2(eid);
 actual_time:=clock_timestamp();
 IF (p_reason='SERVING_TRUTH_EXPIRED' AND actual_time<a.attribution_expires_at)
  OR (p_reason='OWNER_EVENT_ERASURE' AND
   NOT app_private.native_impression_erasure_allowed(owner_id,eid))
  OR (p_reason='ACCOUNT_DELETION' AND NOT app_private.privacy_owner_allowed(owner_id)) THEN
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_PURGE_AUTHORITY' USING ERRCODE='55000';
 END IF;
 INSERT INTO app_private.native_attribution_context
 VALUES(pg_current_xact_id(),eid,owner_id,a.recommendation_request_id,'PURGE',a.association_sha256);
 IF p_reason='SERVING_TRUTH_EXPIRED' THEN t.attribution_closed:=true; END IF;
 INSERT INTO ml.native_attribution_purge_receipt(association_id,owner_user_id,interaction_id,
  source_server_sequence,original_request_sha256,acceptance_sha256,
  association_sha256,target_sha256,reason,attribution_expires_at,purged_at,retain_until)
 VALUES(a.association_id,owner_id,eid,s.server_sequence,
  a.original_request_sha256,a.acceptance_sha256,a.association_sha256,
  app_private.native_impression_target_hash(t),p_reason,a.attribution_expires_at,actual_time,
  a.attribution_expires_at+interval '220 days');
 IF p_reason='SERVING_TRUTH_EXPIRED' THEN
  UPDATE library.user_interaction_event SET attribution_closed=true WHERE interaction_id=eid;
 END IF;
 DELETE FROM ml.recommendation_attribution_association WHERE association_id=a.association_id;
 DELETE FROM app_private.native_attribution_context
  WHERE transaction_id=pg_current_xact_id() AND event_id=eid;
 RETURN true;
END $$;

CREATE FUNCTION app_private.purge_native_impression_v2(owner_id uuid,eid uuid,p_reason text)
 RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT app_private.purge_recommendation_attribution_v2(owner_id,eid,p_reason) $$;

CREATE FUNCTION app_private.purge_native_request_attributions_v2(
 owner_id uuid,request_id uuid,p_reason text,p_limit integer DEFAULT 256) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE eid uuid; removed integer:=0;
BEGIN
 IF owner_id IS NULL OR request_id IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 256 THEN
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_PURGE_BOUND' USING ERRCODE='22023';
 END IF;
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 PERFORM app_private.lock_recommendation_request_v2(request_id);
 FOR eid IN SELECT interaction_id FROM ml.recommendation_attribution_association
  WHERE owner_user_id=owner_id AND recommendation_request_id=request_id
  ORDER BY association_id LIMIT p_limit LOOP
  IF app_private.purge_native_impression_v2(owner_id,eid,p_reason) THEN removed:=removed+1; END IF;
 END LOOP;
 RETURN removed;
END $$;

CREATE FUNCTION app_private.purge_native_attribution_receipts_v2(
 owner_id uuid,p_limit integer DEFAULT 256)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.native_attribution_purge_receipt%ROWTYPE; removed integer:=0;
BEGIN
 IF owner_id IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 256 THEN
  RAISE EXCEPTION 'NATIVE_ATTRIBUTION_PURGE_BOUND' USING ERRCODE='22023';
 END IF;
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 FOR r IN SELECT * FROM ml.native_attribution_purge_receipt WHERE owner_user_id=owner_id
  AND retain_until<=clock_timestamp() ORDER BY retain_until,association_id
  LIMIT p_limit FOR UPDATE SKIP LOCKED LOOP
  INSERT INTO app_private.native_attribution_context
  VALUES(pg_current_xact_id(),r.interaction_id,owner_id,NULL,
   'RECEIPT_PURGE',r.association_sha256);
  DELETE FROM ml.native_attribution_purge_receipt WHERE association_id=r.association_id;
  DELETE FROM app_private.native_attribution_context
   WHERE transaction_id=pg_current_xact_id() AND event_id=r.interaction_id;
  removed:=removed+1;
 END LOOP;
 RETURN removed;
END $$;

CREATE FUNCTION app_private.guard_native_impression_target() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.attribution_contract_version=2 AND (NEW.attribution_closed OR NOT EXISTS(
   SELECT 1 FROM app_private.native_attribution_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.interaction_id
    AND c.owner_id=NEW.user_id AND c.action='ACCEPT')) THEN
   RAISE EXCEPTION 'NATIVE_IMPRESSION_WRITER_REQUIRED' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.attribution_contract_version=2 AND NOT OLD.attribution_closed AND NEW.attribution_closed
   AND (to_jsonb(OLD)-'attribution_closed')=(to_jsonb(NEW)-'attribution_closed')
   AND EXISTS(SELECT 1 FROM app_private.native_attribution_context c
    JOIN ml.recommendation_attribution_association a ON a.interaction_id=c.event_id
    WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=OLD.interaction_id
     AND c.owner_id=OLD.user_id AND c.action='PURGE'
     AND c.content_sha256=a.association_sha256) THEN RETURN NEW; END IF;
  IF NEW.attribution_contract_version IS DISTINCT FROM OLD.attribution_contract_version
   OR (OLD.attribution_contract_version=2 AND NEW IS DISTINCT FROM OLD) THEN
   RAISE EXCEPTION 'NATIVE_IMPRESSION_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF OLD.attribution_contract_version=2 THEN
  IF app_private.privacy_owner_allowed(OLD.user_id) THEN
   PERFORM app_private.purge_native_impression_v2(
    OLD.user_id,OLD.interaction_id,'ACCOUNT_DELETION');
  ELSIF app_private.native_impression_erasure_allowed(OLD.user_id,OLD.interaction_id) THEN
   PERFORM app_private.purge_native_impression_v2(
    OLD.user_id,OLD.interaction_id,'OWNER_EVENT_ERASURE');
  ELSE
   RAISE EXCEPTION 'NATIVE_IMPRESSION_ERASURE_AUTHORITY' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER guard_native_impression_target BEFORE INSERT OR UPDATE OR DELETE
 ON library.user_interaction_event FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_impression_target();

CREATE FUNCTION app_private.accept_native_impression_v2(
 p_owner uuid,p_device uuid,p_epoch uuid,p_event uuid,p_sequence bigint,p_occurred timestamptz,
 p_original_hash bytea,p_request uuid,p_rank integer,p_recording uuid,p_presentation uuid,
 p_source text,p_surface text,p_wire_version integer DEFAULT 1) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
 item ml.recommendation_served_item%ROWTYPE; a ml.recommendation_attribution_association%ROWTYPE;
 prior sync.device_event_inbox%ROWTYPE; cursor_row sync.device_sync_cursor%ROWTYPE;
 floor_row sync.device_erasure_floor%ROWTYPE; owner_floor sync.owner_erasure_floor%ROWTYPE;
 binding bytea; clock_value timestamptz; payload jsonb;
BEGIN
 IF p_owner IS NULL OR p_device IS NULL OR p_epoch IS NULL OR p_event IS NULL
  OR p_request IS NULL OR p_recording IS NULL OR p_presentation IS NULL
  OR p_sequence IS NULL OR p_sequence NOT BETWEEN 1 AND 9007199254740991
  OR p_rank IS NULL OR p_rank NOT BETWEEN 1 AND 1000
  OR p_occurred IS NULL OR NOT isfinite(p_occurred)
  OR p_original_hash IS NULL OR octet_length(p_original_hash)<>32
  OR p_source IS NULL OR p_source !~ '^[a-z][a-z0-9_]{0,99}$'
  OR p_surface IS NULL OR p_surface !~ '^[a-z][a-z0-9_]{0,99}$'
  OR p_wire_version IS DISTINCT FROM 1 THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_INPUT_BOUND' USING ERRCODE='22023';
 END IF;
 -- Expiry RPC and privacy deletion take this server barrier before owner locks.
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner AND status='ACTIVE'
  AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'ATTRIBUTION_NOT_FOUND' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM account.device WHERE device_id=p_device AND user_id=p_owner
  AND revoked_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'ATTRIBUTION_NOT_FOUND' USING ERRCODE='23514'; END IF;
 PERFORM app_private.lock_native_attribution_owner(p_owner);
 SELECT * INTO cursor_row FROM sync.device_sync_cursor
  WHERE user_id=p_owner AND device_id=p_device AND journal_epoch=p_epoch FOR UPDATE;
 IF cursor_row.device_id IS NULL OR EXISTS(SELECT 1 FROM sync.device_erasure_invalidated_epoch
  WHERE device_id=p_device AND journal_epoch=p_epoch) THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_DEVICE_FENCE' USING ERRCODE='23514';
 END IF;
 SELECT * INTO floor_row FROM sync.device_erasure_floor WHERE device_id=p_device;
 SELECT * INTO owner_floor FROM sync.owner_erasure_floor WHERE user_id=p_owner;
 IF (floor_row.device_id IS NOT NULL AND NOT floor_row.bootstrap_completed)
  OR (owner_floor.user_id IS NOT NULL AND
   ((floor_row.device_id IS NULL
     AND cursor_row.last_pulled_server_sequence<owner_floor.floor_sequence)
    OR (floor_row.device_id IS NOT NULL AND
     (floor_row.floor_sequence<owner_floor.floor_sequence
      OR floor_row.ledger_sequence IS DISTINCT FROM owner_floor.ledger_sequence
      OR floor_row.ledger_digest IS DISTINCT FROM owner_floor.ledger_digest)))) THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_ERASURE_WIPE_REQUIRED' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-recommendation-impression-v2','UTF8')||uuid_send(p_owner)
   ||uuid_send(p_presentation)||uuid_send(p_request)||int8send(p_rank::bigint)),
   'hex'),1,16))::bit(64)::bigint);
 binding:=app_private.native_impression_command_hash(p_owner,p_device,p_epoch,p_event,p_sequence,
  p_occurred,p_original_hash,p_request,p_rank,p_recording,p_presentation,p_source,p_surface,p_wire_version);
 SELECT * INTO prior FROM sync.device_event_inbox WHERE event_id=p_event FOR UPDATE;
 IF prior.event_id IS NOT NULL THEN
  IF prior.user_id<>p_owner OR prior.device_id<>p_device OR prior.journal_epoch<>p_epoch
   OR prior.device_sequence<>p_sequence OR prior.request_hash<>p_original_hash
   OR prior.ingress_validation_version<>2 OR prior.event_type<>'RECOMMENDATION_IMPRESSION_RECORDED'
   OR prior.occurred_at<>p_occurred OR prior.apply_status NOT IN ('APPLIED','ERASED') THEN
   RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM ml.native_attribution_purge_receipt
   WHERE owner_user_id=p_owner AND interaction_id=p_event AND acceptance_sha256<>binding)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association
    WHERE owner_user_id=p_owner AND interaction_id=p_event AND acceptance_sha256<>binding) THEN
   RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514';
  END IF;
  IF prior.apply_status='ERASED' THEN
   PERFORM app_private.assert_native_impression_v2(p_event);
   RETURN jsonb_build_object('event_id',p_event,'outcome','EVENT_ERASED');
  END IF;
 END IF;
 SELECT * INTO q FROM ml.recommendation_request
  WHERE recommendation_request_id=p_request AND user_id=p_owner FOR SHARE;
 IF q.recommendation_request_id IS NULL THEN
  -- Commit the owner-only terminal disposition, including a retained expiry tombstone.
  RETURN app_private.expire_native_served_authority(p_owner,p_request)
   ||jsonb_build_object('event_id',p_event);
 END IF;
 IF q.serving_contract_version<>2 THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_LEGACY_REFUSED' USING ERRCODE='23514';
 END IF;
 SELECT * INTO d FROM ml.recommendation_serving_decision
  WHERE recommendation_request_id=p_request FOR SHARE;
 clock_value:=clock_timestamp();
 IF d.recommendation_request_id IS NULL OR d.state<>'COMMITTED_P11'
  OR d.served_truth_expires_at IS DISTINCT FROM q.created_at+interval '180 days' THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_SERVING_AUTHORITY' USING ERRCODE='23514';
 END IF;
 IF clock_value>=d.served_truth_expires_at THEN
  RETURN app_private.expire_native_served_authority(p_owner,p_request)
   ||jsonb_build_object('event_id',p_event);
 END IF;
 IF p_occurred<q.created_at OR p_occurred>clock_value OR p_occurred>=d.served_truth_expires_at THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_TIME_BINDING' USING ERRCODE='23514';
 END IF;
 PERFORM app_private.assert_native_served_authority(p_request);
 SELECT * INTO item FROM ml.recommendation_served_item
  WHERE recommendation_request_id=p_request AND source_rank=p_rank FOR SHARE;
 IF item.recommendation_request_id IS NULL OR item.recording_id<>p_recording
  OR item.attribution_source<>'P11' OR item.origin<>'P11' THEN
  RAISE EXCEPTION 'ATTRIBUTION_MISMATCH' USING ERRCODE='23514';
 END IF;
 SELECT * INTO a FROM ml.recommendation_attribution_association
  WHERE owner_user_id=p_owner AND presentation_id=p_presentation
   AND recommendation_request_id=p_request AND served_rank=p_rank FOR UPDATE;
 IF a.association_id IS NOT NULL THEN
  IF a.acceptance_sha256<>binding THEN
   RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514';
  END IF;
  PERFORM app_private.assert_native_impression_v2(a.interaction_id);
  IF clock_timestamp()>=a.attribution_expires_at THEN
   RETURN app_private.expire_native_served_authority(p_owner,p_request)
    ||jsonb_build_object('event_id',p_event);
  END IF;
  RETURN jsonb_build_object('event_id',a.interaction_id,
   'association_id',a.association_id,'outcome','APPLIED');
 END IF;
 IF prior.event_id IS NOT NULL OR EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt
  WHERE user_id=p_owner AND source_event_id=p_event) OR EXISTS(SELECT 1 FROM sync.sync_event
  WHERE event_id=p_event) OR EXISTS(SELECT 1 FROM library.user_interaction_event
  WHERE interaction_id=p_event) THEN
  RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514';
 END IF;
 IF p_sequence<>cursor_row.last_acked_device_sequence+1 OR EXISTS(
  SELECT 1 FROM sync.device_event_inbox WHERE device_id=p_device AND journal_epoch=p_epoch
   AND (device_sequence=p_sequence OR idempotency_key='native-impression-v2:'||p_event))
  OR EXISTS(SELECT 1 FROM sync.idempotency_record
   WHERE scope='sync-event:'||p_owner||':'||p_device||':'||p_epoch
    AND idempotency_key='native-impression-v2:'||p_event) THEN
  RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514';
 END IF;
 BEGIN
 payload:='{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}'::jsonb;
 PERFORM app_private.stage_normalized_sync_ingress_v2(p_event,p_owner,p_device,p_epoch,p_sequence,
  'RECOMMENDATION_IMPRESSION_RECORDED',1,'USER_INTERACTION_EVENT',p_event,p_occurred,p_original_hash,payload,2);
 INSERT INTO app_private.native_attribution_context
 VALUES(pg_current_xact_id(),p_event,p_owner,p_request,'ACCEPT',binding);
 INSERT INTO library.user_interaction_event(interaction_id,user_id,device_id,event_type,
  recording_id,presentation_id,payload,created_at,attribution_contract_version)
 VALUES(p_event,p_owner,p_device,'RECOMMENDATION_IMPRESSION_RECORDED',p_recording,
  p_presentation,payload,p_occurred,2);
 PERFORM 1 FROM library.user_interaction_event WHERE interaction_id=p_event FOR UPDATE;
 a.association_id:=uuidv7(); a.owner_user_id:=p_owner; a.recommendation_request_id:=p_request;
 a.kind:='IMPRESSION'; a.served_rank:=p_rank; a.recording_id:=p_recording;
 a.serving_provenance:=item.attribution_source; a.request_surface:=q.surface;
 a.presentation_source:=p_source; a.presentation_surface:=p_surface;
 a.wire_attribution_version:=p_wire_version; a.presentation_id:=p_presentation;
 a.interaction_id:=p_event; a.attribution_expires_at:=d.served_truth_expires_at;
 a.original_request_sha256:=p_original_hash; a.acceptance_sha256:=binding;
 a.decision_sha256:=d.decision_sha256; a.served_item_sha256:=item.served_item_sha256;
 a.association_sha256:=app_private.native_impression_association_hash(a);
 -- This database-clock check under the common request lock is the linearization point.
 IF clock_timestamp()>=a.attribution_expires_at THEN
  RAISE EXCEPTION 'NATIVE_IMPRESSION_LINEARIZATION_EXPIRED' USING ERRCODE='P1903';
 END IF;
 INSERT INTO ml.recommendation_attribution_association SELECT a.*;
 INSERT INTO app_private.normalized_ingress_write_context VALUES(pg_current_xact_id(),p_event);
 UPDATE sync.device_event_inbox SET apply_status='APPLIED',
  terminal_ack=app_private.native_impression_ack(p_event,p_sequence),
  idempotency_key='native-impression-v2:'||p_event WHERE event_id=p_event;
 DELETE FROM app_private.normalized_ingress_write_context
  WHERE transaction_id=pg_current_xact_id() AND event_id=p_event;
 DELETE FROM app_private.native_attribution_context
  WHERE transaction_id=pg_current_xact_id() AND event_id=p_event;
 UPDATE sync.device_sync_cursor SET last_acked_device_sequence=p_sequence WHERE device_id=p_device;
 PERFORM app_private.assert_native_impression_v2(p_event);
 RETURN jsonb_build_object('event_id',p_event,
  'association_id',a.association_id,'outcome','APPLIED');
 EXCEPTION WHEN SQLSTATE 'P1903' THEN
  -- Undo only staged event/target/context work, then retain the terminal tombstone.
  -- The owner and request locks were acquired outside this subtransaction.
  RETURN app_private.expire_native_served_authority(p_owner,p_request)
   ||jsonb_build_object('event_id',p_event);
 END;
END $$;
""")
    _extend(
        "app_private.assert_normalized_listen_acceptance(uuid)",
        "BEGIN\n",
        """ -- 0093: validate only the exact impression branch; keep every listening guard.
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE event_id=eid
  AND ingress_validation_version=2 AND event_type='RECOMMENDATION_IMPRESSION_RECORDED')
  OR EXISTS(SELECT 1 FROM library.user_interaction_event WHERE interaction_id=eid
   AND attribution_contract_version=2)
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association WHERE interaction_id=eid) THEN
  PERFORM app_private.assert_native_impression_v2(eid);
  RETURN;
 END IF;
""",
    )
    _extend(
        "app_private.guard_normalized_sync_ingress()",
        "BEGIN\n",
        """ -- 0093: exact typed impression acceptance, never a fake listening ACK.
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='device_event_inbox' THEN
 IF OLD.ingress_validation_version=2 AND NEW.ingress_validation_version=2
  AND OLD.event_type='RECOMMENDATION_IMPRESSION_RECORDED'
  AND (to_jsonb(NEW)-ARRAY['apply_status','terminal_ack','idempotency_key'])
   = (to_jsonb(OLD)-ARRAY['apply_status','terminal_ack','idempotency_key'])
  AND OLD.apply_status='RECEIVED' AND NEW.apply_status='APPLIED'
  AND NEW.idempotency_key='native-impression-v2:'||NEW.event_id
  AND NEW.terminal_ack=app_private.native_impression_ack(NEW.event_id,NEW.device_sequence)
  AND EXISTS(SELECT 1 FROM app_private.native_attribution_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id
    AND c.owner_id=NEW.user_id AND c.action='ACCEPT')
  AND EXISTS(SELECT 1 FROM app_private.normalized_ingress_write_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id) THEN RETURN NEW; END IF;
 END IF;
""",
    )
    op.execute("""
REVOKE ALL ON FUNCTION
 app_private.guard_native_inline_request(),app_private.guard_native_request_legacy_collision(),
 app_private.native_attribution_hash_v1(text,bytea[]),
 app_private.native_impression_command_hash(uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,integer),
 app_private.native_impression_target_hash(library.user_interaction_event),
 app_private.native_impression_association_hash(ml.recommendation_attribution_association),
 app_private.native_impression_ack(uuid,bigint),app_private.native_impression_erasure_allowed(uuid,uuid),
 app_private.assert_native_impression_v2(uuid),app_private.check_native_impression_v2(),
 app_private.guard_native_attribution(),app_private.guard_native_attribution_receipt(),
 app_private.lock_native_attribution_owner(uuid),
 app_private.purge_recommendation_attribution_v2(uuid,uuid,text),
 app_private.purge_native_impression_v2(uuid,uuid,text),
 app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer),
 app_private.purge_native_attribution_receipts_v2(uuid,integer),
 app_private.guard_native_impression_target(),
 app_private.accept_native_impression_v2(uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,integer)
 FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
-- Serialize the emptiness decision with every in-flight graph writer before
-- removing guards or restoring the normalized ingress functions. A later DDL
-- lock cannot protect evidence committed after an earlier empty snapshot.
LOCK TABLE ml.recommendation_request,ml.recommendation_serving_decision,
 ml.recommendation_served_item,library.user_interaction_event,
 ml.recommendation_attribution_association,ml.native_attribution_purge_receipt,
 sync.sync_event,sync.device_event_inbox,app_private.native_attribution_context,
 library.listening_event,ml.recommendation_temporal_event,ml.offline_recommendation_pack
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM library.user_interaction_event WHERE attribution_contract_version=2)
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association)
  OR EXISTS(SELECT 1 FROM ml.native_attribution_purge_receipt)
  OR EXISTS(SELECT 1 FROM app_private.native_attribution_context)
  OR EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE ingress_validation_version=2
   AND event_type='RECOMMENDATION_IMPRESSION_RECORDED' AND apply_status<>'RECEIVED') THEN
  RAISE EXCEPTION 'refusing native attribution downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
""")
    for name in (
        "app_private.assert_normalized_listen_acceptance(uuid)",
        "app_private.guard_normalized_sync_ingress()",
    ):
        definition = op.get_bind().scalar(
            text(
                "SELECT ddl FROM app_private.native_attribution_prior_definition WHERE name=:name"
            ),
            {"name": name},
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_attribution_prior_function_missing")
        op.execute(definition)
    op.execute("""
DROP TRIGGER guard_native_impression_target ON library.user_interaction_event;
DROP TRIGGER guard_native_inline_listening ON library.listening_event;
DROP TRIGGER guard_native_inline_interaction ON library.user_interaction_event;
DROP TRIGGER guard_native_inline_temporal ON ml.recommendation_temporal_event;
DROP TRIGGER guard_native_inline_pack ON ml.offline_recommendation_pack;
DROP TRIGGER guard_native_request_legacy_collision ON ml.recommendation_request;
DROP TRIGGER check_native_impression_target ON library.user_interaction_event;
DROP TRIGGER check_native_impression_association ON ml.recommendation_attribution_association;
DROP TRIGGER guard_native_attribution ON ml.recommendation_attribution_association;
DROP TRIGGER guard_native_attribution_receipt ON ml.native_attribution_purge_receipt;
DROP FUNCTION app_private.accept_native_impression_v2(
 uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,integer);
DROP FUNCTION app_private.guard_native_impression_target();
DROP FUNCTION app_private.guard_native_inline_request();
DROP FUNCTION app_private.guard_native_request_legacy_collision();
DROP FUNCTION app_private.purge_native_attribution_receipts_v2(uuid,integer);
DROP FUNCTION app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer);
DROP FUNCTION app_private.purge_native_impression_v2(uuid,uuid,text);
DROP FUNCTION app_private.purge_recommendation_attribution_v2(uuid,uuid,text);
DROP FUNCTION app_private.lock_native_attribution_owner(uuid);
DROP FUNCTION app_private.guard_native_attribution_receipt();
DROP FUNCTION app_private.guard_native_attribution();
DROP FUNCTION app_private.check_native_impression_v2();
DROP FUNCTION app_private.assert_native_impression_v2(uuid);
DROP FUNCTION app_private.native_impression_erasure_allowed(uuid,uuid);
DROP FUNCTION app_private.native_impression_ack(uuid,bigint);
DROP FUNCTION app_private.native_impression_association_hash(
 ml.recommendation_attribution_association);
DROP FUNCTION app_private.native_impression_target_hash(library.user_interaction_event);
DROP FUNCTION app_private.native_impression_command_hash(
 uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,integer);
DROP FUNCTION app_private.native_attribution_hash_v1(text,bytea[]);
DROP TABLE app_private.native_attribution_context;
DROP TABLE app_private.native_attribution_prior_definition;
DROP TABLE ml.native_attribution_purge_receipt;
DROP TABLE ml.recommendation_attribution_association;
ALTER TABLE library.user_interaction_event DROP CONSTRAINT uq_interaction_owner_recording,
 DROP CONSTRAINT ck_interaction_attribution_v2,DROP CONSTRAINT ck_interaction_attribution_version,
 DROP CONSTRAINT ck_interaction_attribution_closed,
 DROP COLUMN attribution_contract_version,DROP COLUMN attribution_closed;
""")
