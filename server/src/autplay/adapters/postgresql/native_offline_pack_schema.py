# ruff: noqa: E501
"""Forward-only private offline-pack graph over existing immutable P11 truth.

Installed predecessors are captured rather than historical migrations edited.
No PUBLIC grants, runtime producer, serving cutover or Sona authority is added.
"""

from sqlalchemy import Connection, text

from autplay.adapters.postgresql.native_feedback_attribution_schema import replace_exact

PRIOR_FUNCTIONS = (
    "app_private.guard_native_attribution()",
    "app_private.check_native_impression_v2()",
    "app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer)",
    "app_private.purge_native_served_authority(uuid,uuid)",
    "app_private.purge_native_served_owner(uuid)",
)

SCHEMA_SQL = r"""
CREATE TABLE app_private.native_pack_prior_definition(name text PRIMARY KEY,ddl text NOT NULL);
CREATE TABLE app_private.native_pack_context(
 transaction_id xid8 NOT NULL,pack_id uuid NOT NULL,owner_id uuid NOT NULL,
 action text NOT NULL CHECK(action IN ('CREATE','CONTENT_PURGE','BINDING_PURGE','RECEIPT_PURGE')),
 content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
 PRIMARY KEY(transaction_id,pack_id));
REVOKE ALL ON app_private.native_pack_prior_definition,app_private.native_pack_context FROM PUBLIC;
ALTER TABLE ml.offline_recommendation_pack
 ADD COLUMN attribution_contract_version integer NOT NULL DEFAULT 1,
 ADD CONSTRAINT ck_native_pack_version CHECK(attribution_contract_version IN (1,2)),
 ADD CONSTRAINT ck_native_pack_v2 CHECK(attribution_contract_version=1 OR
  (recommendation_request_id IS NULL AND device_id IS NOT NULL AND payload_version=2
   AND payload_encoding='RAW_JSON' AND octet_length(payload)<=2097152
   AND payload_sha256=sha256(payload) AND expires_at<=created_at+interval '7 days')),
 ADD CONSTRAINT uq_native_pack_owner UNIQUE(user_id,offline_pack_id);
CREATE TABLE ml.offline_pack_binding_receipt(
 pack_id uuid PRIMARY KEY,owner_user_id uuid NOT NULL,device_id uuid NOT NULL,
 recommendation_request_id uuid NOT NULL,
 serving_contract_version integer NOT NULL CONSTRAINT native_pack_binding_version CHECK(serving_contract_version=2),
 decision_sha256 bytea NOT NULL CONSTRAINT native_pack_binding_decision_sha256 CHECK(octet_length(decision_sha256)=32),
 served_item_count integer NOT NULL CONSTRAINT native_pack_binding_count CHECK(served_item_count BETWEEN 0 AND 100),
 served_items_sha256 bytea NOT NULL CONSTRAINT native_pack_binding_served_items_sha256 CHECK(octet_length(served_items_sha256)=32),
 pack_payload_sha256 bytea NOT NULL CONSTRAINT native_pack_binding_pack_payload_sha256 CHECK(octet_length(pack_payload_sha256)=32),
 created_at timestamptz NOT NULL,content_expires_at timestamptz NOT NULL,
 attribution_expires_at timestamptz NOT NULL,
 binding_sha256 bytea NOT NULL CONSTRAINT native_pack_binding_binding_sha256 CHECK(octet_length(binding_sha256)=32),
 FOREIGN KEY(owner_user_id,recommendation_request_id)
  REFERENCES ml.recommendation_request(user_id,recommendation_request_id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_user_id,device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT,
 CONSTRAINT native_pack_binding_expiry CHECK(content_expires_at>created_at AND content_expires_at<=created_at+interval '7 days'
  AND content_expires_at<=attribution_expires_at));
CREATE INDEX ix_native_pack_binding_owner ON ml.offline_pack_binding_receipt
 (owner_user_id,recommendation_request_id,pack_id);
CREATE INDEX ix_native_pack_binding_expiry ON ml.offline_pack_binding_receipt
 (attribution_expires_at,pack_id);
CREATE TABLE ml.native_offline_pack_purge_receipt(
 pack_id uuid NOT NULL,stage text NOT NULL CONSTRAINT native_pack_purge_stage CHECK(stage IN ('CONTENT','BINDING')),
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 binding_sha256 bytea NOT NULL CONSTRAINT native_pack_purge_binding_sha256 CHECK(octet_length(binding_sha256)=32),
 association_sha256 bytea NOT NULL CONSTRAINT native_pack_purge_association_sha256 CHECK(octet_length(association_sha256)=32),
 payload_sha256 bytea NOT NULL CONSTRAINT native_pack_purge_payload_sha256 CHECK(octet_length(payload_sha256)=32),
 reason text NOT NULL CONSTRAINT native_pack_purge_reason CHECK(reason IN ('PACK_EXPIRED','SERVING_TRUTH_EXPIRED','ACCOUNT_DELETION')),
 purged_at timestamptz NOT NULL,retain_until timestamptz NOT NULL,
 PRIMARY KEY(pack_id,stage));
CREATE INDEX ix_native_pack_purge_owner ON ml.native_offline_pack_purge_receipt
 (owner_user_id,retain_until,pack_id);
REVOKE ALL ON ml.offline_pack_binding_receipt,ml.native_offline_pack_purge_receipt FROM PUBLIC;
ALTER TABLE ml.recommendation_attribution_association
 ALTER COLUMN served_rank DROP NOT NULL,ALTER COLUMN recording_id DROP NOT NULL,
 ALTER COLUMN presentation_id DROP NOT NULL,ALTER COLUMN presentation_source DROP NOT NULL,
 ALTER COLUMN presentation_surface DROP NOT NULL,ALTER COLUMN served_item_sha256 DROP NOT NULL,
 ADD COLUMN offline_pack_id uuid UNIQUE,
 ADD COLUMN pack_served_item_count integer,
 ADD COLUMN pack_served_items_sha256 bytea,
 ADD COLUMN pack_payload_sha256 bytea,
 DROP CONSTRAINT native_attr_kind_check,
 ADD CONSTRAINT native_attr_kind_check CHECK(kind IN ('IMPRESSION','FEEDBACK','LISTEN','OFFLINE_PACK')),
 DROP CONSTRAINT native_attr_target_check,
 ADD CONSTRAINT native_attr_target_check CHECK(
  (kind IN ('IMPRESSION','FEEDBACK') AND interaction_id IS NOT NULL AND listening_event_id IS NULL AND offline_pack_id IS NULL)
  OR (kind='LISTEN' AND interaction_id IS NULL AND listening_event_id IS NOT NULL AND offline_pack_id IS NULL)
  OR (kind='OFFLINE_PACK' AND interaction_id IS NULL AND listening_event_id IS NULL AND offline_pack_id IS NOT NULL)),
 DROP CONSTRAINT native_attr_impression_parent_check,
 ADD CONSTRAINT native_attr_impression_parent_check CHECK(
  (kind IN ('IMPRESSION','OFFLINE_PACK') AND impression_association_id IS NULL AND impression_association_kind IS NULL)
  OR (kind IN ('FEEDBACK','LISTEN') AND impression_association_id IS NOT NULL
   AND impression_association_kind IS NOT NULL AND impression_association_kind='IMPRESSION')),
 ADD CONSTRAINT ck_native_pack_association_shape CHECK(
  (kind='OFFLINE_PACK' AND served_rank IS NULL AND recording_id IS NULL AND presentation_id IS NULL
   AND presentation_source IS NULL AND presentation_surface IS NULL AND served_item_sha256 IS NULL
   AND pack_served_item_count IS NOT NULL AND pack_served_item_count BETWEEN 0 AND 100
   AND pack_served_items_sha256 IS NOT NULL AND octet_length(pack_served_items_sha256)=32
   AND pack_payload_sha256 IS NOT NULL AND octet_length(pack_payload_sha256)=32)
  OR (kind<>'OFFLINE_PACK' AND served_rank IS NOT NULL AND recording_id IS NOT NULL
   AND presentation_id IS NOT NULL AND presentation_source IS NOT NULL AND presentation_surface IS NOT NULL
   AND served_item_sha256 IS NOT NULL AND pack_served_item_count IS NULL
   AND pack_served_items_sha256 IS NULL AND pack_payload_sha256 IS NULL)),
 ADD CONSTRAINT fk_native_pack_association_target FOREIGN KEY(owner_user_id,offline_pack_id)
  REFERENCES ml.offline_recommendation_pack(user_id,offline_pack_id) ON DELETE RESTRICT;
"""

