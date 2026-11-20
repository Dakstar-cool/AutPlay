# ruff: noqa: E501
"""Forward typed listening authority, preserving captured installed predecessors."""

from sqlalchemy import Connection, text

from autplay.adapters.postgresql.native_feedback_attribution_schema import replace_exact

PRIOR_FUNCTIONS = (
    "app_private.accept_normalized_listen_v2(uuid,uuid,uuid,bytea,bytea)",
    "app_private.assert_normalized_listen_acceptance(uuid)",
    "app_private.guard_normalized_listen_base()",
    "app_private.guard_native_attribution()",
    "app_private.check_native_impression_v2()",
    "app_private.purge_recommendation_attribution_v2(uuid,uuid,text)",
    "app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer)",
)

SCHEMA_SQL = r"""
CREATE TABLE app_private.native_listen_prior_definition(name text PRIMARY KEY,ddl text NOT NULL);
REVOKE ALL ON app_private.native_listen_prior_definition FROM PUBLIC;
ALTER TABLE library.listening_event
 ADD COLUMN attribution_contract_version integer NOT NULL DEFAULT 1,
 ADD COLUMN attribution_closed boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT ck_listen_attribution_version CHECK(attribution_contract_version IN (1,2)),
 ADD CONSTRAINT ck_listen_attribution_closed CHECK(attribution_contract_version=2 OR NOT attribution_closed),
 ADD CONSTRAINT ck_listen_attribution_v2 CHECK(attribution_contract_version=1 OR
  (event_origin='RECOMMENDED' AND recording_id IS NOT NULL AND recommendation_request_id IS NULL)),
 DROP CONSTRAINT ck_listening_event_recommendation_origin,
 ADD CONSTRAINT ck_listening_event_recommendation_origin CHECK(event_origin<>'RECOMMENDED'
  OR (attribution_contract_version=1 AND recommendation_request_id IS NOT NULL)
  OR (attribution_contract_version=2 AND recommendation_request_id IS NULL)),
 ADD CONSTRAINT uq_listen_owner_recording UNIQUE(user_id,listening_event_id,recording_id);
ALTER TABLE ml.recommendation_attribution_association
 ALTER COLUMN interaction_id DROP NOT NULL,
 ADD COLUMN listening_event_id uuid UNIQUE,
 DROP CONSTRAINT native_attr_kind_check,
 ADD CONSTRAINT native_attr_kind_check CHECK(kind IN ('IMPRESSION','FEEDBACK','LISTEN')),
 ADD CONSTRAINT native_attr_target_check CHECK(
  (kind IN ('IMPRESSION','FEEDBACK') AND interaction_id IS NOT NULL AND listening_event_id IS NULL)
  OR (kind='LISTEN' AND interaction_id IS NULL AND listening_event_id IS NOT NULL)),
 DROP CONSTRAINT native_attr_impression_parent_check,
 ADD CONSTRAINT native_attr_impression_parent_check CHECK(
  (kind='IMPRESSION' AND impression_association_id IS NULL AND impression_association_kind IS NULL)
  OR (kind IN ('FEEDBACK','LISTEN') AND impression_association_id IS NOT NULL
   AND impression_association_kind IS NOT NULL AND impression_association_kind='IMPRESSION')),
 ADD CONSTRAINT fk_attribution_owner_listen FOREIGN KEY(owner_user_id,listening_event_id,recording_id)
  REFERENCES library.listening_event(user_id,listening_event_id,recording_id) ON DELETE RESTRICT;
CREATE INDEX ix_attribution_listen_parent ON ml.recommendation_attribution_association
 (impression_association_id,association_id) WHERE kind='LISTEN';
"""

