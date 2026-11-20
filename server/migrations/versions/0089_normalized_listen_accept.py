"""Dormant noncausal listening acceptance; no role grant or production cutover."""

from alembic import op
from sqlalchemy import text

revision = "0089_normalized_listen_accept"
down_revision = "0088_ingest_offline_cleanup"
branch_labels = None
depends_on = None

_GUARD = """
 -- 0089: exact acceptance/erasure exceptions, before the immutable 0087 fence.
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='device_event_inbox'
  AND OLD.ingress_validation_version=2 AND NEW.ingress_validation_version=2
  AND (to_jsonb(NEW)-ARRAY['apply_status','terminal_ack','error_code','idempotency_key'])
   = (to_jsonb(OLD)-ARRAY['apply_status','terminal_ack','error_code','idempotency_key']) THEN
  IF OLD.apply_status='RECEIVED' AND NEW.apply_status='APPLIED'
   AND NEW.error_code IS NULL AND NEW.idempotency_key IS NOT NULL
   AND NEW.terminal_ack=app_private.normalized_listen_ack(NEW.event_id,NEW.device_sequence)
   AND EXISTS(SELECT 1 FROM app_private.normalized_ingress_write_context c
    WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id) THEN
   RETURN NEW;
  END IF;
  IF OLD.apply_status='APPLIED' AND NEW.apply_status='ERASED'
   AND NEW.idempotency_key IS NOT DISTINCT FROM OLD.idempotency_key
   AND NEW.error_code='EVENT_ERASED'
   AND NEW.terminal_ack=jsonb_build_object('event_id',NEW.event_id,'outcome','EVENT_ERASED')
   AND app_private.normalized_listen_erasure_allowed(OLD.user_id,OLD.event_id) THEN
   RETURN NEW;
  END IF;
 END IF;
 IF TG_OP='DELETE' AND TG_TABLE_NAME='sync_event' AND OLD.ingress_validation_version=2
  AND app_private.normalized_listen_erasure_allowed(OLD.user_id,OLD.event_id) THEN
  RETURN OLD;
 END IF;
"""

_PAIR = """
 -- 0089: an erased receipt survives source removal, never accepted eligibility.
 IF s.event_id IS NULL AND i.ingress_validation_version=2 AND i.apply_status='ERASED' THEN
  PERFORM app_private.assert_normalized_listen_acceptance(eid);
  RETURN NULL;
 END IF;
"""


def _extend(name: str, anchor: str, addition: str, *, remove: bool = False) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name}
    )
    if not isinstance(definition, str):
        raise RuntimeError("normalized_acceptance_prior_function_missing")
    if remove:
        if definition.count(addition) != 1:
            raise RuntimeError("normalized_acceptance_prior_function_changed")
        definition = definition.replace(addition, "", 1)
    else:
        if definition.count(anchor) != 1:
            raise RuntimeError("normalized_acceptance_prior_function_changed")
        definition = definition.replace(anchor, anchor + addition, 1)
    op.execute(definition)


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE sync.device_event_inbox DROP CONSTRAINT ck_inbox_normalized_ingress;
ALTER TABLE sync.device_event_inbox ADD CONSTRAINT ck_inbox_normalized_ingress CHECK(
 (ingress_validation_version=1 AND normalized_payload_sha256 IS NULL) OR
 (ingress_validation_version=2 AND normalized_payload_sha256 IS NOT NULL
  AND octet_length(normalized_payload_sha256)=32 AND payload='{}'::jsonb AND schema_version=1
  AND device_sequence BETWEEN 1 AND 9007199254740991
  AND ((apply_status='RECEIVED' AND terminal_ack IS NULL AND error_code IS NULL)
   OR (apply_status='APPLIED' AND terminal_ack IS NOT NULL AND error_code IS NULL)
   OR (apply_status='ERASED' AND terminal_ack IS NOT NULL AND error_code='EVENT_ERASED'))));