HASH_SQL = r"""
CREATE FUNCTION app_private.native_pack_binding_hash(b ml.offline_pack_binding_receipt)
 RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.native-offline-pack-binding.v2',ARRAY[
 uuid_send(b.pack_id),uuid_send(b.owner_user_id),uuid_send(b.device_id),uuid_send(b.recommendation_request_id),
 int8send(b.serving_contract_version::bigint),b.decision_sha256,int8send(b.served_item_count::bigint),
 b.served_items_sha256,b.pack_payload_sha256,timestamptz_send(b.created_at),
 timestamptz_send(b.content_expires_at),timestamptz_send(b.attribution_expires_at)])) $$;
CREATE FUNCTION app_private.native_pack_association_hash(a ml.recommendation_attribution_association)
 RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.native-offline-pack-association.v2',ARRAY[
 uuid_send(a.association_id),uuid_send(a.owner_user_id),uuid_send(a.recommendation_request_id),
 uuid_send(a.offline_pack_id),convert_to(a.kind,'UTF8'),convert_to(a.serving_provenance,'UTF8'),
 convert_to(a.request_surface,'UTF8'),int8send(a.wire_attribution_version::bigint),
 timestamptz_send(a.attribution_expires_at),a.decision_sha256,int8send(a.pack_served_item_count::bigint),
 a.pack_served_items_sha256,a.pack_payload_sha256,a.original_request_sha256,a.acceptance_sha256])) $$;
CREATE FUNCTION app_private.native_pack_payload_v2(b ml.offline_pack_binding_receipt)
 RETURNS bytea LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT convert_to(jsonb_build_object('payload_version',2,'serving_contract_version',2,
 'offline_pack_id',b.pack_id,'user_id',b.owner_user_id,'device_id',b.device_id,
 'recommendation_request_id',b.recommendation_request_id,
 'decision_sha256',encode(b.decision_sha256,'hex'),'served_item_count',b.served_item_count,
 'served_items_sha256',encode(b.served_items_sha256,'hex'),
 'created_at_ms',floor(extract(epoch FROM b.created_at)*1000)::bigint,
 'expires_at_ms',floor(extract(epoch FROM b.content_expires_at)*1000)::bigint,
 'items',coalesce((SELECT jsonb_agg(jsonb_build_object('source_rank',source_rank,
 'recording_id',recording_id,'score',score_units_e8::numeric/100000000,
 'score_units_e8',score_units_e8,'score_kind',score_kind,'attribution_source',attribution_source,
 'reason_codes',reasons,'contributions',contributions,'original_p11_rank',original_p11_rank,
 'origin',origin,'served_item_sha256',encode(served_item_sha256,'hex')) ORDER BY source_rank)
 FROM ml.recommendation_served_item WHERE recommendation_request_id=b.recommendation_request_id),
 '[]'::jsonb))::text,'UTF8') $$;
"""