HASH_SQL = r"""
CREATE FUNCTION app_private.native_listen_canonical(value jsonb) RETURNS text
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v jsonb; encoded text; output text:='{'; number numeric;
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'object' OR
  (SELECT count(*) FROM jsonb_object_keys(value))>32 THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_SHAPE' USING ERRCODE='22023'; END IF;
 FOR k,v IN SELECT * FROM jsonb_each(value) ORDER BY key COLLATE "C" LOOP
  IF k !~ '^[a-z][a-z0-9_]{0,99}$' OR k ~
   '(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|raw_model_features|model_features|feature_vector|debug_text|personal_debug)(_|$)' THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_EXTENSION' USING ERRCODE='22023'; END IF;
  encoded:=v::text;
  IF jsonb_typeof(v)='object' THEN
   IF k NOT IN ('payload','recommendation') THEN
    RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_EXTENSION' USING ERRCODE='22023'; END IF;
   IF EXISTS(SELECT 1 FROM jsonb_each(v) child WHERE jsonb_typeof(child.value)='object'
    AND (k<>'payload' OR child.key<>'recommendation')) THEN
    RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_EXTENSION' USING ERRCODE='22023'; END IF;
   encoded:=app_private.native_listen_canonical(v);
  ELSIF jsonb_typeof(v) NOT IN ('string','number','boolean','null')
   OR (jsonb_typeof(v)='string' AND length(v#>>'{}')>200) THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_EXTENSION' USING ERRCODE='22023';
  ELSIF jsonb_typeof(v)='number' THEN
   number:=(v#>>'{}')::numeric;
   IF NOT ((number=trunc(number) AND abs(number)<=9007199254740991)
    OR (number BETWEEN 0.000001 AND 1 AND scale(number)<=15)) THEN
    RAISE EXCEPTION 'NATIVE_LISTEN_WIRE_NUMBER' USING ERRCODE='22023'; END IF;
   encoded:=CASE WHEN number=0 THEN '0' WHEN scale(number)>0
    THEN rtrim(rtrim(number::text,'0'),'.') ELSE number::text END;
  END IF;
  output:=output||CASE WHEN output='{' THEN '' ELSE ',' END||to_jsonb(k)::text||':'||encoded;
 END LOOP;
 RETURN output||'}';
END $$;
CREATE FUNCTION app_private.native_listen_target_hash(t library.listening_event) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-listen-target.v1',ARRAY[
 uuid_send(t.listening_event_id),uuid_send(t.user_id),uuid_send(t.device_id),
 uuid_send(t.user_track_ref_id),uuid_send(t.recording_id),timestamptz_send(t.started_at),
 int8send(t.played_ms),CASE WHEN t.track_duration_ms IS NULL THEN NULL ELSE int8send(t.track_duration_ms) END,
 CASE WHEN t.completion_ratio IS NULL THEN NULL ELSE numeric_send(t.completion_ratio) END,
 convert_to(t.event_origin,'UTF8'),convert_to(t.context,'UTF8'),convert_to(t.explicit_feedback,'UTF8'),
 boolsend(t.excluded_from_taste),int8send(t.attribution_contract_version::bigint),boolsend(t.attribution_closed)]) $$;
CREATE FUNCTION app_private.native_listen_acceptance_hash(
 a ml.recommendation_attribution_association,i sync.device_event_inbox) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-listen-command.v1',ARRAY[
 uuid_send(a.owner_user_id),uuid_send(i.device_id),uuid_send(i.journal_epoch),uuid_send(i.event_id),
 int8send(i.device_sequence),timestamptz_send(i.occurred_at),i.request_hash,i.normalized_payload_sha256,
 convert_to(i.idempotency_key,'UTF8'),uuid_send(a.recommendation_request_id),int8send(a.served_rank::bigint),
 uuid_send(a.recording_id),uuid_send(a.presentation_id),uuid_send(a.impression_association_id),
 convert_to(a.presentation_source,'UTF8'),convert_to(a.presentation_surface,'UTF8'),
 int8send(a.wire_attribution_version::bigint)]) $$;
CREATE FUNCTION app_private.native_listen_association_hash(a ml.recommendation_attribution_association)
 RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-listen-association.v1',ARRAY[
 uuid_send(a.association_id),uuid_send(a.owner_user_id),uuid_send(a.recommendation_request_id),
 convert_to(a.kind,'UTF8'),int8send(a.served_rank::bigint),uuid_send(a.recording_id),
 convert_to(a.serving_provenance,'UTF8'),convert_to(a.request_surface,'UTF8'),
 convert_to(a.presentation_source,'UTF8'),convert_to(a.presentation_surface,'UTF8'),
 int8send(a.wire_attribution_version::bigint),uuid_send(a.presentation_id),uuid_send(a.listening_event_id),
 timestamptz_send(a.attribution_expires_at),a.original_request_sha256,a.acceptance_sha256,
 a.decision_sha256,a.served_item_sha256,uuid_send(a.impression_association_id),
 convert_to(a.impression_association_kind,'UTF8')]) $$;
CREATE FUNCTION app_private.assert_native_listen_parent(
 a ml.recommendation_attribution_association,device uuid,happened timestamptz,admission boolean)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent ml.recommendation_attribution_association%ROWTYPE; t library.user_interaction_event%ROWTYPE;
BEGIN
 SELECT * INTO parent FROM ml.recommendation_attribution_association
  WHERE association_id=a.impression_association_id FOR SHARE;
 IF parent.association_id IS NULL OR parent.kind IS DISTINCT FROM 'IMPRESSION'
  OR a.kind IS DISTINCT FROM 'LISTEN' OR a.impression_association_kind IS DISTINCT FROM 'IMPRESSION'
  OR parent.owner_user_id IS DISTINCT FROM a.owner_user_id
  OR parent.presentation_id IS DISTINCT FROM a.presentation_id
  OR parent.recommendation_request_id IS DISTINCT FROM a.recommendation_request_id
  OR parent.served_rank IS DISTINCT FROM a.served_rank OR parent.recording_id IS DISTINCT FROM a.recording_id
  OR parent.serving_provenance IS DISTINCT FROM a.serving_provenance
  OR parent.request_surface IS DISTINCT FROM a.request_surface
  OR parent.attribution_expires_at IS DISTINCT FROM a.attribution_expires_at THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_PARENT_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.assert_native_impression_v2(parent.interaction_id);
 SELECT * INTO t FROM library.user_interaction_event WHERE interaction_id=parent.interaction_id FOR SHARE;
 IF t.interaction_id IS NULL OR t.device_id IS DISTINCT FROM device OR t.attribution_closed
  OR happened<t.created_at OR (admission AND EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt
   WHERE user_id=a.owner_user_id AND source_event_id=parent.interaction_id)) THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_PARENT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
END $$;
"""

