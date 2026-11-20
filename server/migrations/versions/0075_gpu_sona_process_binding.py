"""Bind one Sona reservation generation to one immutable Linux process identity.

Revision ID: 0075_gpu_sona_binding
Revises: 0074_sync_erasure_receipt
"""

from alembic import op

revision = "0075_gpu_sona_binding"
down_revision = "0074_sync_erasure_receipt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ml.gpu_admission_receipt
 ADD COLUMN release_process_id bigint,
 ADD COLUMN release_boot_id uuid,
 ADD COLUMN release_start_ticks bigint,
 ADD COLUMN release_pid_namespace_inode bigint,
 ADD CONSTRAINT ck_gpu_receipt_process_identity CHECK (
  (release_process_id IS NULL AND release_boot_id IS NULL
   AND release_start_ticks IS NULL AND release_pid_namespace_inode IS NULL)
  OR
  (release_process_id IS NOT NULL AND release_process_id BETWEEN 1 AND 4294967295
   AND release_boot_id IS NOT NULL AND release_start_ticks IS NOT NULL
   AND release_start_ticks>0 AND release_pid_namespace_inode IS NOT NULL
   AND release_pid_namespace_inode>0)
 );
CREATE TABLE ml.gpu_sona_process_binding (
 device_uuid uuid NOT NULL REFERENCES ml.gpu_device_authority(device_uuid) ON DELETE RESTRICT,
 reservation_generation bigint NOT NULL CONSTRAINT ck_gpu_sona_binding_generation
  CHECK(reservation_generation>=1),
 holder_id uuid NOT NULL,
 model_identity_sha256 bytea NOT NULL CONSTRAINT ck_gpu_sona_binding_model_hash
  CHECK(octet_length(model_identity_sha256)=32),
 process_id bigint NOT NULL CONSTRAINT ck_gpu_sona_binding_pid
  CHECK(process_id BETWEEN 1 AND 4294967295),
 boot_id uuid NOT NULL,
 start_ticks bigint NOT NULL CONSTRAINT ck_gpu_sona_binding_start
  CHECK(start_ticks>0),
 pid_namespace_inode bigint NOT NULL CONSTRAINT ck_gpu_sona_binding_namespace
  CHECK(pid_namespace_inode>0),
 bound_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(device_uuid,reservation_generation),
 CONSTRAINT uq_gpu_sona_process_identity
  UNIQUE(device_uuid,boot_id,process_id,start_ticks)
);

CREATE FUNCTION app_private.guard_gpu_sona_process_binding() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE c ml.gpu_reservation_current%ROWTYPE;
BEGIN
 PERFORM 1 FROM ml.gpu_device_authority WHERE device_uuid=NEW.device_uuid FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'gpu_device_unknown' USING ERRCODE='23503';
 END IF;
 SELECT * INTO c FROM ml.gpu_reservation_current
  WHERE device_uuid=NEW.device_uuid AND reservation_kind='SONA' FOR UPDATE;
 IF NOT FOUND OR c.state<>'ACTIVE' OR c.lease_until<=clock_timestamp()
  OR c.reservation_generation<>NEW.reservation_generation
  OR c.holder_id<>NEW.holder_id
  OR c.model_identity_sha256<>NEW.model_identity_sha256 THEN
  RAISE EXCEPTION 'gpu_sona_binding_fenced' USING ERRCODE='40001';
 END IF;
 IF NEW.bound_at<clock_timestamp()-interval '5 seconds'
  OR NEW.bound_at>clock_timestamp()+interval '5 seconds' THEN
  RAISE EXCEPTION 'gpu_sona_binding_time_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_gpu_sona_binding_guard BEFORE INSERT ON ml.gpu_sona_process_binding
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_gpu_sona_process_binding();
CREATE TRIGGER z_gpu_sona_binding_immutable BEFORE UPDATE OR DELETE
 ON ml.gpu_sona_process_binding FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();

CREATE FUNCTION app_private.guard_gpu_sona_release_identity() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE b ml.gpu_sona_process_binding%ROWTYPE;
BEGIN
 IF NEW.event_kind NOT IN ('RELEASED','EXPIRED') OR NEW.reservation_kind<>'SONA' THEN
  IF NEW.release_process_id IS NOT NULL THEN
   RAISE EXCEPTION 'gpu_release_identity_unexpected' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 PERFORM 1 FROM ml.gpu_device_authority WHERE device_uuid=NEW.device_uuid FOR UPDATE;
 SELECT * INTO b FROM ml.gpu_sona_process_binding
  WHERE device_uuid=NEW.device_uuid AND reservation_generation=NEW.reservation_generation;
 IF FOUND THEN
  IF NEW.release_process_id IS DISTINCT FROM b.process_id
   OR NEW.release_boot_id IS DISTINCT FROM b.boot_id
   OR NEW.release_start_ticks IS DISTINCT FROM b.start_ticks
   OR NEW.release_pid_namespace_inode IS DISTINCT FROM b.pid_namespace_inode THEN
   RAISE EXCEPTION 'gpu_release_identity_mismatch' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.release_process_id IS NOT NULL THEN
  RAISE EXCEPTION 'gpu_release_identity_unbound' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a0_gpu_sona_release_identity BEFORE INSERT ON ml.gpu_admission_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_gpu_sona_release_identity();
REVOKE ALL ON ml.gpu_sona_process_binding FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.guard_gpu_sona_process_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.guard_gpu_sona_release_identity() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.gpu_sona_process_binding)
  OR EXISTS(SELECT 1 FROM ml.gpu_admission_receipt WHERE release_process_id IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing gpu process binding downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_gpu_sona_binding_immutable ON ml.gpu_sona_process_binding;
DROP TRIGGER a_gpu_sona_binding_guard ON ml.gpu_sona_process_binding;
DROP TRIGGER a0_gpu_sona_release_identity ON ml.gpu_admission_receipt;
DROP FUNCTION app_private.guard_gpu_sona_release_identity();
DROP FUNCTION app_private.guard_gpu_sona_process_binding();
DROP TABLE ml.gpu_sona_process_binding;
ALTER TABLE ml.gpu_admission_receipt DROP CONSTRAINT ck_gpu_receipt_process_identity;
ALTER TABLE ml.gpu_admission_receipt
 DROP COLUMN release_process_id,
 DROP COLUMN release_boot_id,
 DROP COLUMN release_start_ticks,
 DROP COLUMN release_pid_namespace_inode;
""")
