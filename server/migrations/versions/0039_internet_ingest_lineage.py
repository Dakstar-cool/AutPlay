"""Persist immutable Internet selection lineage for server-owned ingest handoff."""

from alembic import op

revision = "0039_internet_ingest_lineage"
down_revision = "0038_worker_resource_wait"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing device and A1 uploads retain their original actor and authority.
    op.execute("""
ALTER TABLE vault.upload_session
  ADD COLUMN source_internet_acquisition_id uuid,
  ADD CONSTRAINT upload_session_source_internet_acquisition_id_fkey
    FOREIGN KEY(source_internet_acquisition_id)
    REFERENCES discovery.internet_acquisition(acquisition_id) ON DELETE RESTRICT,
  ADD CONSTRAINT uq_upload_session_source_internet_acquisition
    UNIQUE(source_internet_acquisition_id),
  DROP CONSTRAINT ck_upload_session_actor,
  ADD CONSTRAINT ck_upload_session_actor CHECK (
    (actor_kind='DEVICE' AND device_id IS NOT NULL
      AND source_candidate_id IS NULL AND source_acquisition_attempt_id IS NULL
      AND source_internet_acquisition_id IS NULL)
    OR (actor_kind='PROVIDER' AND device_id IS NULL
      AND source_candidate_id IS NOT NULL AND source_acquisition_attempt_id IS NOT NULL
      AND source_internet_acquisition_id IS NULL)
    OR (actor_kind='INTERNET' AND device_id IS NULL
      AND source_candidate_id IS NULL AND source_acquisition_attempt_id IS NULL
      AND source_internet_acquisition_id IS NOT NULL
      AND declared_sha256 IS NOT NULL AND job_id IS NOT NULL AND state<>'OPEN'));

CREATE FUNCTION app_private.protect_internet_ingest_lineage() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_TABLE_NAME='upload_session' THEN
    IF (OLD.actor_kind='INTERNET' OR NEW.actor_kind='INTERNET') AND
       ROW(NEW.actor_kind,NEW.user_id,NEW.device_id,NEW.source_candidate_id,
           NEW.source_acquisition_attempt_id,NEW.source_internet_acquisition_id,
           NEW.target_recording_id,NEW.staging_key,NEW.job_id,
           NEW.expected_size,NEW.declared_sha256,NEW.request_hash,NEW.idempotency_key)
       IS DISTINCT FROM
       ROW(OLD.actor_kind,OLD.user_id,OLD.device_id,OLD.source_candidate_id,
           OLD.source_acquisition_attempt_id,OLD.source_internet_acquisition_id,
           OLD.target_recording_id,OLD.staging_key,OLD.job_id,
           OLD.expected_size,OLD.declared_sha256,OLD.request_hash,OLD.idempotency_key)
    THEN
      RAISE EXCEPTION 'Internet upload lineage is immutable' USING ERRCODE='23514';
    END IF;
  ELSE
    IF ROW(NEW.user_id,NEW.device_id,NEW.job_id,NEW.search_id,
           NEW.candidate_id,NEW.selected_snapshot)
       IS DISTINCT FROM
       ROW(OLD.user_id,OLD.device_id,OLD.job_id,OLD.search_id,
           OLD.candidate_id,OLD.selected_snapshot)
       OR (OLD.upload_id IS NOT NULL AND NEW.upload_id IS DISTINCT FROM OLD.upload_id)
       OR (OLD.user_track_ref_id IS NOT NULL
           AND NEW.user_track_ref_id IS DISTINCT FROM OLD.user_track_ref_id)
    THEN
      RAISE EXCEPTION 'Internet acquisition lineage is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER internet_upload_lineage_guard BEFORE UPDATE ON vault.upload_session
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_internet_ingest_lineage();
CREATE TRIGGER internet_acquisition_lineage_guard BEFORE UPDATE
  ON discovery.internet_acquisition
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_internet_ingest_lineage();
REVOKE ALL ON FUNCTION app_private.protect_internet_ingest_lineage() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM vault.upload_session WHERE source_internet_acquisition_id IS NOT NULL)
  THEN
    RAISE EXCEPTION 'Refusing to discard Internet ingest lineage';
  END IF;
END $$;
DROP TRIGGER internet_upload_lineage_guard ON vault.upload_session;
DROP TRIGGER internet_acquisition_lineage_guard ON discovery.internet_acquisition;
DROP FUNCTION app_private.protect_internet_ingest_lineage();
ALTER TABLE vault.upload_session DROP CONSTRAINT ck_upload_session_actor,
  DROP CONSTRAINT uq_upload_session_source_internet_acquisition,
  DROP CONSTRAINT upload_session_source_internet_acquisition_id_fkey,
  DROP COLUMN source_internet_acquisition_id,
  ADD CONSTRAINT ck_upload_session_actor CHECK (
    (actor_kind='DEVICE' AND device_id IS NOT NULL
      AND source_candidate_id IS NULL AND source_acquisition_attempt_id IS NULL)
    OR (actor_kind='PROVIDER' AND device_id IS NULL
      AND source_candidate_id IS NOT NULL AND source_acquisition_attempt_id IS NOT NULL));
    """)
