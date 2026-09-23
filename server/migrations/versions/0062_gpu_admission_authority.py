"""Add non-activating, generation-fenced GPU admission authority."""

from alembic import op

revision = "0062_gpu_admission_authority"
down_revision = "0061_ml_artifact_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.gpu_device_authority (
 device_uuid uuid PRIMARY KEY,
 reviewed_total_vram_bytes bigint NOT NULL
  CHECK(reviewed_total_vram_bytes BETWEEN 1 AND 9007199254740991),
 safety_margin_bytes bigint NOT NULL CHECK(safety_margin_bytes>=1073741824),
 reserved_sona_vram_bytes bigint NOT NULL CHECK(reserved_sona_vram_bytes>0),
 generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
 face_cancellation_generation bigint NOT NULL DEFAULT 0
  CHECK(face_cancellation_generation>=0),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(safety_margin_bytes*10>=reviewed_total_vram_bytes),
 CHECK(safety_margin_bytes+reserved_sona_vram_bytes<reviewed_total_vram_bytes)
);
CREATE TABLE ml.gpu_reservation_current (
 device_uuid uuid NOT NULL REFERENCES ml.gpu_device_authority(device_uuid) ON DELETE RESTRICT,
 reservation_kind text NOT NULL CHECK(reservation_kind IN ('FACE','SONA')),
 reservation_generation bigint NOT NULL CHECK(reservation_generation>=1),
 authority_generation bigint NOT NULL CHECK(authority_generation>=1),
 holder_id uuid NOT NULL,
 model_identity_sha256 bytea NOT NULL CHECK(octet_length(model_identity_sha256)=32),
 priority integer NOT NULL CHECK(priority IN (10,100)),
 requested_vram_bytes bigint NOT NULL CHECK(requested_vram_bytes>0),
 measured_vram_bytes bigint NOT NULL CHECK(measured_vram_bytes>=0),
 cancellation_generation bigint NOT NULL CHECK(cancellation_generation>=0),
 state text NOT NULL CHECK(state IN ('ACTIVE','CANCELLED','RELEASED','EXPIRED')),
 acquired_at timestamptz NOT NULL,
 heartbeat_at timestamptz NOT NULL,
 lease_until timestamptz NOT NULL,
 released_at timestamptz,
 PRIMARY KEY(device_uuid,reservation_kind),
 CHECK((state IN ('ACTIVE','CANCELLED') AND released_at IS NULL) OR
  (state IN ('RELEASED','EXPIRED') AND released_at IS NOT NULL)),
 CHECK((reservation_kind='FACE' AND priority=10) OR
  (reservation_kind='SONA' AND priority=100))
);
CREATE INDEX ix_gpu_current_lease ON ml.gpu_reservation_current(lease_until)
 WHERE state IN ('ACTIVE','CANCELLED');

CREATE TABLE ml.gpu_admission_receipt (
 receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
 device_uuid uuid NOT NULL REFERENCES ml.gpu_device_authority(device_uuid) ON DELETE RESTRICT,
 reservation_kind text NOT NULL CHECK(reservation_kind IN ('FACE','SONA')),
 event_kind text NOT NULL CHECK(event_kind IN
  ('ACQUIRED','HEARTBEAT','CANCEL_FACE','RELEASED','EXPIRED')),
 authority_generation bigint NOT NULL CHECK(authority_generation>=1),
 reservation_generation bigint NOT NULL CHECK(reservation_generation>=1),
 holder_id uuid NOT NULL,
 model_identity_sha256 bytea NOT NULL CHECK(octet_length(model_identity_sha256)=32),
 priority integer NOT NULL CHECK(priority IN (10,100)),
 requested_vram_bytes bigint NOT NULL CHECK(requested_vram_bytes>0),
 measured_vram_bytes bigint NOT NULL CHECK(measured_vram_bytes>=0),
 cancellation_generation bigint NOT NULL CHECK(cancellation_generation>=0),
 lease_until timestamptz NOT NULL,
 nvml_release_confirmed boolean NOT NULL DEFAULT false,
 process_exit_confirmed boolean NOT NULL DEFAULT false,
 session_unloaded boolean NOT NULL DEFAULT false,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(device_uuid,authority_generation),
 CHECK((reservation_kind='FACE' AND priority=10) OR
  (reservation_kind='SONA' AND priority=100)),
 CHECK(measured_vram_bytes<=requested_vram_bytes)
);
CREATE INDEX ix_gpu_receipt_device_time
 ON ml.gpu_admission_receipt(device_uuid,recorded_at DESC);

CREATE FUNCTION app_private.protect_gpu_current_projection() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<2 THEN
  RAISE EXCEPTION 'gpu_current_is_derived' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_gpu_device_derived BEFORE UPDATE OR DELETE ON ml.gpu_device_authority
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_gpu_current_projection();
CREATE TRIGGER z_gpu_current_derived BEFORE INSERT OR UPDATE OR DELETE
 ON ml.gpu_reservation_current FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_gpu_current_projection();