CREATE FUNCTION app_private.normalized_listen_ack(eid uuid, seq bigint) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('event_id',eid,'device_sequence',seq,'aggregate_type','LISTENING_EVENT',
 'aggregate_local_id',eid,'aggregate_server_id',eid,'outcome','APPLIED','server_row_version',1) $$;

CREATE FUNCTION app_private.normalized_listen_erasure_allowed(owner_id uuid,eid uuid)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt r JOIN sync.sync_event s
  ON s.user_id=r.user_id AND s.event_id=r.source_event_id
   AND s.server_sequence=r.source_server_sequence
  WHERE r.user_id=owner_id AND r.source_event_id=eid AND s.ingress_validation_version=2) $$;

CREATE FUNCTION app_private.assert_normalized_listen_acceptance(eid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s sync.sync_event%ROWTYPE; i sync.device_event_inbox%ROWTYPE;
 l library.listening_event%ROWTYPE; r ml.sona_event_erasure_receipt%ROWTYPE; expected jsonb;
BEGIN
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 SELECT * INTO i FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;
 SELECT * INTO l FROM library.listening_event WHERE listening_event_id=eid FOR UPDATE;
 IF s.ingress_validation_version IS DISTINCT FROM 2
  AND i.ingress_validation_version IS DISTINCT FROM 2 THEN RETURN; END IF;
 IF i.apply_status='RECEIVED' AND l.listening_event_id IS NULL THEN RETURN; END IF;
 IF i.apply_status='ERASED' THEN
  SELECT * INTO r FROM ml.sona_event_erasure_receipt
   WHERE user_id=i.user_id AND source_event_id=eid;
  IF s.event_id IS NOT NULL OR l.listening_event_id IS NOT NULL OR r.source_event_id IS NULL
   OR i.terminal_ack IS DISTINCT FROM jsonb_build_object('event_id',eid,'outcome','EVENT_ERASED')
   OR (clock_timestamp()<r.erased_at+interval '400 days' AND NOT EXISTS(
    SELECT 1 FROM sync.tombstone t JOIN sync.sync_event e
     ON e.event_id=t.deleted_by_event_id AND e.user_id=t.user_id
     AND e.aggregate_id=t.aggregate_id AND e.operation='DELETE'
    WHERE t.user_id=i.user_id AND t.aggregate_id=eid AND t.deleted_by_event_id=r.erasure_event_id
     AND t.aggregate_type='LISTENING_EVENT' AND e.event_type='LISTENING_EVENT_ERASED'
     AND e.aggregate_type='LISTENING_EVENT' AND e.schema_version=2 AND e.payload='{}'::jsonb
     AND t.projection_event_type='LISTENING_EVENT_ERASED' AND t.projection_schema_version=2
     AND t.deleted_at=r.erased_at AND t.retain_until=r.erased_at+interval '400 days')) THEN
   RAISE EXCEPTION 'normalized_erasure_binding' USING ERRCODE='23514';
  END IF;
  RETURN;
 END IF;
 expected:=jsonb_build_object('interaction_type','LISTENING_EVENT_RECORDED',
  'local_user_track_ref_id',s.payload->'local_user_track_ref_id',
  'server_user_track_ref_id',l.user_track_ref_id,'recording_id',l.recording_id,
  'played_ms',l.played_ms,'track_duration_ms',l.track_duration_ms,
  'completion_ratio',l.completion_ratio,'event_origin',l.event_origin,'context',l.context,
  'explicit_feedback',l.explicit_feedback,'excluded_from_taste',l.excluded_from_taste);
 IF i.apply_status IS DISTINCT FROM 'APPLIED' OR s.event_id IS NULL
  OR l.listening_event_id IS NULL OR l.user_id IS DISTINCT FROM i.user_id
  OR l.device_id IS DISTINCT FROM i.device_id OR l.started_at IS DISTINCT FROM i.occurred_at
  OR l.recommendation_request_id IS NOT NULL OR l.event_origin='RECOMMENDED'
  OR s.event_type IS DISTINCT FROM 'LISTENING_EVENT_RECORDED'
  OR s.payload IS DISTINCT FROM expected
  OR i.idempotency_key IS NULL
  OR i.terminal_ack IS DISTINCT FROM app_private.normalized_listen_ack(eid,i.device_sequence) THEN
  RAISE EXCEPTION 'normalized_accepted_binding' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION app_private.check_normalized_listen_acceptance() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE eid uuid;
BEGIN
 eid:=COALESCE((to_jsonb(NEW)->>'event_id')::uuid,(to_jsonb(OLD)->>'event_id')::uuid,
  (to_jsonb(NEW)->>'listening_event_id')::uuid,(to_jsonb(OLD)->>'listening_event_id')::uuid);
 PERFORM app_private.assert_normalized_listen_acceptance(eid);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_normalized_listen_sync AFTER INSERT OR UPDATE OR DELETE
 ON sync.sync_event DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_normalized_listen_acceptance();
CREATE CONSTRAINT TRIGGER check_normalized_listen_inbox AFTER INSERT OR UPDATE OR DELETE
 ON sync.device_event_inbox DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_normalized_listen_acceptance();
CREATE CONSTRAINT TRIGGER check_normalized_listen_base AFTER INSERT OR UPDATE OR DELETE
 ON library.listening_event DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_normalized_listen_acceptance();

CREATE FUNCTION app_private.guard_normalized_listen_base() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox i WHERE i.event_id=OLD.listening_event_id
  AND i.ingress_validation_version=2) AND
  ((TG_OP='UPDATE' AND NEW IS DISTINCT FROM OLD) OR
   (TG_OP='DELETE' AND NOT app_private.privacy_owner_allowed(OLD.user_id)
    AND NOT app_private.normalized_listen_erasure_allowed(OLD.user_id,OLD.listening_event_id))) THEN
  RAISE EXCEPTION 'normalized_listen_immutable' USING ERRCODE='55000';
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER guard_normalized_listen_base BEFORE UPDATE OR DELETE ON library.listening_event
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_listen_base();