VALIDATE_SQL = r"""
CREATE FUNCTION app_private.assert_native_listen_v2(eid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s sync.sync_event%ROWTYPE; i sync.device_event_inbox%ROWTYPE; t library.listening_event%ROWTYPE;
 a ml.recommendation_attribution_association%ROWTYPE; r ml.native_attribution_purge_receipt%ROWTYPE;
 q ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
 item ml.recommendation_served_item%ROWTYPE; expected jsonb;
BEGIN
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 SELECT * INTO i FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;
 SELECT * INTO t FROM library.listening_event WHERE listening_event_id=eid FOR UPDATE;
 SELECT * INTO a FROM ml.recommendation_attribution_association WHERE listening_event_id=eid;
 SELECT * INTO r FROM ml.native_attribution_purge_receipt WHERE interaction_id=eid;
 IF t.listening_event_id IS NULL AND a.association_id IS NULL THEN RETURN; END IF;
 IF t.attribution_contract_version IS DISTINCT FROM 2 AND a.association_id IS NULL THEN RETURN; END IF;
 expected:=jsonb_build_object('interaction_type','LISTENING_EVENT_RECORDED',
  'local_user_track_ref_id',s.payload->'local_user_track_ref_id','server_user_track_ref_id',t.user_track_ref_id,
  'recording_id',t.recording_id,'played_ms',t.played_ms,'track_duration_ms',t.track_duration_ms,
  'completion_ratio',t.completion_ratio,'event_origin',t.event_origin,'context',t.context,
  'explicit_feedback',t.explicit_feedback,'excluded_from_taste',t.excluded_from_taste);
 IF i.event_id IS NULL OR s.event_id IS NULL OR t.listening_event_id IS NULL
  OR i.ingress_validation_version IS DISTINCT FROM 2 OR s.ingress_validation_version IS DISTINCT FROM 2
  OR t.attribution_contract_version IS DISTINCT FROM 2 OR i.apply_status IS DISTINCT FROM 'APPLIED'
  OR i.error_code IS NOT NULL OR i.idempotency_key IS NULL OR i.payload IS DISTINCT FROM '{}'::jsonb
  OR i.user_id IS DISTINCT FROM s.user_id OR i.user_id IS DISTINCT FROM t.user_id
  OR i.device_id IS DISTINCT FROM s.origin_device_id OR i.device_id IS DISTINCT FROM t.device_id
  OR i.event_type IS DISTINCT FROM 'LISTENING_EVENT_RECORDED' OR i.event_type IS DISTINCT FROM s.event_type
  OR i.schema_version IS DISTINCT FROM 1 OR s.schema_version IS DISTINCT FROM 1
  OR s.operation IS DISTINCT FROM 'UPSERT' OR s.server_row_version IS DISTINCT FROM 1
  OR s.aggregate_id IS DISTINCT FROM eid OR i.aggregate_id IS DISTINCT FROM eid
  OR i.aggregate_type IS DISTINCT FROM 'LISTENING_EVENT' OR s.aggregate_type IS DISTINCT FROM i.aggregate_type
  OR i.occurred_at IS DISTINCT FROM t.started_at OR s.ingress_device_sequence IS DISTINCT FROM i.device_sequence
  OR s.ingress_journal_epoch IS DISTINCT FROM i.journal_epoch OR t.recommendation_request_id IS NOT NULL
  OR t.recording_id IS NULL OR t.event_origin IS DISTINCT FROM 'RECOMMENDED'
  OR s.payload IS DISTINCT FROM expected
  OR i.normalized_payload_sha256 IS DISTINCT FROM s.normalized_payload_sha256
  OR s.normalized_payload_sha256 IS DISTINCT FROM app_private.sync_payload_sha256_pg_v1(s.payload)
  OR i.terminal_ack IS DISTINCT FROM app_private.normalized_listen_ack(eid,i.device_sequence) THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_ACCEPTED_BINDING' USING ERRCODE='23514'; END IF;
 IF a.association_id IS NULL THEN
  IF NOT t.attribution_closed THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_ASSOCIATION_REQUIRED' USING ERRCODE='23514'; END IF;
  IF r.association_id IS NULL THEN RETURN; END IF;
  IF r.reason<>'SERVING_TRUTH_EXPIRED' OR r.owner_user_id<>t.user_id
   OR r.source_server_sequence<>s.server_sequence OR r.original_request_sha256<>i.request_hash
   OR r.target_sha256<>app_private.native_listen_target_hash(t) OR r.purged_at<r.attribution_expires_at THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_CLOSED_BINDING' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 SELECT * INTO q FROM ml.recommendation_request WHERE recommendation_request_id=a.recommendation_request_id;
 PERFORM app_private.assert_native_served_authority(a.recommendation_request_id);
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=a.recommendation_request_id;
 SELECT * INTO item FROM ml.recommendation_served_item WHERE recommendation_request_id=a.recommendation_request_id
  AND source_rank=a.served_rank;
 IF q.recommendation_request_id IS NULL OR q.serving_contract_version<>2 OR q.user_id<>t.user_id
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR t.attribution_closed OR a.kind IS DISTINCT FROM 'LISTEN'
  OR a.interaction_id IS NOT NULL OR a.listening_event_id IS DISTINCT FROM eid
  OR item.recording_id IS DISTINCT FROM t.recording_id OR a.recording_id IS DISTINCT FROM t.recording_id
  OR a.owner_user_id IS DISTINCT FROM t.user_id OR a.serving_provenance IS DISTINCT FROM item.attribution_source
  OR a.serving_provenance IS DISTINCT FROM 'P11' OR a.request_surface IS DISTINCT FROM q.surface
  OR a.decision_sha256 IS DISTINCT FROM d.decision_sha256 OR a.served_item_sha256 IS DISTINCT FROM item.served_item_sha256
  OR a.original_request_sha256 IS DISTINCT FROM i.request_hash
  OR a.attribution_expires_at IS DISTINCT FROM q.created_at+interval '180 days'
  OR a.attribution_expires_at IS DISTINCT FROM d.served_truth_expires_at
  OR t.started_at<q.created_at OR t.started_at>=a.attribution_expires_at
  OR a.association_sha256 IS DISTINCT FROM app_private.native_listen_association_hash(a)
  OR a.acceptance_sha256 IS DISTINCT FROM app_private.native_listen_acceptance_hash(a,i) THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_CAUSAL_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.assert_native_listen_parent(a,t.device_id,t.started_at,false);
END $$;
CREATE FUNCTION app_private.check_native_listen_v2() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM app_private.assert_native_listen_v2(coalesce(NEW.listening_event_id,OLD.listening_event_id));
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_native_listen_association AFTER INSERT OR UPDATE OR DELETE
 ON ml.recommendation_attribution_association DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_native_listen_v2();
"""