CREATE FUNCTION app_private.apply_gpu_admission_receipt() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE a ml.gpu_device_authority%ROWTYPE;
        c ml.gpu_reservation_current%ROWTYPE;
        other_current ml.gpu_reservation_current%ROWTYPE;
        current_exists boolean;
        next_face_cancel bigint;
BEGIN
 SELECT * INTO a FROM ml.gpu_device_authority
  WHERE device_uuid=NEW.device_uuid FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'gpu_device_unknown' USING ERRCODE='23503';
 END IF;
 IF NEW.authority_generation<>a.generation+1 THEN
  RAISE EXCEPTION 'gpu_authority_generation_conflict' USING ERRCODE='40001';
 END IF;
 IF NEW.recorded_at<clock_timestamp()-interval '5 seconds'
  OR NEW.recorded_at>clock_timestamp()+interval '5 seconds' THEN
  RAISE EXCEPTION 'gpu_receipt_time_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO c FROM ml.gpu_reservation_current
  WHERE device_uuid=NEW.device_uuid AND reservation_kind=NEW.reservation_kind FOR UPDATE;
 current_exists:=FOUND;
 next_face_cancel:=a.face_cancellation_generation;

 IF NEW.event_kind='ACQUIRED' THEN
  IF current_exists AND c.state NOT IN ('RELEASED','EXPIRED') THEN
   RAISE EXCEPTION 'gpu_previous_holder_unreleased' USING ERRCODE='23514';
  END IF;
  IF NEW.reservation_generation<>coalesce(c.reservation_generation,0)+1
   OR NEW.measured_vram_bytes<>0
   OR NEW.nvml_release_confirmed OR NEW.process_exit_confirmed OR NEW.session_unloaded
   OR NEW.lease_until<=clock_timestamp()
   OR NEW.lease_until>NEW.recorded_at+interval '30 seconds' THEN
   RAISE EXCEPTION 'gpu_acquire_shape_invalid' USING ERRCODE='23514';
  END IF;
  IF NEW.reservation_kind='FACE' THEN
   IF NEW.cancellation_generation<>a.face_cancellation_generation
    OR NEW.requested_vram_bytes>
      a.reviewed_total_vram_bytes-a.safety_margin_bytes-a.reserved_sona_vram_bytes THEN
    RAISE EXCEPTION 'gpu_face_budget_invalid' USING ERRCODE='23514';
   END IF;
   SELECT * INTO other_current FROM ml.gpu_reservation_current
    WHERE device_uuid=NEW.device_uuid AND reservation_kind='SONA';
   IF FOUND AND other_current.state='ACTIVE' AND
     NEW.requested_vram_bytes+other_current.requested_vram_bytes>
       a.reviewed_total_vram_bytes-a.safety_margin_bytes THEN
    RAISE EXCEPTION 'gpu_capacity_exceeded' USING ERRCODE='23514';
   END IF;
  ELSE
   IF NEW.cancellation_generation<>0
    OR NEW.requested_vram_bytes>a.reserved_sona_vram_bytes THEN
    RAISE EXCEPTION 'gpu_sona_budget_invalid' USING ERRCODE='23514';
   END IF;
   SELECT * INTO other_current FROM ml.gpu_reservation_current
    WHERE device_uuid=NEW.device_uuid AND reservation_kind='FACE';
   IF FOUND AND other_current.state IN ('ACTIVE','CANCELLED') THEN
    RAISE EXCEPTION 'gpu_face_release_required' USING ERRCODE='23514';
   END IF;
  END IF;
  INSERT INTO ml.gpu_reservation_current(
   device_uuid,reservation_kind,reservation_generation,authority_generation,
   holder_id,model_identity_sha256,priority,requested_vram_bytes,
   measured_vram_bytes,cancellation_generation,state,acquired_at,heartbeat_at,lease_until)
  VALUES(NEW.device_uuid,NEW.reservation_kind,NEW.reservation_generation,
   NEW.authority_generation,NEW.holder_id,NEW.model_identity_sha256,NEW.priority,
   NEW.requested_vram_bytes,0,NEW.cancellation_generation,'ACTIVE',
   NEW.recorded_at,NEW.recorded_at,NEW.lease_until)
  ON CONFLICT(device_uuid,reservation_kind) DO UPDATE SET
   reservation_generation=EXCLUDED.reservation_generation,
   authority_generation=EXCLUDED.authority_generation,
   holder_id=EXCLUDED.holder_id,model_identity_sha256=EXCLUDED.model_identity_sha256,
   priority=EXCLUDED.priority,requested_vram_bytes=EXCLUDED.requested_vram_bytes,
   measured_vram_bytes=0,cancellation_generation=EXCLUDED.cancellation_generation,
   state='ACTIVE',acquired_at=EXCLUDED.acquired_at,
   heartbeat_at=EXCLUDED.heartbeat_at,lease_until=EXCLUDED.lease_until,released_at=NULL;
 ELSE
  IF NOT current_exists OR c.holder_id<>NEW.holder_id
   OR c.reservation_generation<>NEW.reservation_generation
   OR c.model_identity_sha256<>NEW.model_identity_sha256
   OR c.priority<>NEW.priority OR c.requested_vram_bytes<>NEW.requested_vram_bytes THEN
   RAISE EXCEPTION 'gpu_holder_generation_mismatch' USING ERRCODE='40001';
  END IF;
  IF NEW.event_kind='HEARTBEAT' THEN
   IF c.state<>'ACTIVE' OR c.lease_until<=clock_timestamp()
    OR NEW.cancellation_generation<>c.cancellation_generation
    OR NEW.lease_until<=clock_timestamp()
    OR NEW.lease_until>NEW.recorded_at+interval '30 seconds'
    OR NEW.nvml_release_confirmed OR NEW.process_exit_confirmed OR NEW.session_unloaded THEN
    RAISE EXCEPTION 'gpu_heartbeat_fenced' USING ERRCODE='23514';
   END IF;
   UPDATE ml.gpu_reservation_current SET authority_generation=NEW.authority_generation,
    measured_vram_bytes=NEW.measured_vram_bytes,heartbeat_at=NEW.recorded_at,
    lease_until=NEW.lease_until WHERE device_uuid=NEW.device_uuid
     AND reservation_kind=NEW.reservation_kind;
  ELSIF NEW.event_kind='CANCEL_FACE' THEN
   IF NEW.reservation_kind<>'FACE' OR c.state<>'ACTIVE'
    OR NEW.cancellation_generation<>a.face_cancellation_generation+1
    OR NEW.lease_until<>c.lease_until
    OR NEW.measured_vram_bytes<>c.measured_vram_bytes
    OR NEW.nvml_release_confirmed OR NEW.process_exit_confirmed OR NEW.session_unloaded THEN
    RAISE EXCEPTION 'gpu_cancel_shape_invalid' USING ERRCODE='23514';
   END IF;
   next_face_cancel:=NEW.cancellation_generation;
   UPDATE ml.gpu_reservation_current SET authority_generation=NEW.authority_generation,
    cancellation_generation=NEW.cancellation_generation,state='CANCELLED'
    WHERE device_uuid=NEW.device_uuid AND reservation_kind='FACE';
  ELSIF NEW.event_kind IN ('RELEASED','EXPIRED') THEN
   IF c.state NOT IN ('ACTIVE','CANCELLED')
    OR NEW.cancellation_generation<>c.cancellation_generation
    OR NEW.lease_until<>c.lease_until
    OR NEW.measured_vram_bytes<>0
    OR NOT NEW.nvml_release_confirmed
    OR NOT (NEW.process_exit_confirmed OR NEW.session_unloaded)
    OR (NEW.event_kind='EXPIRED' AND c.lease_until>clock_timestamp()) THEN
    RAISE EXCEPTION 'gpu_release_proof_required' USING ERRCODE='23514';
   END IF;
   UPDATE ml.gpu_reservation_current SET authority_generation=NEW.authority_generation,
    measured_vram_bytes=0,state=NEW.event_kind,released_at=NEW.recorded_at
    WHERE device_uuid=NEW.device_uuid AND reservation_kind=NEW.reservation_kind;
  ELSE
   RAISE EXCEPTION 'gpu_event_kind_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 UPDATE ml.gpu_device_authority SET generation=NEW.authority_generation,
  face_cancellation_generation=next_face_cancel,updated_at=NEW.recorded_at
  WHERE device_uuid=NEW.device_uuid;
 RETURN NEW;