VALIDATE_SQL = r"""
CREATE FUNCTION app_private.assert_native_offline_pack_v2(pid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;p ml.offline_recommendation_pack%ROWTYPE;
 a ml.recommendation_attribution_association%ROWTYPE;q ml.recommendation_request%ROWTYPE;
 d ml.recommendation_serving_decision%ROWTYPE;r ml.recommendation_request_replay_payload%ROWTYPE;
BEGIN
 IF pid IS NULL THEN RETURN; END IF;
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=pid;
 SELECT * INTO p FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid;
 SELECT * INTO a FROM ml.recommendation_attribution_association WHERE offline_pack_id=pid;
 IF b.pack_id IS NULL THEN
  IF a.association_id IS NOT NULL OR p.attribution_contract_version=2 THEN
   RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 PERFORM app_private.lock_recommendation_request_v2(b.recommendation_request_id);
 PERFORM app_private.assert_native_served_authority(b.recommendation_request_id);
 SELECT * INTO q FROM ml.recommendation_request WHERE recommendation_request_id=b.recommendation_request_id;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=b.recommendation_request_id;
 SELECT * INTO r FROM ml.recommendation_request_replay_payload WHERE recommendation_request_id=b.recommendation_request_id;
 IF q.serving_contract_version IS DISTINCT FROM 2 OR q.surface IS DISTINCT FROM 'offline_pack'
  OR q.user_id IS DISTINCT FROM b.owner_user_id OR r.device_id IS DISTINCT FROM b.device_id
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR d.serving_provenance IS DISTINCT FROM 'P11'
  OR b.decision_sha256 IS DISTINCT FROM d.decision_sha256
  OR b.served_item_count IS DISTINCT FROM d.served_item_count
  OR b.served_items_sha256 IS DISTINCT FROM d.served_items_sha256
  OR b.attribution_expires_at IS DISTINCT FROM q.created_at+interval '180 days'
  OR b.created_at<q.created_at OR b.binding_sha256 IS DISTINCT FROM app_private.native_pack_binding_hash(b) THEN
  RAISE EXCEPTION 'NATIVE_PACK_BINDING' USING ERRCODE='23514'; END IF;
 IF p.offline_pack_id IS NULL THEN
  IF a.association_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt x
   WHERE x.pack_id=pid AND x.stage='CONTENT' AND x.owner_user_id=b.owner_user_id
    AND x.binding_sha256=b.binding_sha256 AND x.payload_sha256=b.pack_payload_sha256) THEN
   RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 IF p.attribution_contract_version IS DISTINCT FROM 2 OR p.user_id IS DISTINCT FROM b.owner_user_id
  OR p.device_id IS DISTINCT FROM b.device_id OR p.recommendation_request_id IS NOT NULL
  OR p.pipeline_key IS DISTINCT FROM q.pipeline_key OR p.pipeline_version IS DISTINCT FROM q.pipeline_version
  OR p.input_snapshot_sha256 IS DISTINCT FROM q.input_snapshot_sha256
  OR p.catalog_snapshot IS DISTINCT FROM q.catalog_snapshot OR p.model_bundle_version IS DISTINCT FROM q.model_bundle_version
  OR p.created_at IS DISTINCT FROM b.created_at OR p.expires_at IS DISTINCT FROM b.content_expires_at
  OR p.payload_sha256 IS DISTINCT FROM b.pack_payload_sha256 OR p.payload_sha256 IS DISTINCT FROM sha256(p.payload)
  OR p.payload IS DISTINCT FROM app_private.native_pack_payload_v2(b)
  OR a.kind IS DISTINCT FROM 'OFFLINE_PACK' OR a.owner_user_id IS DISTINCT FROM b.owner_user_id
  OR a.recommendation_request_id IS DISTINCT FROM b.recommendation_request_id
  OR a.serving_provenance IS DISTINCT FROM d.serving_provenance OR a.request_surface IS DISTINCT FROM q.surface
  OR a.attribution_expires_at IS DISTINCT FROM b.attribution_expires_at
  OR a.decision_sha256 IS DISTINCT FROM b.decision_sha256 OR a.pack_served_item_count IS DISTINCT FROM b.served_item_count
  OR a.pack_served_items_sha256 IS DISTINCT FROM b.served_items_sha256
  OR a.pack_payload_sha256 IS DISTINCT FROM b.pack_payload_sha256
  OR a.original_request_sha256 IS DISTINCT FROM b.binding_sha256 OR a.acceptance_sha256 IS DISTINCT FROM b.binding_sha256
  OR a.association_sha256 IS DISTINCT FROM app_private.native_pack_association_hash(a) THEN
  RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION app_private.check_native_offline_pack_v2() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE pid uuid;
BEGIN
 pid:=coalesce((to_jsonb(NEW)->>'pack_id')::uuid,(to_jsonb(OLD)->>'pack_id')::uuid,
  (to_jsonb(NEW)->>'offline_pack_id')::uuid,(to_jsonb(OLD)->>'offline_pack_id')::uuid);
 PERFORM app_private.assert_native_offline_pack_v2(pid);RETURN NULL;
END $$;
CREATE FUNCTION app_private.guard_native_offline_pack_v2() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE value jsonb;pid uuid;target_owner uuid;digest bytea;action_name text;
BEGIN
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_TABLE_NAME='recommendation_attribution_association' AND value->>'kind'<>'OFFLINE_PACK' THEN
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END IF;
 IF TG_TABLE_NAME='offline_recommendation_pack' THEN
  IF TG_OP='UPDATE' AND OLD.attribution_contract_version IS DISTINCT FROM NEW.attribution_contract_version THEN
   RAISE EXCEPTION 'NATIVE_PACK_IMMUTABLE' USING ERRCODE='55000'; END IF;
  IF (value->>'attribution_contract_version')::integer=1 THEN
   RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END IF;
 END IF;
 pid:=coalesce(value->>'pack_id',value->>'offline_pack_id')::uuid;
 target_owner:=coalesce(value->>'owner_user_id',value->>'user_id')::uuid;
 IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'NATIVE_PACK_IMMUTABLE' USING ERRCODE='55000';END IF;
 IF TG_TABLE_NAME='native_offline_pack_purge_receipt' THEN
  IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(target_owner) THEN RETURN OLD;END IF;
  action_name:=CASE WHEN TG_OP='DELETE' THEN 'RECEIPT_PURGE'
   WHEN value->>'stage'='CONTENT' THEN 'CONTENT_PURGE' ELSE 'BINDING_PURGE' END;
 ELSE
  action_name:=CASE WHEN TG_OP='INSERT' THEN 'CREATE'
   WHEN TG_TABLE_NAME='offline_pack_binding_receipt' THEN 'BINDING_PURGE' ELSE 'CONTENT_PURGE' END;
 END IF;
 SELECT b.binding_sha256 INTO digest FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=pid;
 IF digest IS NULL THEN digest:=decode(substr(value->>'binding_sha256',3),'hex');END IF;
 IF TG_OP='DELETE' AND TG_TABLE_NAME='native_offline_pack_purge_receipt'
  AND (clock_timestamp()<(value->>'retain_until')::timestamptz
   OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt WHERE pack_id=pid)) THEN
  RAISE EXCEPTION 'NATIVE_PACK_PURGE_RECEIPT_IMMUTABLE' USING ERRCODE='55000';END IF;
 IF NOT EXISTS(SELECT 1 FROM app_private.native_pack_context c WHERE c.transaction_id=pg_current_xact_id()
  AND c.pack_id=pid AND c.owner_id=target_owner AND c.action=action_name AND c.content_sha256=digest) THEN
  RAISE EXCEPTION 'NATIVE_PACK_WRITER_REQUIRED' USING ERRCODE='55000';END IF;
 IF TG_OP='INSERT' AND TG_TABLE_NAME='recommendation_attribution_association' THEN
  PERFORM 1 FROM ml.offline_recommendation_pack WHERE user_id=target_owner AND offline_pack_id=pid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_PACK_TARGET_REQUIRED' USING ERRCODE='23514';END IF;
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE FUNCTION app_private.purge_native_offline_pack_receipts_v2(owner_id uuid,p_limit integer DEFAULT 256)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.native_offline_pack_purge_receipt%ROWTYPE;removed integer:=0;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 256 THEN
  RAISE EXCEPTION 'NATIVE_PACK_PURGE_BOUND' USING ERRCODE='22023';END IF;
 PERFORM app_private.lock_native_served_server();
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 FOR r IN SELECT * FROM ml.native_offline_pack_purge_receipt x WHERE x.owner_user_id=owner_id
  AND x.retain_until<=clock_timestamp() AND x.purged_at<transaction_timestamp()
  AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=x.pack_id)
  ORDER BY x.retain_until,x.pack_id,x.stage LIMIT p_limit FOR UPDATE LOOP
  INSERT INTO app_private.native_pack_context VALUES(pg_current_xact_id(),r.pack_id,owner_id,'RECEIPT_PURGE',r.binding_sha256);
  DELETE FROM ml.native_offline_pack_purge_receipt WHERE pack_id=r.pack_id AND stage=r.stage;
  DELETE FROM app_private.native_pack_context WHERE transaction_id=pg_current_xact_id() AND pack_id=r.pack_id;
  removed:=removed+1;
 END LOOP;
 RETURN removed;
END $$;
"""

