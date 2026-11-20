"""Durable, dormant owner-scoped Face projection tombstone delivery.

Revision ID: 0069_face_tombstone_outbox
Revises: 0068_face_timeline_work
"""

from alembic import op

revision = "0069_face_tombstone_outbox"
down_revision = "0068_face_timeline_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_projection_tombstone_outbox (
 outbox_id uuid PRIMARY KEY DEFAULT uuidv7(),
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 server_profile_id uuid REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
 face_projection_id uuid NOT NULL REFERENCES ml.face_projection(face_projection_id)
  ON DELETE RESTRICT,
 projection_generation bigint NOT NULL CHECK(projection_generation BETWEEN 1 AND 9007199254740991),
 reason_code text NOT NULL CHECK(reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 tombstoned_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','CLAIMED','DELIVERED')),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 5),
 claim_token uuid,
 lease_expires_at timestamptz,
 next_retry_at timestamptz,
 delivered_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT uq_face_tombstone_transition
  UNIQUE(owner_user_id,face_projection_id,projection_generation),
 CONSTRAINT ck_face_tombstone_delivery_shape CHECK(
  (state='PENDING' AND claim_token IS NULL AND lease_expires_at IS NULL AND delivered_at IS NULL)
  OR (state='CLAIMED' AND server_profile_id IS NOT NULL AND claim_token IS NOT NULL
      AND lease_expires_at IS NOT NULL AND delivered_at IS NULL)
  OR (state='DELIVERED' AND server_profile_id IS NOT NULL AND claim_token IS NULL
      AND lease_expires_at IS NULL AND delivered_at IS NOT NULL))
);
CREATE INDEX ix_face_tombstone_claim ON ml.face_projection_tombstone_outbox
 (created_at,outbox_id) WHERE state<>'DELIVERED' AND attempt_count<5;
CREATE INDEX ix_face_tombstone_owner ON ml.face_projection_tombstone_outbox
 (owner_user_id,face_projection_id,projection_generation);

CREATE TABLE ml.face_projection_tombstone_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 outbox_id uuid NOT NULL REFERENCES ml.face_projection_tombstone_outbox(outbox_id)
  ON DELETE RESTRICT,
 action text NOT NULL CHECK(action IN ('ENQUEUED','CLAIMED','RETRIED','DELIVERED')),
 attempt_count integer NOT NULL CHECK(attempt_count BETWEEN 0 AND 5),
 claim_token uuid,
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_face_tombstone_audit_outbox
 ON ml.face_projection_tombstone_audit(outbox_id,audit_id);

CREATE FUNCTION app_private.enqueue_face_projection_tombstone() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE profile uuid;
BEGIN
 IF NEW.state='TOMBSTONED' AND
  (TG_OP='INSERT' OR (OLD.state='ACTIVE'
   AND NEW.projection_generation>OLD.projection_generation)) THEN
  -- A missing or ambiguous profile stays pending and cannot be claimed.
  -- A later owner/profile binding migration must resolve such rows explicitly.
  SELECT (array_agg(server_instance_id))[1] INTO profile
   FROM account.server_instance HAVING count(*)=1;
  INSERT INTO ml.face_projection_tombstone_outbox
   (owner_user_id,server_profile_id,face_projection_id,projection_generation,
    reason_code,tombstoned_at)
  VALUES(NEW.owner_user_id,profile,NEW.face_projection_id,NEW.projection_generation,
    NEW.reason,NEW.tombstoned_at)
  ON CONFLICT (owner_user_id,face_projection_id,projection_generation) DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_face_projection_tombstone_outbox AFTER INSERT OR UPDATE
 ON ml.face_projection FOR EACH ROW
 EXECUTE FUNCTION app_private.enqueue_face_projection_tombstone();

CREATE FUNCTION app_private.fence_face_projection_tombstone_outbox() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'face_tombstone_outbox_delete_closed' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PENDING' OR NEW.attempt_count<>0 OR NEW.claim_token IS NOT NULL
   OR NEW.lease_expires_at IS NOT NULL OR NEW.next_retry_at IS NOT NULL
   OR NEW.delivered_at IS NOT NULL
   OR NOT EXISTS(SELECT 1 FROM ml.face_projection p
     WHERE p.face_projection_id=NEW.face_projection_id
      AND p.owner_user_id=NEW.owner_user_id AND p.state='TOMBSTONED'
      AND p.projection_generation=NEW.projection_generation
      AND p.reason=NEW.reason_code AND p.tombstoned_at=NEW.tombstoned_at) THEN
   RAISE EXCEPTION 'face_tombstone_outbox_insert_invalid' USING ERRCODE='55000';
  END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['server_profile_id','state','attempt_count','claim_token',
       'lease_expires_at','next_retry_at','delivered_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['server_profile_id','state','attempt_count','claim_token',
       'lease_expires_at','next_retry_at','delivered_at'])
   OR NEW.server_profile_id IS DISTINCT FROM OLD.server_profile_id
   OR (OLD.state='PENDING' AND NEW.state='CLAIMED' AND
       (NEW.attempt_count<>OLD.attempt_count+1 OR NEW.claim_token IS NULL
        OR NEW.lease_expires_at<=clock_timestamp() OR NEW.next_retry_at IS NOT NULL))
   OR (OLD.state='CLAIMED' AND NEW.state='PENDING' AND
       (NEW.attempt_count<>OLD.attempt_count OR NEW.claim_token IS NOT NULL
        OR NEW.lease_expires_at IS NOT NULL OR NEW.next_retry_at IS NULL))
   OR (OLD.state='CLAIMED' AND NEW.state='CLAIMED' AND
       (OLD.lease_expires_at>clock_timestamp() OR NEW.attempt_count<>OLD.attempt_count+1
        OR NEW.claim_token IS NULL OR NEW.claim_token=OLD.claim_token
        OR NEW.lease_expires_at<=clock_timestamp() OR NEW.next_retry_at IS NOT NULL))
   OR (OLD.state='CLAIMED' AND NEW.state='DELIVERED' AND
       (NEW.attempt_count<>OLD.attempt_count OR NEW.claim_token IS NOT NULL
        OR NEW.lease_expires_at IS NOT NULL OR NEW.next_retry_at IS NOT NULL
        OR NEW.delivered_at IS NULL))
   OR NOT ((OLD.state='PENDING' AND NEW.state='CLAIMED')
       OR (OLD.state='CLAIMED' AND NEW.state IN ('PENDING','CLAIMED','DELIVERED'))) THEN
   RAISE EXCEPTION 'face_tombstone_outbox_transition_invalid' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_tombstone_outbox_fence BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_projection_tombstone_outbox FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_projection_tombstone_outbox();