PURGE_SQL = r"""
CREATE FUNCTION app_private.purge_native_listen_v2(owner_id uuid,eid uuid,p_reason text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a ml.recommendation_attribution_association%ROWTYPE; t library.listening_event%ROWTYPE;
 s sync.sync_event%ROWTYPE; actual_time timestamptz;
BEGIN
 IF p_reason IS NULL OR p_reason NOT IN ('SERVING_TRUTH_EXPIRED','OWNER_EVENT_ERASURE','ACCOUNT_DELETION') THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_PURGE_REASON' USING ERRCODE='22023'; END IF;
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 SELECT * INTO a FROM ml.recommendation_attribution_association
  WHERE owner_user_id=owner_id AND listening_event_id=eid;
 IF a.association_id IS NULL THEN RETURN false; END IF;
 PERFORM app_private.lock_recommendation_request_v2(a.recommendation_request_id);
 SELECT * INTO a FROM ml.recommendation_attribution_association
  WHERE owner_user_id=owner_id AND listening_event_id=eid FOR UPDATE;
 IF a.association_id IS NULL THEN RETURN false; END IF;
 SELECT * INTO t FROM library.listening_event WHERE listening_event_id=eid FOR UPDATE;
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 PERFORM app_private.assert_native_listen_v2(eid);
 actual_time:=clock_timestamp();
 IF (p_reason='SERVING_TRUTH_EXPIRED' AND actual_time<a.attribution_expires_at)
  OR (p_reason='OWNER_EVENT_ERASURE' AND NOT app_private.normalized_listen_erasure_allowed(owner_id,eid))
  OR (p_reason='ACCOUNT_DELETION' AND NOT app_private.privacy_owner_allowed(owner_id)) THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_PURGE_AUTHORITY' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.native_attribution_context
  VALUES(pg_current_xact_id(),eid,owner_id,a.recommendation_request_id,'PURGE',a.association_sha256);
 IF p_reason='SERVING_TRUTH_EXPIRED' THEN t.attribution_closed:=true; END IF;
 INSERT INTO ml.native_attribution_purge_receipt(association_id,owner_user_id,interaction_id,
  source_server_sequence,original_request_sha256,acceptance_sha256,association_sha256,
  target_sha256,reason,attribution_expires_at,purged_at,retain_until)
 VALUES(a.association_id,owner_id,eid,s.server_sequence,a.original_request_sha256,a.acceptance_sha256,
  a.association_sha256,app_private.native_listen_target_hash(t),p_reason,a.attribution_expires_at,
  actual_time,a.attribution_expires_at+interval '220 days');
 IF p_reason='SERVING_TRUTH_EXPIRED' THEN
  UPDATE library.listening_event SET attribution_closed=true WHERE listening_event_id=eid; END IF;
 DELETE FROM ml.recommendation_attribution_association WHERE association_id=a.association_id;
 DELETE FROM app_private.native_attribution_context WHERE transaction_id=pg_current_xact_id() AND event_id=eid;
 RETURN true;
END $$;
"""

