"""Dormant normalized ingress integrity foundation; no serving cutover.

SYNC_PAYLOAD_PG_V1 is UTF8('SYNC_PAYLOAD_PG_V1') || 00 || UTF8(payload::text).
Only the bounded flat noncausal payload subset is supported. A codec change
requires a new version/migration/golden vectors. Wire event schema remains v1.
"""

from alembic import op

revision = "0087_normalized_sync_ingress"
down_revision = "0086_face_owner_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE sync.sync_event
 ADD COLUMN ingress_validation_version integer NOT NULL DEFAULT 1,
 ADD COLUMN ingress_device_sequence bigint,
 ADD COLUMN ingress_journal_epoch uuid,
 ADD COLUMN normalized_payload_sha256 bytea,
 ADD CONSTRAINT ck_sync_event_ingress_version CHECK(ingress_validation_version IN (1,2)),
 ADD CONSTRAINT ck_sync_event_normalized_ingress CHECK(
  (ingress_validation_version=1 AND ingress_device_sequence IS NULL
   AND ingress_journal_epoch IS NULL AND normalized_payload_sha256 IS NULL) OR
  (ingress_validation_version=2 AND origin_device_id IS NOT NULL
   AND ingress_device_sequence IS NOT NULL AND ingress_device_sequence BETWEEN 1 AND
 9007199254740991
   AND ingress_journal_epoch IS NOT NULL AND normalized_payload_sha256 IS NOT NULL
   AND octet_length(normalized_payload_sha256)=32 AND schema_version=1
   AND operation='UPSERT' AND server_row_version IS NOT NULL AND server_row_version=1));
ALTER TABLE sync.device_event_inbox
 ADD COLUMN ingress_validation_version integer NOT NULL DEFAULT 1,
 ADD COLUMN normalized_payload_sha256 bytea,
 ADD CONSTRAINT ck_inbox_ingress_version CHECK(ingress_validation_version IN (1,2)),
 ADD CONSTRAINT ck_inbox_normalized_ingress CHECK(
  (ingress_validation_version=1 AND normalized_payload_sha256 IS NULL) OR
  (ingress_validation_version=2 AND normalized_payload_sha256 IS NOT NULL
   AND octet_length(normalized_payload_sha256)=32 AND payload='{}'::jsonb
   AND schema_version=1 AND apply_status='RECEIVED' AND terminal_ack IS NULL
   AND error_code IS NULL AND device_sequence BETWEEN 1 AND 9007199254740991));

-- Unlike a user-settable GUC, this transient capability is inaccessible to DML
-- callers. Only the revoked-by-default SECURITY DEFINER writer creates it.
CREATE TABLE app_private.normalized_ingress_write_context (
 transaction_id xid8 NOT NULL, event_id uuid NOT NULL,
 PRIMARY KEY(transaction_id,event_id));
REVOKE ALL ON app_private.normalized_ingress_write_context FROM PUBLIC;