END $$;
CREATE TRIGGER a_gpu_receipt_apply BEFORE INSERT ON ml.gpu_admission_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.apply_gpu_admission_receipt();
CREATE TRIGGER z_gpu_receipt_immutable BEFORE UPDATE OR DELETE ON ml.gpu_admission_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
REVOKE ALL ON ml.gpu_device_authority,ml.gpu_reservation_current,
 ml.gpu_admission_receipt FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_gpu_current_projection() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.apply_gpu_admission_receipt() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.gpu_device_authority)
  OR EXISTS(SELECT 1 FROM ml.gpu_reservation_current)
  OR EXISTS(SELECT 1 FROM ml.gpu_admission_receipt) THEN
  RAISE EXCEPTION 'refusing gpu admission downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_gpu_receipt_immutable ON ml.gpu_admission_receipt;
DROP TRIGGER a_gpu_receipt_apply ON ml.gpu_admission_receipt;
DROP TRIGGER z_gpu_current_derived ON ml.gpu_reservation_current;
DROP TRIGGER z_gpu_device_derived ON ml.gpu_device_authority;
DROP FUNCTION app_private.apply_gpu_admission_receipt();
DROP FUNCTION app_private.protect_gpu_current_projection();
DROP TABLE ml.gpu_admission_receipt;
DROP TABLE ml.gpu_reservation_current;
DROP TABLE ml.gpu_device_authority;
""")