ADMISSION_SQL = r"""
 SELECT * INTO q FROM ml.recommendation_request WHERE recommendation_request_id=request_id
  AND user_id=p_owner FOR SHARE;
 IF q.recommendation_request_id IS NULL THEN
  RETURN app_private.expire_native_served_authority(p_owner,request_id)||jsonb_build_object('event_id',eid);
 END IF;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=request_id FOR SHARE;
 IF q.serving_contract_version<>2 OR d.state IS DISTINCT FROM 'COMMITTED_P11'
  OR d.served_truth_expires_at IS DISTINCT FROM q.created_at+interval '180 days' THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_SERVING_AUTHORITY' USING ERRCODE='23514'; END IF;
 clock_value:=clock_timestamp();
 IF clock_value>=d.served_truth_expires_at THEN
  RETURN app_private.expire_native_served_authority(p_owner,request_id)||jsonb_build_object('event_id',eid);
 END IF;
 IF happened<q.created_at OR happened>clock_value OR happened>=d.served_truth_expires_at THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_TIME_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.assert_native_served_authority(request_id);
 SELECT * INTO item FROM ml.recommendation_served_item WHERE recommendation_request_id=request_id
  AND source_rank=rank_value FOR SHARE;
 IF item.recording_id IS DISTINCT FROM (normalized->>'recording_id')::uuid
  OR item.attribution_source IS DISTINCT FROM 'P11' OR item.origin IS DISTINCT FROM 'P11' THEN
  RAISE EXCEPTION 'ATTRIBUTION_MISMATCH' USING ERRCODE='23514'; END IF;
 IF prior.event_id IS NOT NULL THEN
  SELECT * INTO a FROM ml.recommendation_attribution_association WHERE listening_event_id=eid;
  PERFORM app_private.assert_native_listen_v2(eid);
  IF a.association_id IS NULL THEN RAISE EXCEPTION 'ATTRIBUTION_CONFLICT' USING ERRCODE='23514'; END IF;
  IF clock_timestamp()>=a.attribution_expires_at THEN
   RETURN app_private.expire_native_served_authority(p_owner,request_id)||jsonb_build_object('event_id',eid);
  END IF;
  RETURN jsonb_build_object('event_id',eid,'association_id',a.association_id,'outcome','APPLIED',
   'terminal_ack',prior.terminal_ack);
 END IF;
 SELECT * INTO parent FROM ml.recommendation_attribution_association
  WHERE interaction_id=impression_event AND kind='IMPRESSION' FOR SHARE;
 a.association_id:=uuidv7(); a.owner_user_id:=p_owner; a.recommendation_request_id:=request_id;
 a.kind:='LISTEN'; a.served_rank:=rank_value; a.recording_id:=item.recording_id;
 a.serving_provenance:=item.attribution_source; a.request_surface:=q.surface;
 a.presentation_source:=claim->>'source'; a.presentation_surface:=claim->>'surface';
 a.wire_attribution_version:=1; a.presentation_id:=(claim->>'presentation_id')::uuid;
 a.listening_event_id:=eid; a.attribution_expires_at:=d.served_truth_expires_at;
 a.original_request_sha256:=digest; a.decision_sha256:=d.decision_sha256;
 a.served_item_sha256:=item.served_item_sha256; a.impression_association_id:=parent.association_id;
 a.impression_association_kind:='IMPRESSION';
 PERFORM app_private.assert_native_listen_parent(a,p_device,happened,true);
 binding_inbox.event_id:=eid; binding_inbox.device_id:=p_device; binding_inbox.journal_epoch:=p_epoch;
 binding_inbox.device_sequence:=seq; binding_inbox.occurred_at:=happened; binding_inbox.request_hash:=digest;
 binding_inbox.normalized_payload_sha256:=app_private.sync_payload_sha256_pg_v1(normalized);
 binding_inbox.idempotency_key:=doc->>'idempotency_key';
 a.acceptance_sha256:=app_private.native_listen_acceptance_hash(a,binding_inbox);
 a.association_sha256:=app_private.native_listen_association_hash(a);
"""

CLAIM_SQL = r"""
 claim:=payload->'recommendation';
 IF jsonb_typeof(claim) IS DISTINCT FROM 'object' OR NOT claim ?& ARRAY[
  'recommendation_request_id','source_rank','recording_id','source','surface','presentation_id']
  OR claim ? 'offline_pack_id' OR claim->>'source' IS NULL OR claim->>'surface' IS NULL
  OR claim->>'source' !~ '^[a-z][a-z0-9_]{0,99}$'
  OR claim->>'surface' !~ '^[a-z][a-z0-9_]{0,99}$'
  OR jsonb_typeof(claim->'source_rank') IS DISTINCT FROM 'number'
  OR (claim->>'source_rank') !~ '^[1-9][0-9]*$'
  OR (claim->>'source_rank')::integer NOT BETWEEN 1 AND 1000
  OR NOT (claim ? 'impression_event_local_id' OR claim ? 'impression_event_server_id') THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_CLAIM_BOUND' USING ERRCODE='22023'; END IF;
 request_id:=(claim->>'recommendation_request_id')::uuid; rank_value:=(claim->>'source_rank')::integer;
 impression_event:=coalesce(claim->>'impression_event_server_id',claim->>'impression_event_local_id')::uuid;
 IF claim->>'recommendation_request_id' IS DISTINCT FROM request_id::text
  OR claim->>'recording_id' IS DISTINCT FROM ((claim->>'recording_id')::uuid)::text
  OR claim->>'recording_id' IS DISTINCT FROM payload->>'recording_id'
  OR claim->>'presentation_id' IS DISTINCT FROM ((claim->>'presentation_id')::uuid)::text
  OR claim->>'presentation_id' IS NULL OR impression_event IS NULL
  OR (claim->>'impression_event_local_id' IS NOT NULL
   AND claim->>'impression_event_local_id' IS DISTINCT FROM impression_event::text)
  OR (claim->>'impression_event_server_id' IS NOT NULL
   AND claim->>'impression_event_server_id' IS DISTINCT FROM impression_event::text) THEN
  RAISE EXCEPTION 'NATIVE_LISTEN_CLAIM_BINDING' USING ERRCODE='22023'; END IF;
 PERFORM app_private.native_listen_canonical(claim);
"""