CREATE FUNCTION app_private.sync_payload_pg_v1(p jsonb) RETURNS bytea
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v jsonb;
BEGIN
 IF p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>8192
  OR (SELECT count(*) FROM jsonb_object_keys(p))>16 THEN
  RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
 END IF;
 FOR k,v IN SELECT * FROM jsonb_each(p) LOOP
  IF length(k)>100 OR jsonb_typeof(v) NOT IN ('null','boolean','number','string')
   OR (jsonb_typeof(v)='string' AND length(v#>>'{}')>200) THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(v)='number' AND NOT (
   ((v#>>'{}')::numeric=trunc((v#>>'{}')::numeric)
    AND scale((v#>>'{}')::numeric)=0
    AND (v#>>'{}')::numeric BETWEEN -9223372036854775808 AND 9223372036854775807)
   OR (abs((v#>>'{}')::numeric)<=1 AND scale((v#>>'{}')::numeric)=6)) THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END LOOP;
 RETURN convert_to('SYNC_PAYLOAD_PG_V1','UTF8')||decode('00','hex')||convert_to(p::text,'UTF8');
END $$;
CREATE FUNCTION app_private.sync_payload_sha256_pg_v1(p jsonb) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.sync_payload_pg_v1(p)) $$;

CREATE FUNCTION app_private.validate_normalized_sync_payload_v2(kind text,p jsonb)
 RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE expected text[]; actual text[]; k text; value text; n numeric;
BEGIN
 PERFORM app_private.sync_payload_pg_v1(p);
 IF kind IS NULL OR p->>'interaction_type' IS DISTINCT FROM kind THEN
  RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
 END IF;
 CASE kind
 WHEN 'LISTENING_EVENT_RECORDED' THEN expected:=ARRAY[
  'interaction_type','local_user_track_ref_id','server_user_track_ref_id','recording_id',
  'played_ms','track_duration_ms','completion_ratio','event_origin','context',
  'explicit_feedback','excluded_from_taste'];
 WHEN 'RECOMMENDATION_IMPRESSION_RECORDED' THEN expected:=ARRAY['interaction_type'];
 WHEN 'RECOMMENDATION_FEEDBACK_RECORDED' THEN expected:=ARRAY['interaction_type','feedback_type'];
 ELSE RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
 END CASE;
 SELECT array_agg(x ORDER BY x COLLATE "C") INTO actual FROM jsonb_object_keys(p) x;
 SELECT array_agg(x ORDER BY x COLLATE "C") INTO expected FROM unnest(expected) x;
 IF actual IS DISTINCT FROM expected THEN
  RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
 END IF;
 IF kind='RECOMMENDATION_FEEDBACK_RECORDED' THEN
  IF jsonb_typeof(p->'feedback_type')<>'string'
   OR (p->>'feedback_type') !~ '^[A-Z][A-Z0-9_]{0,63}$' THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END IF;
 IF kind<>'LISTENING_EVENT_RECORDED' THEN RETURN; END IF;
 FOREACH k IN ARRAY ARRAY['local_user_track_ref_id','server_user_track_ref_id','recording_id'] LOOP
  IF p->k='null'::jsonb AND k<>'local_user_track_ref_id' THEN CONTINUE; END IF;
  IF jsonb_typeof(p->k)<>'string'
   OR (p->>k) !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['played_ms','track_duration_ms'] LOOP
  IF p->k='null'::jsonb AND k='track_duration_ms' THEN CONTINUE; END IF;
  IF jsonb_typeof(p->k)<>'number' THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
  n:=(p->>k)::numeric;
  IF scale(n)<>0 OR n<(CASE WHEN k='played_ms' THEN 0 ELSE 1 END) OR n>604800000 THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END LOOP;
 IF p->'completion_ratio'<>'null'::jsonb THEN
  IF jsonb_typeof(p->'completion_ratio')<>'number' THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
  n:=(p->>'completion_ratio')::numeric;
  IF n<0 OR n>1 OR scale(n)<>6 THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END IF;
 FOREACH k IN ARRAY ARRAY['event_origin','context','explicit_feedback'] LOOP
  value:=p->>k;
  IF jsonb_typeof(p->k)<>'string' OR value !~ '^[A-Z][A-Z0-9_]*$'
   OR length(value)>(CASE WHEN k='event_origin' THEN 100 ELSE 50 END) THEN
   RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
  END IF;
 END LOOP;
 IF jsonb_typeof(p->'excluded_from_taste')<>'boolean' THEN
  RAISE EXCEPTION 'invalid_normalized_payload' USING ERRCODE='22023';
 END IF;
END $$;

CREATE FUNCTION app_private.guard_normalized_sync_ingress() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  IF OLD.ingress_validation_version=2 AND NOT
   app_private.privacy_owner_allowed(OLD.user_id) THEN
   RAISE EXCEPTION 'normalized_ingress_privacy_authority_required' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' THEN
  IF NEW.ingress_validation_version IS DISTINCT FROM OLD.ingress_validation_version
   OR NEW.normalized_payload_sha256 IS DISTINCT FROM OLD.normalized_payload_sha256
   OR (TG_TABLE_NAME='sync_event' AND
    (to_jsonb(NEW)->'ingress_device_sequence' IS DISTINCT FROM
 to_jsonb(OLD)->'ingress_device_sequence'
     OR to_jsonb(NEW)->'ingress_journal_epoch' IS DISTINCT FROM
 to_jsonb(OLD)->'ingress_journal_epoch'))
   OR (OLD.ingress_validation_version=2 AND NEW IS DISTINCT FROM OLD) THEN
   RAISE EXCEPTION 'normalized_ingress_immutable' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.ingress_validation_version=2 THEN
  IF TG_OP='INSERT' AND NOT EXISTS(
   SELECT 1 FROM app_private.normalized_ingress_write_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id) THEN
   RAISE EXCEPTION 'normalized_ingress_writer_required' USING ERRCODE='55000';
  END IF;
  IF TG_TABLE_NAME='sync_event' THEN
   PERFORM app_private.validate_normalized_sync_payload_v2(NEW.event_type,NEW.payload);
   IF app_private.sync_payload_sha256_pg_v1(NEW.payload) IS DISTINCT FROM
 NEW.normalized_payload_sha256 THEN
    RAISE EXCEPTION 'normalized_ingress_digest_mismatch' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_normalized_sync_ingress BEFORE INSERT OR UPDATE OR DELETE ON sync.sync_event
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_sync_ingress();
CREATE TRIGGER guard_normalized_inbox_ingress BEFORE INSERT OR UPDATE OR DELETE ON
 sync.device_event_inbox
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_normalized_sync_ingress();

CREATE FUNCTION app_private.check_normalized_sync_ingress_pair() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE eid uuid; s sync.sync_event%ROWTYPE; i sync.device_event_inbox%ROWTYPE;
BEGIN
 eid:=CASE WHEN TG_OP='DELETE' THEN OLD.event_id ELSE NEW.event_id END;
 -- Lock both rows in the same order on either trigger. No snapshot-only check.
 SELECT * INTO s FROM sync.sync_event WHERE event_id=eid FOR UPDATE;
 SELECT * INTO i FROM sync.device_event_inbox WHERE event_id=eid FOR UPDATE;
 IF s.ingress_validation_version IS DISTINCT FROM 2 AND i.ingress_validation_version IS DISTINCT
 FROM 2 THEN
  RETURN NULL;
 END IF;
 IF s.ingress_validation_version IS DISTINCT FROM 2 OR i.ingress_validation_version IS DISTINCT
 FROM 2
  OR s.event_id IS DISTINCT FROM i.event_id OR s.user_id IS DISTINCT FROM i.user_id
  OR s.origin_device_id IS DISTINCT FROM i.device_id OR s.event_type IS DISTINCT FROM i.event_type
  OR s.schema_version IS DISTINCT FROM i.schema_version OR s.aggregate_type IS DISTINCT FROM
 i.aggregate_type
  OR s.aggregate_id IS DISTINCT FROM i.aggregate_id OR s.ingress_device_sequence IS DISTINCT FROM
 i.device_sequence
  OR s.ingress_journal_epoch IS DISTINCT FROM i.journal_epoch
  OR s.normalized_payload_sha256 IS DISTINCT FROM i.normalized_payload_sha256
  OR s.normalized_payload_sha256 IS DISTINCT FROM
 app_private.sync_payload_sha256_pg_v1(s.payload) THEN
  RAISE EXCEPTION 'normalized_ingress_pair_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_normalized_sync_ingress_pair AFTER INSERT OR UPDATE OR DELETE ON
 sync.sync_event
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
 app_private.check_normalized_sync_ingress_pair();
CREATE CONSTRAINT TRIGGER check_normalized_inbox_ingress_pair AFTER INSERT OR UPDATE OR DELETE ON
 sync.device_event_inbox
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
 app_private.check_normalized_sync_ingress_pair();

CREATE FUNCTION app_private.stage_normalized_sync_ingress_v2(
 p_eid uuid, p_owner_id uuid, p_device uuid, p_epoch uuid, p_device_seq bigint,
 p_kind text, p_wire_schema integer, p_aggregate text, p_aggregate_uuid uuid,
 p_happened_at timestamptz, p_original_hash bytea, p_normalized jsonb, p_validation_version integer)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result bigint; digest bytea; cursor_seq bigint;
BEGIN
 IF p_eid IS NULL OR p_owner_id IS NULL OR p_device IS NULL OR p_epoch IS NULL OR p_happened_at
 IS NULL
  OR NOT isfinite(p_happened_at)
  OR p_device_seq IS NULL OR p_device_seq NOT BETWEEN 1 AND 9007199254740991
  OR p_validation_version IS DISTINCT FROM 2 OR p_wire_schema IS DISTINCT FROM 1
  OR p_original_hash IS NULL OR octet_length(p_original_hash)<>32
  OR p_aggregate_uuid IS DISTINCT FROM p_eid
  OR p_aggregate IS DISTINCT FROM (CASE WHEN p_kind='LISTENING_EVENT_RECORDED'
   THEN 'LISTENING_EVENT' ELSE 'USER_INTERACTION_EVENT' END) THEN
  RAISE EXCEPTION 'invalid_normalized_ingress' USING ERRCODE='22023';
 END IF;
 PERFORM app_private.validate_normalized_sync_payload_v2(p_kind,p_normalized);
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner_id AND status='ACTIVE' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'normalized_ingress_binding_mismatch' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM account.device WHERE device_id=p_device AND user_id=p_owner_id AND revoked_at IS
 NULL
 FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'normalized_ingress_binding_mismatch' USING ERRCODE='23514';
 END IF;
 SELECT last_acked_device_sequence INTO cursor_seq FROM sync.device_sync_cursor
  WHERE device_id=p_device AND user_id=p_owner_id AND journal_epoch=p_epoch FOR UPDATE;
 IF NOT FOUND OR p_device_seq<=cursor_seq OR EXISTS(
  SELECT 1 FROM sync.device_erasure_invalidated_epoch WHERE device_id=p_device AND
 journal_epoch=p_epoch) THEN
  RAISE EXCEPTION 'normalized_ingress_binding_mismatch' USING ERRCODE='23514';
 END IF;
 digest:=app_private.sync_payload_sha256_pg_v1(p_normalized);
 INSERT INTO app_private.normalized_ingress_write_context VALUES(pg_current_xact_id(),p_eid);
 INSERT INTO sync.device_event_inbox(event_id,user_id,device_id,journal_epoch,device_sequence,
  event_type,schema_version,aggregate_type,aggregate_id,aggregate_local_id,payload,occurred_at,
  request_hash,ingress_validation_version,normalized_payload_sha256)
 VALUES(p_eid,p_owner_id,p_device,p_epoch,p_device_seq,p_kind,p_wire_schema,p_aggregate,p_eid,p_eid,'{}',p_happened_at,
  p_original_hash,2,digest);
 INSERT INTO sync.sync_event(event_id,user_id,origin_device_id,event_type,schema_version,
  aggregate_type,aggregate_id,payload,operation,server_row_version,ingress_validation_version,
  ingress_device_sequence,ingress_journal_epoch,normalized_payload_sha256)
 VALUES(p_eid,p_owner_id,p_device,p_kind,p_wire_schema,p_aggregate,p_eid,p_normalized,'UPSERT',1,2,p_device_seq,p_epoch,digest)
 RETURNING server_sequence INTO result;
 DELETE FROM app_private.normalized_ingress_write_context
  WHERE transaction_id=pg_current_xact_id() AND event_id=p_eid;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION app_private.sync_payload_pg_v1(jsonb),
 app_private.sync_payload_sha256_pg_v1(jsonb),
 app_private.validate_normalized_sync_payload_v2(text,jsonb),
 app_private.guard_normalized_sync_ingress(),
 app_private.check_normalized_sync_ingress_pair(),
 app_private.stage_normalized_sync_ingress_v2(uuid,uuid,uuid,uuid,bigint,text,integer,text,uuid,timestamptz,bytea,jsonb,integer)
 FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.sync_event WHERE ingress_validation_version=2)
  OR EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE ingress_validation_version=2) THEN
  RAISE EXCEPTION 'refusing normalized ingress downgrade with v2 evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER check_normalized_sync_ingress_pair ON sync.sync_event;
DROP TRIGGER check_normalized_inbox_ingress_pair ON sync.device_event_inbox;
DROP TRIGGER guard_normalized_sync_ingress ON sync.sync_event;
DROP TRIGGER guard_normalized_inbox_ingress ON sync.device_event_inbox;
DROP FUNCTION
 app_private.stage_normalized_sync_ingress_v2(uuid,uuid,uuid,uuid,bigint,text,integer,text,uuid,timestamptz,bytea,jsonb,integer);
DROP FUNCTION app_private.check_normalized_sync_ingress_pair();
DROP FUNCTION app_private.guard_normalized_sync_ingress();
DROP FUNCTION app_private.validate_normalized_sync_payload_v2(text,jsonb);
DROP FUNCTION app_private.sync_payload_sha256_pg_v1(jsonb);
DROP FUNCTION app_private.sync_payload_pg_v1(jsonb);
DROP TABLE app_private.normalized_ingress_write_context;
ALTER TABLE sync.device_event_inbox DROP CONSTRAINT ck_inbox_normalized_ingress,
 DROP CONSTRAINT ck_inbox_ingress_version, DROP COLUMN normalized_payload_sha256,
 DROP COLUMN ingress_validation_version;
ALTER TABLE sync.sync_event DROP CONSTRAINT ck_sync_event_normalized_ingress,
 DROP CONSTRAINT ck_sync_event_ingress_version, DROP COLUMN normalized_payload_sha256,
 DROP COLUMN ingress_device_sequence, DROP COLUMN ingress_journal_epoch,
 DROP COLUMN ingress_validation_version;
""")
