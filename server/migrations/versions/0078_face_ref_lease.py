"""Permit versioned Face leases only with an exact signed owner reference.

Revision ID: 0078_face_ref_lease
Revises: 0077_m11_auto_backlog
"""

from alembic import op

revision = "0078_face_ref_lease"
down_revision = "0077_m11_auto_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE OR REPLACE FUNCTION app_private.check_face_signed_upsert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE p ml.face_projection%ROWTYPE;
DECLARE e sync.sync_event%ROWTYPE;
BEGIN
 SELECT * INTO p FROM ml.face_projection WHERE face_projection_id=NEW.face_projection_id FOR SHARE;
 SELECT * INTO e FROM sync.sync_event WHERE event_id=NEW.event_id FOR SHARE;
 IF p.face_projection_id IS NULL OR p.state<>'ACTIVE' OR p.projection_generation<>1
    OR p.server_profile_id IS NOT NULL OR e.event_id IS NULL
    OR e.user_id IS DISTINCT FROM p.owner_user_id
    OR e.event_type<>'FACE_PROJECTION_UPSERTED' OR e.schema_version NOT IN (2,3)
    OR e.aggregate_type<>'FACE_PROJECTION' OR e.aggregate_id<>p.face_projection_id
    OR e.operation<>'UPSERT' OR e.origin_device_id IS NOT NULL
    OR e.server_row_version IS NOT NULL
    OR e.payload IS DISTINCT FROM convert_from(NEW.canonical_envelope,'UTF8')::jsonb
    OR e.payload->>'schema_version' IS DISTINCT FROM e.schema_version::text
    OR e.payload->>'projection_id' IS DISTINCT FROM p.face_projection_id::text
    OR e.payload->>'server_profile_id' IS DISTINCT FROM NEW.server_profile_id::text
    OR e.payload->>'user_id' IS DISTINCT FROM p.owner_user_id::text
    OR (e.schema_version=3 AND
       e.payload->>'user_track_ref_id' IS DISTINCT FROM p.user_track_ref_id::text)
    OR (e.schema_version=2 AND e.payload ? 'user_track_ref_id') THEN
  RAISE EXCEPTION 'face_signed_upsert_binding_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.sync_event WHERE event_type='FACE_PROJECTION_UPSERTED'
           AND schema_version=3) THEN
  RAISE EXCEPTION 'face_reference_lease_downgrade_has_data' USING ERRCODE='55000';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION app_private.check_face_signed_upsert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE p ml.face_projection%ROWTYPE;
DECLARE e sync.sync_event%ROWTYPE;
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
""")