def listening_acceptance(source: str) -> str:
    """Clone bounded original-wire validation, then attach SQL-derived authority."""
    result = replace_exact(
        source, "app_private.accept_normalized_listen_v2(", "app_private.accept_native_listen_v2("
    )
    result = replace_exact(
        result,
        "canonical_payload text;",
        "claim jsonb; request_id uuid; impression_event uuid; rank_value integer;\n a ml.recommendation_attribution_association%ROWTYPE; parent ml.recommendation_attribution_association%ROWTYPE;\n q ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;\n item ml.recommendation_served_item%ROWTYPE; binding_inbox sync.device_event_inbox%ROWTYPE;\n clock_value timestamptz; canonical_payload text;",
    )
    result = replace_exact(
        result,
        "OR payload->'recommendation' IS DISTINCT FROM 'null'::jsonb",
        "OR jsonb_typeof(payload->'recommendation') IS DISTINCT FROM 'object'",
    )
    start = result.index(" FOR k,v IN SELECT * FROM jsonb_each(payload) LOOP")
    end = result.index(" required:=ARRAY['interaction_type'", start)
    result = (
        result[:start]
        + " IF p_canonical<>convert_to(app_private.native_listen_canonical(doc-'request_hash'),'UTF8') THEN\n RAISE EXCEPTION 'NATIVE_LISTEN_CANONICAL_BINDING' USING ERRCODE='22023'; END IF;\n"
        + CLAIM_SQL
        + result[end:]
    )
    result = replace_exact(
        result,
        "payload->>'event_origin' NOT IN ('ORGANIC','PLAYLIST','SEARCH','WAVE')",
        "payload->>'event_origin' IS DISTINCT FROM 'RECOMMENDED'",
    )
    result = replace_exact(
        result,
        " -- Match the existing owner publication namespace",
        " PERFORM app_private.lock_native_served_server();\n -- Match the existing owner publication namespace",
    )
    result = replace_exact(
        result,
        " SELECT * INTO prior FROM sync.device_event_inbox",
        " PERFORM app_private.lock_recommendation_request_v2(request_id);\n SELECT * INTO prior FROM sync.device_event_inbox",
    )
    result = replace_exact(
        result,
        "RAISE EXCEPTION 'normalized_event_hash_or_identity_mismatch'",
        "RAISE EXCEPTION 'ATTRIBUTION_CONFLICT'",
    )
    result = replace_exact(
        result,
        "  RETURN prior.terminal_ack;",
        "  IF prior.apply_status='ERASED' THEN RETURN jsonb_build_object('event_id',eid,'outcome','EVENT_ERASED',\n   'association_id',NULL,'terminal_ack',prior.terminal_ack); END IF;",
    )
    result = replace_exact(
        result,
        " IF seq<>cursor_row.last_acked_device_sequence+1",
        ADMISSION_SQL + " IF seq<>cursor_row.last_acked_device_sequence+1",
    )
    result = replace_exact(
        result,
        " PERFORM app_private.stage_normalized_sync_ingress_v2(eid",
        " BEGIN\n INSERT INTO app_private.native_attribution_context VALUES(pg_current_xact_id(),eid,p_owner,request_id,'ACCEPT',a.acceptance_sha256);\n PERFORM app_private.stage_normalized_sync_ingress_v2(eid",
    )
    result = replace_exact(
        result,
        "explicit_feedback,excluded_from_taste)\n VALUES",
        "explicit_feedback,excluded_from_taste,attribution_contract_version)\n VALUES",
    )
    result = replace_exact(
        result,
        "normalized->>'explicit_feedback',(normalized->>'excluded_from_taste')::boolean);",
        "normalized->>'explicit_feedback',(normalized->>'excluded_from_taste')::boolean,2);\n PERFORM 1 FROM library.listening_event WHERE listening_event_id=eid FOR UPDATE;\n IF clock_timestamp()>=a.attribution_expires_at THEN\n RAISE EXCEPTION 'NATIVE_LISTEN_LINEARIZATION_EXPIRED' USING ERRCODE='P1906'; END IF;\n INSERT INTO ml.recommendation_attribution_association SELECT a.*;",
    )
    result = replace_exact(
        result,
        " RETURN ack;\nEND",
        " DELETE FROM app_private.native_attribution_context WHERE transaction_id=pg_current_xact_id() AND event_id=eid;\n RETURN jsonb_build_object('event_id',eid,'association_id',a.association_id,'outcome','APPLIED','terminal_ack',ack);\n EXCEPTION WHEN SQLSTATE 'P1906' THEN\n RETURN app_private.expire_native_served_authority(p_owner,request_id)||jsonb_build_object('event_id',eid);\n END;\nEND",
    )
    return result