PURGE_SQL = r"""
CREATE FUNCTION app_private.purge_native_offline_pack_v2(owner_id uuid,pid uuid,p_reason text)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;a ml.recommendation_attribution_association%ROWTYPE;
 actual_time timestamptz;association_hash bytea;
BEGIN
 IF p_reason IS NULL OR p_reason NOT IN ('PACK_EXPIRED','SERVING_TRUTH_EXPIRED','ACCOUNT_DELETION') THEN
  RAISE EXCEPTION 'NATIVE_PACK_PURGE_REASON' USING ERRCODE='22023';END IF;
 PERFORM app_private.lock_native_served_server();
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE owner_user_id=owner_id AND pack_id=pid;
 IF b.pack_id IS NULL THEN RETURN false;END IF;
 PERFORM app_private.lock_recommendation_request_v2(b.recommendation_request_id);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=pid AND owner_user_id=owner_id FOR UPDATE;
 IF b.pack_id IS NULL THEN RETURN false;END IF;
 PERFORM 1 FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid FOR UPDATE;
 PERFORM app_private.assert_native_offline_pack_v2(pid);
 actual_time:=clock_timestamp();
 IF (p_reason='PACK_EXPIRED' AND actual_time<b.content_expires_at)
  OR (p_reason='SERVING_TRUTH_EXPIRED' AND actual_time<b.attribution_expires_at)
  OR (p_reason='ACCOUNT_DELETION' AND NOT app_private.privacy_owner_allowed(owner_id)) THEN
  RAISE EXCEPTION 'NATIVE_PACK_PURGE_AUTHORITY' USING ERRCODE='55000';END IF;
 SELECT * INTO a FROM ml.recommendation_attribution_association WHERE offline_pack_id=pid FOR UPDATE;
 IF a.association_id IS NOT NULL THEN
  INSERT INTO app_private.native_pack_context VALUES(pg_current_xact_id(),pid,owner_id,'CONTENT_PURGE',b.binding_sha256);
  INSERT INTO ml.native_offline_pack_purge_receipt VALUES(pid,'CONTENT',owner_id,b.binding_sha256,
   a.association_sha256,b.pack_payload_sha256,p_reason,actual_time,b.attribution_expires_at+interval '220 days');
  -- The RESTRICT target FK forces association-before-payload deletion.
  DELETE FROM ml.recommendation_attribution_association WHERE offline_pack_id=pid;
  DELETE FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid;
  DELETE FROM app_private.native_pack_context WHERE transaction_id=pg_current_xact_id() AND pack_id=pid;
 END IF;
 IF p_reason<>'PACK_EXPIRED' THEN
  SELECT association_sha256 INTO association_hash FROM ml.native_offline_pack_purge_receipt
   WHERE pack_id=pid AND stage='CONTENT' AND binding_sha256=b.binding_sha256;
  IF association_hash IS NULL THEN RAISE EXCEPTION 'NATIVE_PACK_PURGE_INCOMPLETE' USING ERRCODE='23514';END IF;
  INSERT INTO app_private.native_pack_context VALUES(pg_current_xact_id(),pid,owner_id,'BINDING_PURGE',b.binding_sha256);
  INSERT INTO ml.native_offline_pack_purge_receipt VALUES(pid,'BINDING',owner_id,b.binding_sha256,
   association_hash,b.pack_payload_sha256,p_reason,actual_time,b.attribution_expires_at+interval '220 days');
  DELETE FROM ml.offline_pack_binding_receipt WHERE pack_id=pid;
  DELETE FROM app_private.native_pack_context WHERE transaction_id=pg_current_xact_id() AND pack_id=pid;
 END IF;
 RETURN true;
END $$;
CREATE FUNCTION app_private.purge_native_offline_request_v2(owner_id uuid,rid uuid,p_reason text,p_limit integer DEFAULT 256)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE pid uuid;removed integer:=0;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 256 THEN
  RAISE EXCEPTION 'NATIVE_PACK_PURGE_BOUND' USING ERRCODE='22023';END IF;
 PERFORM app_private.lock_native_served_server();
 PERFORM app_private.lock_native_attribution_owner(owner_id);
 PERFORM app_private.lock_recommendation_request_v2(rid);
 FOR pid IN SELECT pack_id FROM ml.offline_pack_binding_receipt WHERE owner_user_id=owner_id
  AND recommendation_request_id=rid ORDER BY pack_id LIMIT p_limit LOOP
  IF app_private.purge_native_offline_pack_v2(owner_id,pid,p_reason) THEN removed:=removed+1;END IF;
 END LOOP;
 RETURN removed;
END $$;
"""