CREATE FUNCTION app_private.audit_face_projection_tombstone_outbox() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO ml.face_projection_tombstone_audit(outbox_id,action,attempt_count,claim_token)
 VALUES(NEW.outbox_id,
  CASE WHEN TG_OP='INSERT' THEN 'ENQUEUED'
   WHEN NEW.state='DELIVERED' THEN 'DELIVERED'
   WHEN NEW.state='PENDING' THEN 'RETRIED' ELSE 'CLAIMED' END,
  NEW.attempt_count,NEW.claim_token);
 RETURN NULL;
END $$;
CREATE TRIGGER zz_face_tombstone_outbox_audit AFTER INSERT OR UPDATE
 ON ml.face_projection_tombstone_outbox FOR EACH ROW
 EXECUTE FUNCTION app_private.audit_face_projection_tombstone_outbox();

CREATE FUNCTION app_private.fence_face_projection_tombstone_audit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'face_tombstone_audit_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_tombstone_audit_fence BEFORE UPDATE OR DELETE
 ON ml.face_projection_tombstone_audit FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_projection_tombstone_audit();

CREATE FUNCTION app_private.verify_face_tombstone_delivery() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='DELIVERED' AND NOT EXISTS(
  SELECT 1 FROM sync.sync_event e
  WHERE e.event_id=NEW.outbox_id AND e.user_id=NEW.owner_user_id
   AND e.event_type='FACE_PROJECTION_TOMBSTONED' AND e.schema_version=2
   AND e.aggregate_type='FACE_PROJECTION' AND e.aggregate_id=NEW.face_projection_id
   AND e.operation='DELETE' AND e.server_row_version=NEW.projection_generation
   AND e.origin_device_id IS NULL
   AND e.payload=jsonb_build_object(
    'schema_version',2,'projection_id',NEW.face_projection_id::text,
    'server_profile_id',NEW.server_profile_id::text,'user_id',NEW.owner_user_id::text,
    'superseding_generation',NEW.projection_generation,'reason_code',NEW.reason_code,
    'tombstoned_at_ms',floor(extract(epoch FROM NEW.tombstoned_at)*1000)::bigint)
 ) THEN
  RAISE EXCEPTION 'face_tombstone_sync_commit_required' USING ERRCODE='55000';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER zz_face_tombstone_delivery_commit
 AFTER UPDATE ON ml.face_projection_tombstone_outbox
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.verify_face_tombstone_delivery();

REVOKE ALL ON ml.face_projection_tombstone_outbox,ml.face_projection_tombstone_audit FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.enqueue_face_projection_tombstone() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_projection_tombstone_outbox() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.audit_face_projection_tombstone_outbox() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_projection_tombstone_audit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.verify_face_tombstone_delivery() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_projection_tombstone_outbox) THEN
  RAISE EXCEPTION 'refusing Face tombstone downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER zz_face_tombstone_delivery_commit ON ml.face_projection_tombstone_outbox;
DROP TRIGGER z_face_tombstone_audit_fence ON ml.face_projection_tombstone_audit;
DROP TRIGGER z_face_tombstone_outbox_fence ON ml.face_projection_tombstone_outbox;
DROP TRIGGER zz_face_tombstone_outbox_audit ON ml.face_projection_tombstone_outbox;
DROP TRIGGER zz_face_projection_tombstone_outbox ON ml.face_projection;
DROP TABLE ml.face_projection_tombstone_audit;
DROP TABLE ml.face_projection_tombstone_outbox;
DROP FUNCTION app_private.verify_face_tombstone_delivery();
DROP FUNCTION app_private.fence_face_projection_tombstone_audit();
DROP FUNCTION app_private.fence_face_projection_tombstone_outbox();
DROP FUNCTION app_private.audit_face_projection_tombstone_outbox();
DROP FUNCTION app_private.enqueue_face_projection_tombstone();
""")