BASE_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_private.guard_normalized_listen_base() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.attribution_contract_version=2 AND (NEW.attribution_closed OR NOT EXISTS(
   SELECT 1 FROM app_private.native_attribution_context c WHERE c.transaction_id=pg_current_xact_id()
    AND c.event_id=NEW.listening_event_id AND c.owner_id=NEW.user_id AND c.action='ACCEPT')) THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_WRITER_REQUIRED' USING ERRCODE='55000'; END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.attribution_contract_version=2 AND NOT OLD.attribution_closed AND NEW.attribution_closed
   AND (to_jsonb(OLD)-'attribution_closed')=(to_jsonb(NEW)-'attribution_closed') AND EXISTS(
    SELECT 1 FROM app_private.native_attribution_context c
    JOIN ml.recommendation_attribution_association a ON a.listening_event_id=c.event_id
    WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=OLD.listening_event_id
     AND c.owner_id=OLD.user_id AND c.action='PURGE' AND c.content_sha256=a.association_sha256) THEN
   RETURN NEW;
  END IF;
  IF NEW.attribution_contract_version IS DISTINCT FROM OLD.attribution_contract_version
   OR (OLD.attribution_contract_version=2 AND NEW IS DISTINCT FROM OLD) THEN
   RAISE EXCEPTION 'NATIVE_LISTEN_IMMUTABLE' USING ERRCODE='55000'; END IF;
 END IF;
 IF TG_OP='DELETE' AND OLD.attribution_contract_version=2 THEN
  IF app_private.privacy_owner_allowed(OLD.user_id) THEN
   PERFORM app_private.purge_native_listen_v2(OLD.user_id,OLD.listening_event_id,'ACCOUNT_DELETION');
  ELSIF app_private.normalized_listen_erasure_allowed(OLD.user_id,OLD.listening_event_id) THEN
   PERFORM app_private.purge_native_listen_v2(OLD.user_id,OLD.listening_event_id,'OWNER_EVENT_ERASURE');
  ELSE RAISE EXCEPTION 'NATIVE_LISTEN_ERASURE_AUTHORITY' USING ERRCODE='55000'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox i WHERE i.event_id=OLD.listening_event_id
  AND i.ingress_validation_version=2) AND
  ((TG_OP='UPDATE' AND NEW IS DISTINCT FROM OLD) OR
   (TG_OP='DELETE' AND NOT app_private.privacy_owner_allowed(OLD.user_id)
    AND NOT app_private.normalized_listen_erasure_allowed(OLD.user_id,OLD.listening_event_id))) THEN
  RAISE EXCEPTION 'normalized_listen_immutable' USING ERRCODE='55000'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER guard_native_listen_insert BEFORE INSERT ON library.listening_event
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_listen_base();
"""


def install_native_listening(connection: Connection) -> None:
    connection.execute(text(SCHEMA_SQL))
    prior: dict[str, str] = {}
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_listen_prior_function_missing")
        prior[signature] = definition
        connection.execute(
            text("INSERT INTO app_private.native_listen_prior_definition VALUES(:name,:ddl)"),
            {"name": signature, "ddl": definition},
        )
    for sql in (HASH_SQL, VALIDATE_SQL, PURGE_SQL, BASE_GUARD_SQL):
        connection.execute(text(sql))
    dispatch = """ IF EXISTS(SELECT 1 FROM library.listening_event WHERE listening_event_id=eid
 AND attribution_contract_version=2) OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association
 WHERE listening_event_id=eid) THEN PERFORM app_private.assert_native_listen_v2(eid); RETURN; END IF;
"""
    connection.execute(
        text(replace_exact(prior[PRIOR_FUNCTIONS[1]], "BEGIN\n", "BEGIN\n" + dispatch))
    )
    connection.execute(text(listening_acceptance(prior[PRIOR_FUNCTIONS[0]])))
    guard = prior[PRIOR_FUNCTIONS[3]]
    for prefix in ("NEW", "OLD"):
        guard = guard.replace(
            f"{prefix}.interaction_id",
            f"coalesce({prefix}.interaction_id,{prefix}.listening_event_id)",
        )
    connection.execute(text(guard))
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[4]],
                "BEGIN\n",
                "BEGIN\n IF TG_TABLE_NAME='recommendation_attribution_association' AND\n coalesce(to_jsonb(NEW)->>'kind',to_jsonb(OLD)->>'kind')='LISTEN' THEN RETURN NULL; END IF;\n",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[5]],
                "BEGIN\n",
                "BEGIN\n IF EXISTS(SELECT 1 FROM ml.recommendation_attribution_association WHERE owner_user_id=owner_id\n AND listening_event_id=eid) THEN RETURN app_private.purge_native_listen_v2(owner_id,eid,p_reason); END IF;\n",
            )
        )
    )
    purge = replace_exact(
        prior[PRIOR_FUNCTIONS[6]],
        "FOR eid IN SELECT interaction_id",
        "FOR eid IN SELECT coalesce(interaction_id,listening_event_id)",
    )
    purge = replace_exact(
        purge,
        "CASE WHEN kind='FEEDBACK' THEN 0 ELSE 1 END",
        "CASE WHEN kind IN ('FEEDBACK','LISTEN') THEN 0 ELSE 1 END",
    )
    connection.execute(text(purge))
    connection.execute(
        text("""