CREATE FUNCTION app_private.guard_normalized_listen_erasure() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.sona_event_erasure_receipt%ROWTYPE; deletion_id uuid; deletion_seq bigint;
BEGIN
 deletion_id:=CASE WHEN TG_TABLE_NAME='tombstone'
  THEN (to_jsonb(OLD)->>'deleted_by_event_id')::uuid ELSE (to_jsonb(OLD)->>'event_id')::uuid END;
 SELECT receipt.* INTO r FROM ml.sona_event_erasure_receipt receipt
  JOIN sync.device_event_inbox i ON i.user_id=receipt.user_id
   AND i.event_id=receipt.source_event_id AND i.ingress_validation_version=2
  WHERE receipt.erasure_event_id=deletion_id;
 IF r.source_event_id IS NOT NULL THEN
  IF TG_OP='UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
  IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(r.user_id) THEN RETURN OLD; END IF;
  SELECT server_sequence INTO deletion_seq FROM sync.sync_event WHERE event_id=deletion_id;
  IF TG_OP='DELETE' AND clock_timestamp()>=r.erased_at+interval '400 days'
   AND EXISTS(SELECT 1 FROM sync.owner_erasure_floor f WHERE f.user_id=r.user_id
    AND f.floor_sequence>=deletion_seq AND f.ledger_sequence IS NOT NULL
    AND f.ledger_digest IS NOT NULL AND octet_length(f.ledger_digest)=32) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'normalized_erasure_retention_required' USING ERRCODE='55000';
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER guard_normalized_listen_tombstone BEFORE UPDATE OR DELETE ON sync.tombstone
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_listen_erasure();
CREATE TRIGGER guard_normalized_listen_erasure_event BEFORE UPDATE OR DELETE ON sync.sync_event
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_listen_erasure();

CREATE FUNCTION app_private.accept_normalized_listen_v2(
 p_owner uuid,p_device uuid,p_epoch uuid,p_original bytea,p_canonical bytea) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE original_text text; canonical_text text; doc jsonb; payload jsonb; normalized jsonb;
 eid uuid; seq bigint; happened timestamptz; digest bytea; k text; v jsonb; ratio numeric;
 cursor_row sync.device_sync_cursor%ROWTYPE; prior sync.device_event_inbox%ROWTYPE;
 ref library.user_track_ref%ROWTYPE; floor_row sync.device_erasure_floor%ROWTYPE;
 owner_floor sync.owner_erasure_floor%ROWTYPE; ack jsonb; required text[];
 canonical_payload text; computed_canonical text; encoded text; number numeric;