WRITE_SQL = r"""
CREATE FUNCTION app_private.create_native_offline_pack_v2(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,p_request uuid,p_pack uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;a ml.recommendation_attribution_association%ROWTYPE;
 q ml.recommendation_request%ROWTYPE;d ml.recommendation_serving_decision%ROWTYPE;
 r ml.recommendation_request_replay_payload%ROWTYPE;body bytea;actual_time timestamptz;
BEGIN
 IF p_request IS NULL OR p_pack IS NULL THEN RAISE EXCEPTION 'NATIVE_PACK_COMMAND' USING ERRCODE='22023';END IF;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_epoch,p_server,p_identity);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack;
 IF b.pack_id IS NOT NULL THEN
  IF b.owner_user_id IS DISTINCT FROM p_owner OR b.device_id IS DISTINCT FROM p_device
   OR b.recommendation_request_id IS DISTINCT FROM p_request THEN
   RAISE EXCEPTION 'NATIVE_PACK_RETRY_CONFLICT' USING ERRCODE='23514';END IF;
  PERFORM app_private.assert_native_offline_pack_v2(p_pack);
  IF clock_timestamp()>=b.attribution_expires_at THEN RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
  RETURN jsonb_build_object('status',CASE WHEN clock_timestamp()>=b.content_expires_at THEN 'PACK_EXPIRED' ELSE 'COMMITTED' END,
   'pack_id',p_pack,'binding_sha256',encode(b.binding_sha256,'hex'),'replayed',true);
 END IF;
 IF EXISTS(SELECT 1 FROM ml.offline_recommendation_pack WHERE offline_pack_id=p_pack)
  OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt WHERE pack_id=p_pack) THEN
  RAISE EXCEPTION 'NATIVE_PACK_ID_REUSED' USING ERRCODE='23514';END IF;
 SELECT * INTO q FROM ml.recommendation_request WHERE recommendation_request_id=p_request AND user_id=p_owner;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 SELECT * INTO r FROM ml.recommendation_request_replay_payload WHERE recommendation_request_id=p_request;
 IF q.serving_contract_version IS DISTINCT FROM 2 OR q.surface IS DISTINCT FROM 'offline_pack'
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR r.device_id IS DISTINCT FROM p_device
  OR r.journal_epoch IS DISTINCT FROM p_epoch OR r.server_instance_id IS DISTINCT FROM p_server
  OR r.identity_epoch IS DISTINCT FROM p_identity THEN
  RAISE EXCEPTION 'NATIVE_PACK_REQUEST_BINDING' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request
  AND xmin::text=pg_current_xact_id()::text) THEN
  RAISE EXCEPTION 'NATIVE_PACK_COMMITTED_DECISION_REQUIRED' USING ERRCODE='23514';END IF;
 PERFORM app_private.assert_native_served_authority(p_request);
 actual_time:=clock_timestamp();
 b.pack_id:=p_pack;b.owner_user_id:=p_owner;b.device_id:=p_device;b.recommendation_request_id:=p_request;
 b.serving_contract_version:=2;b.decision_sha256:=d.decision_sha256;b.served_item_count:=d.served_item_count;
 b.served_items_sha256:=d.served_items_sha256;b.created_at:=actual_time;
 b.attribution_expires_at:=q.created_at+interval '180 days';
 b.content_expires_at:=least(actual_time+interval '7 days',b.attribution_expires_at);
 body:=app_private.native_pack_payload_v2(b);b.pack_payload_sha256:=sha256(body);
 b.binding_sha256:=app_private.native_pack_binding_hash(b);
 IF octet_length(body)>2097152 THEN RAISE EXCEPTION 'NATIVE_PACK_PAYLOAD_BOUND' USING ERRCODE='22023';END IF;
 -- PostgreSQL time under the shared request lock is the creation linearization point.
 IF clock_timestamp()>=b.attribution_expires_at THEN
  PERFORM app_private.ensure_native_served_expiry(p_owner,p_request);
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 INSERT INTO app_private.native_pack_context VALUES(pg_current_xact_id(),p_pack,p_owner,'CREATE',b.binding_sha256);
 INSERT INTO ml.offline_pack_binding_receipt SELECT (b).*;
 INSERT INTO ml.offline_recommendation_pack(offline_pack_id,user_id,device_id,pipeline_key,pipeline_version,
  input_snapshot_sha256,catalog_snapshot,model_bundle_version,payload_version,payload_encoding,payload,
  payload_sha256,created_at,expires_at,attribution_contract_version)
 VALUES(p_pack,p_owner,p_device,q.pipeline_key,q.pipeline_version,q.input_snapshot_sha256,q.catalog_snapshot,
  q.model_bundle_version,2,'RAW_JSON',body,b.pack_payload_sha256,b.created_at,b.content_expires_at,2);
 a.association_id:=uuidv7();a.owner_user_id:=p_owner;a.recommendation_request_id:=p_request;a.kind:='OFFLINE_PACK';
 a.serving_provenance:='P11';a.request_surface:=q.surface;a.wire_attribution_version:=1;
 a.offline_pack_id:=p_pack;a.attribution_expires_at:=b.attribution_expires_at;
 a.original_request_sha256:=b.binding_sha256;a.acceptance_sha256:=b.binding_sha256;
 a.decision_sha256:=b.decision_sha256;a.pack_served_item_count:=b.served_item_count;
 a.pack_served_items_sha256:=b.served_items_sha256;a.pack_payload_sha256:=b.pack_payload_sha256;
 a.association_sha256:=app_private.native_pack_association_hash(a);
 INSERT INTO ml.recommendation_attribution_association SELECT (a).*;
 DELETE FROM app_private.native_pack_context WHERE transaction_id=pg_current_xact_id() AND pack_id=p_pack;
 PERFORM app_private.assert_native_offline_pack_v2(p_pack);
 RETURN jsonb_build_object('status','COMMITTED','pack_id',p_pack,
  'binding_sha256',encode(b.binding_sha256,'hex'),'replayed',false);
END $$;
CREATE FUNCTION app_private.read_native_offline_pack_v2(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,p_pack uuid,p_payload boolean)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;body bytea;
BEGIN
 IF p_payload IS NULL THEN RAISE EXCEPTION 'NATIVE_PACK_COMMAND' USING ERRCODE='22023';END IF;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_epoch,p_server,p_identity);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack AND owner_user_id=p_owner AND device_id=p_device;
 IF b.pack_id IS NULL THEN
  RETURN jsonb_build_object('status',CASE WHEN EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt
   WHERE pack_id=p_pack AND owner_user_id=p_owner AND stage='BINDING') THEN 'ATTRIBUTION_EXPIRED' ELSE 'ATTRIBUTION_NOT_FOUND' END);
 END IF;
 PERFORM app_private.lock_recommendation_request_v2(b.recommendation_request_id);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack AND owner_user_id=p_owner AND device_id=p_device;
 IF b.pack_id IS NULL THEN RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 PERFORM app_private.assert_native_offline_pack_v2(p_pack);
 SELECT payload INTO body FROM ml.offline_recommendation_pack WHERE offline_pack_id=p_pack;
 -- The final locked SQL-clock observation linearizes both payload and receipt reads.
 IF clock_timestamp()>=b.attribution_expires_at THEN
  PERFORM app_private.ensure_native_served_expiry(p_owner,b.recommendation_request_id);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'SERVING_TRUTH_EXPIRED');
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 IF p_payload AND clock_timestamp()>=b.content_expires_at THEN
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'PACK_EXPIRED');
  RETURN jsonb_build_object('status','PACK_EXPIRED');END IF;
 RETURN jsonb_build_object('status','COMMITTED','pack_id',p_pack,'request_id',b.recommendation_request_id,
  'serving_contract_version',2,'decision_sha256',encode(b.decision_sha256,'hex'),
  'served_item_count',b.served_item_count,'served_items_sha256',encode(b.served_items_sha256,'hex'),
  'pack_payload_sha256',encode(b.pack_payload_sha256,'hex'),'binding_sha256',encode(b.binding_sha256,'hex'),
  'content_expires_at',b.content_expires_at,'attribution_expires_at',b.attribution_expires_at)
  ||CASE WHEN p_payload THEN jsonb_build_object('payload_b64',encode(body,'base64')) ELSE '{}'::jsonb END;
END $$;
"""