REVOKE ALL ON FUNCTION app_private.native_listen_canonical(jsonb),
 app_private.native_listen_target_hash(library.listening_event),
 app_private.native_listen_acceptance_hash(ml.recommendation_attribution_association,sync.device_event_inbox),
 app_private.native_listen_association_hash(ml.recommendation_attribution_association),
 app_private.assert_native_listen_parent(ml.recommendation_attribution_association,uuid,timestamptz,boolean),
 app_private.assert_native_listen_v2(uuid),app_private.check_native_listen_v2(),
 app_private.purge_native_listen_v2(uuid,uuid,text),
 app_private.accept_native_listen_v2(uuid,uuid,uuid,bytea,bytea) FROM PUBLIC;
""")
    )


def remove_native_listening(connection: Connection) -> None:
    connection.execute(
        text("""
LOCK TABLE library.listening_event,ml.recommendation_attribution_association,
 ml.native_attribution_purge_receipt,sync.device_event_inbox,sync.sync_event IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.recommendation_attribution_association WHERE kind='LISTEN')
 OR EXISTS(SELECT 1 FROM library.listening_event WHERE attribution_contract_version=2)
 OR EXISTS(SELECT 1 FROM ml.native_attribution_purge_receipt r JOIN sync.device_event_inbox i
  ON i.event_id=r.interaction_id WHERE i.event_type='LISTENING_EVENT_RECORDED') THEN
 RAISE EXCEPTION 'refusing native listening downgrade with retained evidence' USING ERRCODE='55000'; END IF;
END $$;
DROP TRIGGER guard_native_listen_insert ON library.listening_event;
DROP TRIGGER check_native_listen_association ON ml.recommendation_attribution_association;
DROP INDEX ml.ix_attribution_listen_parent;
""")
    )
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT ddl FROM app_private.native_listen_prior_definition WHERE name=:name"),
            {"name": signature},
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_listen_prior_function_missing")
        connection.execute(text(definition))
    connection.execute(
        text("""
DROP FUNCTION app_private.accept_native_listen_v2(uuid,uuid,uuid,bytea,bytea);
DROP FUNCTION app_private.purge_native_listen_v2(uuid,uuid,text);
DROP FUNCTION app_private.check_native_listen_v2();
DROP FUNCTION app_private.assert_native_listen_v2(uuid);
DROP FUNCTION app_private.assert_native_listen_parent(ml.recommendation_attribution_association,uuid,timestamptz,boolean);
DROP FUNCTION app_private.native_listen_association_hash(ml.recommendation_attribution_association);
DROP FUNCTION app_private.native_listen_acceptance_hash(ml.recommendation_attribution_association,sync.device_event_inbox);
DROP FUNCTION app_private.native_listen_target_hash(library.listening_event);
DROP FUNCTION app_private.native_listen_canonical(jsonb);
ALTER TABLE ml.recommendation_attribution_association
 DROP CONSTRAINT fk_attribution_owner_listen, DROP CONSTRAINT native_attr_target_check,
 DROP CONSTRAINT native_attr_kind_check, ADD CONSTRAINT native_attr_kind_check CHECK(kind IN ('IMPRESSION','FEEDBACK')),
 DROP CONSTRAINT native_attr_impression_parent_check,
 ADD CONSTRAINT native_attr_impression_parent_check CHECK(
  (kind='IMPRESSION' AND impression_association_id IS NULL AND impression_association_kind IS NULL)
  OR (kind='FEEDBACK' AND impression_association_id IS NOT NULL
   AND impression_association_kind IS NOT NULL AND impression_association_kind='IMPRESSION')),
 DROP COLUMN listening_event_id, ALTER COLUMN interaction_id SET NOT NULL;
ALTER TABLE library.listening_event
 DROP CONSTRAINT ck_listen_attribution_version,DROP CONSTRAINT ck_listen_attribution_closed,
 DROP CONSTRAINT ck_listen_attribution_v2,DROP CONSTRAINT uq_listen_owner_recording,
 DROP CONSTRAINT ck_listening_event_recommendation_origin,
 ADD CONSTRAINT ck_listening_event_recommendation_origin CHECK(event_origin<>'RECOMMENDED' OR recommendation_request_id IS NOT NULL),
 DROP COLUMN attribution_contract_version,DROP COLUMN attribution_closed;
DROP TABLE app_private.native_listen_prior_definition;
""")
    )