BEGIN
 IF p_original IS NULL OR octet_length(p_original) NOT BETWEEN 1 AND 16384
  OR p_canonical IS NULL OR octet_length(p_canonical) NOT BETWEEN 1 AND 16384
  OR p_owner IS NULL OR p_device IS NULL OR p_epoch IS NULL THEN
  RAISE EXCEPTION 'normalized_request_bound' USING ERRCODE='22023';
 END IF;
 original_text:=convert_from(p_original,'UTF8'); canonical_text:=convert_from(p_canonical,'UTF8');
 IF NOT (original_text IS JSON OBJECT WITH UNIQUE KEYS)
  OR NOT (canonical_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
  RAISE EXCEPTION 'normalized_request_shape' USING ERRCODE='22023';
 END IF;
 doc:=original_text::jsonb; payload:=doc->'payload';
 required:=ARRAY['event_id','idempotency_key','user_id','device_id','device_sequence','event_type',
 'schema_version','aggregate_type','aggregate_local_id','aggregate_server_id','server_profile_id',
 'base_server_row_version','occurred_at','payload','request_hash'];
 IF NOT doc ?& required OR (SELECT count(*) FROM jsonb_object_keys(doc))<>15
  OR (doc-'request_hash') IS DISTINCT FROM canonical_text::jsonb
  OR doc->>'request_hash' IS DISTINCT FROM encode(sha256(p_canonical),'hex')
  OR doc->>'user_id' IS DISTINCT FROM p_owner::text
  OR doc->>'device_id' IS DISTINCT FROM p_device::text
  OR doc->'schema_version' IS DISTINCT FROM '1'::jsonb
  OR doc->>'event_type' IS DISTINCT FROM 'LISTENING_EVENT_RECORDED'
  OR doc->>'aggregate_type' IS DISTINCT FROM 'LISTENING_EVENT'
  OR doc->'base_server_row_version' IS DISTINCT FROM 'null'::jsonb
  OR jsonb_typeof(doc->'idempotency_key') IS DISTINCT FROM 'string'
  OR length(doc->>'idempotency_key') NOT BETWEEN 1 AND 200
  OR jsonb_typeof(doc->'server_profile_id') IS DISTINCT FROM 'string'
  OR doc->>'server_profile_id' IS DISTINCT FROM (doc->>'server_profile_id')::uuid::text
  OR jsonb_typeof(payload) IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(payload))>32
  OR payload->'recommendation' IS DISTINCT FROM 'null'::jsonb THEN
  RAISE EXCEPTION 'normalized_request_binding' USING ERRCODE='22023';
 END IF;
 eid:=(doc->>'event_id')::uuid; seq:=(doc->>'device_sequence')::bigint;
 happened:=(doc->>'occurred_at')::timestamptz;
 IF doc->>'event_id' IS DISTINCT FROM eid::text
  OR doc->>'aggregate_local_id' IS DISTINCT FROM eid::text
  OR (doc->'aggregate_server_id'<>'null'::jsonb AND doc->>'aggregate_server_id'<>eid::text)
  OR jsonb_typeof(doc->'device_sequence')<>'number'
  OR (doc->>'device_sequence') !~ '^[1-9][0-9]*$'
  OR seq NOT BETWEEN 1 AND 9007199254740991 OR NOT isfinite(happened)
  OR (doc->>'occurred_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
  RAISE EXCEPTION 'normalized_request_binding' USING ERRCODE='22023';
 END IF;
 FOR k,v IN SELECT * FROM jsonb_each(payload) LOOP
  IF length(k)>100 OR k !~ '^[a-z][a-z0-9_]{0,99}$'
   OR k ~ ('(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|'
    ||'base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|'
    ||'raw_model_features|model_features|feature_vector|debug_text|personal_debug)(_|$)')
   OR jsonb_typeof(v) NOT IN ('string','number','boolean','null')
   OR (jsonb_typeof(v)='string' AND length(v#>>'{}')>200) THEN
   RAISE EXCEPTION 'normalized_request_extension' USING ERRCODE='22023';
  END IF;
 END LOOP;
 -- RFC8785 for this bounded, flat, ASCII-key subset. Decimal wire numbers
 -- below 1e-6 or beyond 15 places are outside this dormant subset; integers
 -- must fit the exact JSON safe-integer range. No caller chooses canonical bytes.
 canonical_payload:='{';
 FOR k,v IN SELECT * FROM jsonb_each(payload) ORDER BY key COLLATE "C" LOOP
  encoded:=v::text;
  IF jsonb_typeof(v)='number' THEN
   number:=(v#>>'{}')::numeric;
   IF NOT ((number=trunc(number) AND abs(number)<=9007199254740991)
    OR (number BETWEEN 0.000001 AND 1 AND scale(number)<=15)) THEN
    RAISE EXCEPTION 'normalized_canonical_number_unsupported' USING ERRCODE='22023';
   END IF;
   encoded:=CASE WHEN number=0 THEN '0' WHEN scale(number)>0
    THEN rtrim(rtrim(number::text,'0'),'.') ELSE number::text END;
  END IF;
  canonical_payload:=canonical_payload||CASE WHEN canonical_payload='{' THEN '' ELSE ',' END
   ||to_jsonb(k)::text||':'||encoded;
 END LOOP;
 canonical_payload:=canonical_payload||'}'; computed_canonical:='{';
 FOR k,v IN SELECT * FROM jsonb_each(doc-'request_hash') ORDER BY key COLLATE "C" LOOP
  encoded:=CASE WHEN k='payload' THEN canonical_payload ELSE v::text END;
  IF k<>'payload' AND jsonb_typeof(v) NOT IN ('string','number','null') THEN
   RAISE EXCEPTION 'normalized_request_shape' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(v)='number' THEN
   number:=(v#>>'{}')::numeric;
   IF number<>trunc(number) OR abs(number)>9007199254740991 THEN
    RAISE EXCEPTION 'normalized_canonical_number_unsupported' USING ERRCODE='22023';
   END IF;
   encoded:=trunc(number)::text;
  END IF;
  computed_canonical:=computed_canonical||CASE WHEN computed_canonical='{' THEN '' ELSE ',' END
   ||to_jsonb(k)::text||':'||encoded;
 END LOOP;
 computed_canonical:=computed_canonical||'}';
 IF p_canonical<>convert_to(computed_canonical,'UTF8') THEN
  RAISE EXCEPTION 'normalized_wire_canonical_mismatch' USING ERRCODE='22023';
 END IF;
 required:=ARRAY['interaction_type','local_user_track_ref_id','server_user_track_ref_id',
 'recording_id','played_ms','track_duration_ms','completion_ratio','event_origin','context',
 'explicit_feedback','excluded_from_taste'];
 IF NOT payload ?& required OR payload->'server_user_track_ref_id'='null'::jsonb
  OR payload->>'event_origin' NOT IN ('ORGANIC','PLAYLIST','SEARCH','WAVE')
  OR payload->>'context' NOT IN ('GENERAL','WORKOUT','CYCLING','WORK','SLEEP','PARTY')
  OR payload->>'explicit_feedback' NOT IN ('NONE','LIKE','DISLIKE') THEN
  RAISE EXCEPTION 'normalized_causal_or_value_unavailable' USING ERRCODE='22023';
 END IF;
 SELECT jsonb_object_agg(key,value) INTO normalized
  FROM jsonb_each(payload) WHERE key=ANY(required);
 IF payload->'completion_ratio'<>'null'::jsonb THEN
  IF jsonb_typeof(payload->'completion_ratio')<>'number' THEN
   RAISE EXCEPTION 'normalized_request_ratio' USING ERRCODE='22023';
  END IF;
  ratio:=(payload->>'completion_ratio')::numeric;
  IF ratio NOT BETWEEN 0 AND 1 THEN
   RAISE EXCEPTION 'normalized_request_ratio' USING ERRCODE='22023';
  END IF;
  -- NUMERIC storage uses PostgreSQL round-half-away-from-zero, also used by the adapter.
  normalized:=jsonb_set(normalized,'{completion_ratio}',to_jsonb(ratio::numeric(7,6)));
 END IF;
 PERFORM app_private.validate_normalized_sync_payload_v2('LISTENING_EVENT_RECORDED',normalized);
 -- Match the existing owner publication namespace, including signed int64 byte order.
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),'hex'),1,16))::bit(64)::bigint);
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner AND status='ACTIVE'
  AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'normalized_binding_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM account.device WHERE device_id=p_device AND user_id=p_owner
  AND revoked_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'normalized_binding_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO cursor_row FROM sync.device_sync_cursor WHERE device_id=p_device
  AND user_id=p_owner AND journal_epoch=p_epoch FOR UPDATE;
 IF cursor_row.device_id IS NULL OR EXISTS(SELECT 1 FROM sync.device_erasure_invalidated_epoch
  WHERE device_id=p_device AND journal_epoch=p_epoch) THEN
  RAISE EXCEPTION 'normalized_binding_mismatch' USING ERRCODE='23514';
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
  RAISE EXCEPTION 'erasure_wipe_required' USING ERRCODE='55000';
 END IF;
 digest:=sha256(convert_to('SYNC_ORIGINAL_REQUEST_V1','UTF8')||decode('00','hex')||p_original);
 SELECT * INTO prior FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;
 IF prior.event_id IS NOT NULL THEN
  IF prior.user_id IS DISTINCT FROM p_owner OR prior.device_id IS DISTINCT FROM p_device
   OR prior.journal_epoch IS DISTINCT FROM p_epoch OR prior.device_sequence IS DISTINCT FROM seq
   OR prior.ingress_validation_version IS DISTINCT FROM 2
   OR prior.request_hash IS DISTINCT FROM digest
   OR prior.apply_status NOT IN ('APPLIED','ERASED') THEN
   RAISE EXCEPTION 'normalized_event_hash_or_identity_mismatch' USING ERRCODE='23514';
  END IF;
  PERFORM app_private.assert_normalized_listen_acceptance(eid);
  RETURN prior.terminal_ack;
 END IF;
 IF seq<>cursor_row.last_acked_device_sequence+1 OR EXISTS(
  SELECT 1 FROM sync.device_event_inbox WHERE device_id=p_device AND journal_epoch=p_epoch
   AND (device_sequence=seq OR idempotency_key=doc->>'idempotency_key')) OR EXISTS(
  SELECT 1 FROM sync.idempotency_record
   WHERE scope='sync-event:'||p_owner||':'||p_device||':'||p_epoch
   AND idempotency_key=doc->>'idempotency_key') THEN
  RAISE EXCEPTION 'normalized_sequence_or_idempotency_conflict' USING ERRCODE='23514';
 END IF;
 SELECT * INTO ref FROM library.user_track_ref
  WHERE user_track_ref_id=(normalized->>'server_user_track_ref_id')::uuid
   AND user_id=p_owner AND deleted_at IS NULL FOR UPDATE;
 IF ref.user_track_ref_id IS NULL OR
  (normalized->>'recording_id')::uuid IS DISTINCT FROM ref.recording_id THEN
  RAISE EXCEPTION 'normalized_track_binding' USING ERRCODE='23514';
 END IF;
 PERFORM app_private.stage_normalized_sync_ingress_v2(eid,p_owner,p_device,p_epoch,seq,
  'LISTENING_EVENT_RECORDED',1,'LISTENING_EVENT',eid,happened,digest,normalized,2);
 INSERT INTO library.listening_event(listening_event_id,user_id,device_id,user_track_ref_id,
  recording_id,started_at,played_ms,track_duration_ms,completion_ratio,event_origin,context,
  explicit_feedback,excluded_from_taste)
 VALUES(eid,p_owner,p_device,ref.user_track_ref_id,ref.recording_id,happened,
  (normalized->>'played_ms')::bigint,(normalized->>'track_duration_ms')::bigint,
  (normalized->>'completion_ratio')::numeric,normalized->>'event_origin',normalized->>'context',
  normalized->>'explicit_feedback',(normalized->>'excluded_from_taste')::boolean);
 ack:=app_private.normalized_listen_ack(eid,seq);
 INSERT INTO app_private.normalized_ingress_write_context VALUES(pg_current_xact_id(),eid);
 UPDATE sync.device_event_inbox SET apply_status='APPLIED',terminal_ack=ack,
  idempotency_key=doc->>'idempotency_key' WHERE event_id=eid;
 DELETE FROM app_private.normalized_ingress_write_context
  WHERE transaction_id=pg_current_xact_id() AND event_id=eid;
 UPDATE sync.device_sync_cursor SET last_acked_device_sequence=seq WHERE device_id=p_device;
 PERFORM app_private.assert_normalized_listen_acceptance(eid);
 RETURN ack;