FUNCTIONS = (
    "native_pack_binding_hash(ml.offline_pack_binding_receipt)",
    "native_pack_association_hash(ml.recommendation_attribution_association)",
    "native_pack_payload_v2(ml.offline_pack_binding_receipt)",
    "assert_native_offline_pack_v2(uuid)",
    "check_native_offline_pack_v2()",
    "guard_native_offline_pack_v2()",
    "purge_native_offline_pack_receipts_v2(uuid,integer)",
    "purge_native_offline_pack_v2(uuid,uuid,text)",
    "purge_native_offline_request_v2(uuid,uuid,text,integer)",
    "create_native_offline_pack_v2(uuid,uuid,uuid,uuid,bigint,uuid,uuid)",
    "read_native_offline_pack_v2(uuid,uuid,uuid,uuid,bigint,uuid,boolean)",
)


def install(connection: Connection) -> None:
    """Install the dormant boundary, extending only exact installed predecessors."""
    connection.execute(text(SCHEMA_SQL))
    prior: dict[str, str] = {}
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_pack_prior_function_missing")
        prior[signature] = definition
        connection.execute(
            text("INSERT INTO app_private.native_pack_prior_definition VALUES(:name,:ddl)"),
            {"name": signature, "ddl": definition},
        )
    for statements in (HASH_SQL, VALIDATE_SQL, PURGE_SQL, WRITE_SQL):
        connection.execute(text(statements))
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[0]],
                "BEGIN\n",
                "BEGIN\n IF coalesce(to_jsonb(NEW)->>'kind',to_jsonb(OLD)->>'kind')='OFFLINE_PACK' THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END IF;\n",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[1]],
                "BEGIN\n",
                "BEGIN\n IF TG_TABLE_NAME='recommendation_attribution_association' AND coalesce(to_jsonb(NEW)->>'kind',to_jsonb(OLD)->>'kind')='OFFLINE_PACK' THEN RETURN NULL;END IF;\n",
            )
        )
    )
    # Existing event purge retains its ordering and bounded budget; pack lifecycle
    # is a separate request-level operation, also bounded, before parent deletion.
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[2]],
                "WHERE owner_user_id=owner_id AND recommendation_request_id=request_id\n",
                "WHERE owner_user_id=owner_id AND recommendation_request_id=request_id AND kind<>'OFFLINE_PACK'\n",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[3]],
                " IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN\n",
                " PERFORM app_private.purge_native_offline_request_v2(p_owner,p_request,'SERVING_TRUTH_EXPIRED',256);\n IF EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt WHERE recommendation_request_id=p_request) THEN RETURN jsonb_build_object('status','PENDING');END IF;\n IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN\n",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[4]],
                "   IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN\n",
                "   WHILE EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt WHERE recommendation_request_id=request_id) LOOP\n    PERFORM app_private.purge_native_offline_request_v2(target,request_id,'ACCOUNT_DELETION',256);\n   END LOOP;\n   IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN\n",
            )
        )
    )
    for table in (
        "offline_recommendation_pack",
        "offline_pack_binding_receipt",
        "recommendation_attribution_association",
        "native_offline_pack_purge_receipt",
    ):
        connection.execute(
            text(
                f"CREATE TRIGGER guard_native_pack BEFORE INSERT OR UPDATE OR DELETE ON ml.{table} FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_offline_pack_v2()"
            )
        )
    for table in (
        "offline_recommendation_pack",
        "offline_pack_binding_receipt",
        "recommendation_attribution_association",
    ):
        connection.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER check_native_pack AFTER INSERT OR UPDATE OR DELETE ON ml.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.check_native_offline_pack_v2()"
            )
        )
    for signature in FUNCTIONS:
        connection.execute(text(f"REVOKE ALL ON FUNCTION app_private.{signature} FROM PUBLIC"))


