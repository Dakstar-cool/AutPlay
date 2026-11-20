"""Bind a Face projection only alongside its immutable signed sync event.

Revision ID: 0073_face_projection_binding
Revises: 0072_ml_step_up_register_hash
"""

from alembic import op

revision = "0073_face_projection_binding"
down_revision = "0072_ml_step_up_register_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""
ALTER TABLE ml.face_projection DROP CONSTRAINT ck_face_projection_profile_unproven;

CREATE TABLE ml.face_projection_signed_upsert (
 face_projection_id uuid PRIMARY KEY
  REFERENCES ml.face_projection(face_projection_id) ON DELETE RESTRICT,
 event_id uuid NOT NULL UNIQUE REFERENCES sync.sync_event(event_id) ON DELETE RESTRICT,
 server_profile_id uuid NOT NULL
  REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
 canonical_envelope bytea NOT NULL CHECK(octet_length(canonical_envelope) BETWEEN 1 AND 131072),
 envelope_sha256 bytea NOT NULL CHECK(octet_length(envelope_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_face_signed_upsert_digest CHECK(envelope_sha256=sha256(canonical_envelope))
);

CREATE FUNCTION app_private.check_face_signed_upsert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE p ml.face_projection%%ROWTYPE;
DECLARE e sync.sync_event%%ROWTYPE;
BEGIN
 SELECT * INTO p FROM ml.face_projection WHERE face_projection_id=NEW.face_projection_id FOR SHARE;
 SELECT * INTO e FROM sync.sync_event WHERE event_id=NEW.event_id FOR SHARE;
 IF p.face_projection_id IS NULL OR p.state<>'ACTIVE' OR p.projection_generation<>1
    OR p.server_profile_id IS NOT NULL OR e.event_id IS NULL
    OR e.user_id IS DISTINCT FROM p.owner_user_id
    OR e.event_type<>'FACE_PROJECTION_UPSERTED' OR e.schema_version<>2
    OR e.aggregate_type<>'FACE_PROJECTION' OR e.aggregate_id<>p.face_projection_id
    OR e.operation<>'UPSERT' OR e.origin_device_id IS NOT NULL
    OR e.server_row_version IS NOT NULL
    OR e.payload IS DISTINCT FROM convert_from(NEW.canonical_envelope,'UTF8')::jsonb
    OR e.payload->>'projection_id' IS DISTINCT FROM p.face_projection_id::text
    OR e.payload->>'server_profile_id' IS DISTINCT FROM NEW.server_profile_id::text
    OR e.payload->>'user_id' IS DISTINCT FROM p.owner_user_id::text THEN
  RAISE EXCEPTION 'face_signed_upsert_binding_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_signed_upsert_insert BEFORE INSERT ON ml.face_projection_signed_upsert
 FOR EACH ROW EXECUTE FUNCTION app_private.check_face_signed_upsert();

CREATE FUNCTION app_private.protect_face_signed_upsert() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'face_signed_upsert_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_signed_upsert_immutable BEFORE UPDATE OR DELETE
 ON ml.face_projection_signed_upsert FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_face_signed_upsert();

CREATE FUNCTION app_private.verify_face_signed_event_commit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE target_event_id uuid;
BEGIN
 target_event_id := NEW.event_id;
 IF NOT EXISTS(
  SELECT 1 FROM sync.sync_event e
  JOIN ml.face_projection_signed_upsert u ON u.event_id=e.event_id
  JOIN ml.face_projection p ON p.face_projection_id=u.face_projection_id
  WHERE e.event_id=target_event_id AND e.event_type='FACE_PROJECTION_UPSERTED'
   AND p.server_profile_id=u.server_profile_id AND p.state='ACTIVE'
   AND CASE WHEN e.payload->>'authorized_until_ms' ~ '^[0-9]+$'
    THEN (e.payload->>'authorized_until_ms')::bigint
    ELSE 0 END > floor(extract(epoch FROM clock_timestamp())*1000)::bigint
 ) THEN
  RAISE EXCEPTION 'face_signed_upsert_commit_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_signed_sync_event_commit AFTER INSERT ON sync.sync_event
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 WHEN (NEW.event_type='FACE_PROJECTION_UPSERTED')
 EXECUTE FUNCTION app_private.verify_face_signed_event_commit();
CREATE CONSTRAINT TRIGGER z_face_signed_upsert_commit AFTER INSERT
 ON ml.face_projection_signed_upsert DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_signed_event_commit();

CREATE OR REPLACE FUNCTION app_private.fence_face_projection_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.server_profile_id IS NOT NULL THEN
   RAISE EXCEPTION 'face_projection_profile_unproven' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' AND OLD.state='ACTIVE' AND NEW.state='TOMBSTONED'
  AND NEW.projection_generation=2
  AND (to_jsonb(NEW)-ARRAY['state','projection_generation','reason','tombstoned_at'])
   IS NOT DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','projection_generation','reason','tombstoned_at'])
 THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.state='ACTIVE' AND OLD.server_profile_id IS NULL
  AND NEW.server_profile_id IS NOT NULL
  AND (to_jsonb(NEW)-'server_profile_id')
   IS NOT DISTINCT FROM (to_jsonb(OLD)-'server_profile_id')
  AND EXISTS(SELECT 1 FROM ml.face_projection_signed_upsert u
   WHERE u.face_projection_id=OLD.face_projection_id
    AND u.server_profile_id=NEW.server_profile_id)
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'face_projection_mutation_closed' USING ERRCODE='55000';
END $$;

CREATE FUNCTION app_private.fence_face_signed_sync_event() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_projection_signed_upsert u WHERE u.event_id=OLD.event_id)
    OR OLD.event_type='FACE_PROJECTION_UPSERTED' THEN
  RAISE EXCEPTION 'face_signed_sync_event_immutable' USING ERRCODE='55000';
 END IF;
 RETURN CASE WHEN TG_OP='UPDATE' THEN NEW ELSE OLD END;
END $$;
CREATE TRIGGER z_face_signed_sync_event BEFORE UPDATE OR DELETE ON sync.sync_event
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_signed_sync_event();

REVOKE ALL ON ml.face_projection_signed_upsert FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.check_face_signed_upsert() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_face_signed_upsert() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.verify_face_signed_event_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_signed_sync_event() FROM PUBLIC;
""")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_projection_signed_upsert)
    OR EXISTS(SELECT 1 FROM ml.face_projection WHERE server_profile_id IS NOT NULL)
    OR EXISTS(SELECT 1 FROM sync.sync_event WHERE event_type='FACE_PROJECTION_UPSERTED') THEN
  RAISE EXCEPTION 'face_projection_binding_downgrade_has_data' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_face_signed_sync_event ON sync.sync_event;
DROP TRIGGER z_face_signed_sync_event_commit ON sync.sync_event;
DROP TRIGGER z_face_signed_upsert_commit ON ml.face_projection_signed_upsert;
DROP FUNCTION app_private.fence_face_signed_sync_event();
DROP FUNCTION app_private.verify_face_signed_event_commit();
CREATE OR REPLACE FUNCTION app_private.fence_face_projection_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.state='ACTIVE' AND NEW.state='TOMBSTONED'
  AND NEW.projection_generation=2
  AND (to_jsonb(NEW)-ARRAY['state','projection_generation','reason','tombstoned_at'])
   IS NOT DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','projection_generation','reason','tombstoned_at'])
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'face_projection_mutation_closed' USING ERRCODE='55000';
END $$;
DROP TRIGGER z_face_signed_upsert_immutable ON ml.face_projection_signed_upsert;
DROP TRIGGER z_face_signed_upsert_insert ON ml.face_projection_signed_upsert;
DROP FUNCTION app_private.protect_face_signed_upsert();
DROP FUNCTION app_private.check_face_signed_upsert();
DROP TABLE ml.face_projection_signed_upsert;
ALTER TABLE ml.face_projection ADD CONSTRAINT ck_face_projection_profile_unproven
 CHECK(server_profile_id IS NULL);
""")