END $$;
REVOKE ALL ON FUNCTION app_private.normalized_listen_ack(uuid,bigint),
 app_private.normalized_listen_erasure_allowed(uuid,uuid),
 app_private.assert_normalized_listen_acceptance(uuid),
 app_private.check_normalized_listen_acceptance(),app_private.guard_normalized_listen_base(),
 app_private.guard_normalized_listen_erasure(),
 app_private.accept_normalized_listen_v2(uuid,uuid,uuid,bytea,bytea) FROM PUBLIC;
""")
    _extend("app_private.guard_normalized_sync_ingress()", "BEGIN\n", _GUARD)
    _extend(
        "app_private.check_normalized_sync_ingress_pair()",
        "SELECT * INTO i FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;\n",
        _PAIR,
    )


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox
  WHERE ingress_validation_version=2 AND apply_status<>'RECEIVED') THEN
  RAISE EXCEPTION 'refusing normalized acceptance downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
""")
    _extend("app_private.check_normalized_sync_ingress_pair()", "", _PAIR, remove=True)
    _extend("app_private.guard_normalized_sync_ingress()", "", _GUARD, remove=True)
    op.execute("""
DROP TRIGGER check_normalized_listen_sync ON sync.sync_event;
DROP TRIGGER check_normalized_listen_inbox ON sync.device_event_inbox;
DROP TRIGGER check_normalized_listen_base ON library.listening_event;
DROP TRIGGER guard_normalized_listen_base ON library.listening_event;
DROP TRIGGER guard_normalized_listen_tombstone ON sync.tombstone;
DROP TRIGGER guard_normalized_listen_erasure_event ON sync.sync_event;
DROP FUNCTION app_private.accept_normalized_listen_v2(uuid,uuid,uuid,bytea,bytea);
DROP FUNCTION app_private.check_normalized_listen_acceptance();
DROP FUNCTION app_private.guard_normalized_listen_base();
DROP FUNCTION app_private.guard_normalized_listen_erasure();
DROP FUNCTION app_private.assert_normalized_listen_acceptance(uuid);
DROP FUNCTION app_private.normalized_listen_erasure_allowed(uuid,uuid);
DROP FUNCTION app_private.normalized_listen_ack(uuid,bigint);
ALTER TABLE sync.device_event_inbox DROP CONSTRAINT ck_inbox_normalized_ingress;
ALTER TABLE sync.device_event_inbox ADD CONSTRAINT ck_inbox_normalized_ingress CHECK(
 (ingress_validation_version=1 AND normalized_payload_sha256 IS NULL) OR
 (ingress_validation_version=2 AND normalized_payload_sha256 IS NOT NULL
  AND octet_length(normalized_payload_sha256)=32 AND payload='{}'::jsonb
  AND schema_version=1 AND apply_status='RECEIVED' AND terminal_ack IS NULL
  AND error_code IS NULL AND device_sequence BETWEEN 1 AND 9007199254740991));
""")