def remove(connection: Connection) -> None:
    """Fail closed on downgrade once any v2 pack evidence has been created."""
    connection.execute(
        text("""
LOCK TABLE ml.offline_pack_binding_receipt,ml.native_offline_pack_purge_receipt,
 ml.offline_recommendation_pack,ml.recommendation_attribution_association IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt)
  OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt)
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association WHERE kind='OFFLINE_PACK')
  OR EXISTS(SELECT 1 FROM ml.offline_recommendation_pack WHERE attribution_contract_version=2) THEN
  RAISE EXCEPTION 'refusing native offline pack downgrade with evidence' USING ERRCODE='55000';END IF;
END $$;
""")
    )
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT ddl FROM app_private.native_pack_prior_definition WHERE name=:name"),
            {"name": signature},
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_pack_prior_function_missing")
        connection.execute(text(definition))
    for table in (
        "offline_recommendation_pack",
        "offline_pack_binding_receipt",
        "recommendation_attribution_association",
        "native_offline_pack_purge_receipt",
    ):
        connection.execute(text(f"DROP TRIGGER guard_native_pack ON ml.{table}"))
    for table in (
        "offline_recommendation_pack",
        "offline_pack_binding_receipt",
        "recommendation_attribution_association",
    ):
        connection.execute(text(f"DROP TRIGGER check_native_pack ON ml.{table}"))
    for signature in reversed(FUNCTIONS):
        connection.execute(text(f"DROP FUNCTION app_private.{signature}"))
    connection.execute(
        text("""
ALTER TABLE ml.recommendation_attribution_association
 DROP CONSTRAINT fk_native_pack_association_target,DROP CONSTRAINT ck_native_pack_association_shape,
 DROP CONSTRAINT native_attr_kind_check,ADD CONSTRAINT native_attr_kind_check CHECK(kind IN ('IMPRESSION','FEEDBACK','LISTEN')),
 DROP CONSTRAINT native_attr_target_check,ADD CONSTRAINT native_attr_target_check CHECK(
  (kind IN ('IMPRESSION','FEEDBACK') AND interaction_id IS NOT NULL AND listening_event_id IS NULL)
  OR (kind='LISTEN' AND interaction_id IS NULL AND listening_event_id IS NOT NULL)),
 DROP CONSTRAINT native_attr_impression_parent_check,ADD CONSTRAINT native_attr_impression_parent_check CHECK(
  (kind='IMPRESSION' AND impression_association_id IS NULL AND impression_association_kind IS NULL)
  OR (kind IN ('FEEDBACK','LISTEN') AND impression_association_id IS NOT NULL AND impression_association_kind IS NOT NULL AND impression_association_kind='IMPRESSION')),
 ALTER COLUMN served_rank SET NOT NULL,ALTER COLUMN recording_id SET NOT NULL,
 ALTER COLUMN presentation_id SET NOT NULL,ALTER COLUMN presentation_source SET NOT NULL,
 ALTER COLUMN presentation_surface SET NOT NULL,ALTER COLUMN served_item_sha256 SET NOT NULL,
 DROP COLUMN offline_pack_id,DROP COLUMN pack_served_item_count,DROP COLUMN pack_served_items_sha256,DROP COLUMN pack_payload_sha256;
DROP TABLE ml.native_offline_pack_purge_receipt;
DROP TABLE ml.offline_pack_binding_receipt;
ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT ck_native_pack_version,
 DROP CONSTRAINT ck_native_pack_v2,DROP CONSTRAINT uq_native_pack_owner,DROP COLUMN attribution_contract_version;
DROP TABLE app_private.native_pack_context;
DROP TABLE app_private.native_pack_prior_definition;
""")
    )
