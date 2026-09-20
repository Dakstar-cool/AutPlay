"""Retain internal ingest process ownership independently of job or transfer leases."""

from alembic import op

revision = "0047_ingest_execution"
down_revision = "0046_upload_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE vault.ingest_execution (
  execution_id UUID CONSTRAINT ingest_execution_pkey PRIMARY KEY,
  owner_run_id UUID NOT NULL,
  upload_session_id UUID NOT NULL CONSTRAINT ingest_execution_upload_session_id_fkey
    REFERENCES vault.upload_session(upload_session_id),
  staging_key TEXT NOT NULL,
  job_id UUID NOT NULL CONSTRAINT ingest_execution_job_id_fkey REFERENCES jobs.job(job_id),
  worker_id TEXT NOT NULL,
  attempt_no BIGINT NOT NULL,
  state TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE,
  heartbeat_at TIMESTAMP WITH TIME ZONE,
  io_deadline_at TIMESTAMP WITH TIME ZONE,
  closed_at TIMESTAMP WITH TIME ZONE,
  child_pid BIGINT,
  child_identity_sha256 BYTEA,
  closure_kind TEXT,
  closure_evidence_sha256 BYTEA,
  exit_code BIGINT,
  CONSTRAINT ingest_execution_target_check CHECK (
    staging_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'
    AND length(worker_id) BETWEEN 1 AND 300 AND attempt_no>0
    AND state IN ('PREPARED','RUNNING','CLOSED')),
  CONSTRAINT ingest_execution_child_check CHECK (
    (child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL
    AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR
    (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL
    AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL
    AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at
    AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at
    AND io_deadline_at<=heartbeat_at+interval '5 seconds')),
  CONSTRAINT ingest_execution_times_check CHECK (
    (state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR
    (state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR
    (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at
    AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))),
  CONSTRAINT ingest_execution_closure_check CHECK (
    (state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL
    AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL
    AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32
    AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL)
    OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL)
    OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL))))
);
CREATE UNIQUE INDEX uq_ingest_execution_upload ON vault.ingest_execution(upload_session_id)
  WHERE closed_at IS NULL;
CREATE UNIQUE INDEX uq_ingest_execution_staging ON vault.ingest_execution(staging_key)
  WHERE closed_at IS NULL;

CREATE FUNCTION app_private.protect_ingest_execution() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Ingest ownership cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' THEN
    PERFORM 1 FROM vault.upload_session WHERE upload_session_id=NEW.upload_session_id FOR UPDATE;
    IF NEW.state<>'PREPARED' OR NOT EXISTS (
      SELECT 1 FROM vault.upload_session u JOIN jobs.job j ON j.job_id=u.job_id
      WHERE u.upload_session_id=NEW.upload_session_id AND u.staging_key=NEW.staging_key
      AND u.job_id=NEW.job_id AND u.state IN ('PROCESSING','COMMIT_PREPARED')
      AND j.job_type='vault.ingest' AND j.schema_version=1 AND j.user_id=u.user_id
      AND j.payload->>'upload_session_id'=u.upload_session_id::text
      AND j.state='RUNNING' AND j.lease_owner=NEW.worker_id AND j.attempt_count=NEW.attempt_no
      AND j.cancel_requested_at IS NULL AND j.lease_deadline>clock_timestamp()
    ) OR EXISTS (SELECT 1 FROM account.resource_io_execution e
      WHERE e.kind='VAULT_UPLOAD' AND e.actual_target_id=NEW.upload_session_id
      AND e.closed_at IS NULL) THEN
      RAISE EXCEPTION 'Ingest execution target is unavailable' USING ERRCODE='23514';
    END IF;
  ELSE
    IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.upload_session_id,NEW.staging_key,
           NEW.job_id,NEW.worker_id,NEW.attempt_no,NEW.created_at) IS DISTINCT FROM
       ROW(OLD.execution_id,OLD.owner_run_id,OLD.upload_session_id,OLD.staging_key,
           OLD.job_id,OLD.worker_id,OLD.attempt_no,OLD.created_at)
      OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
      OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','CLOSED'))
      OR (OLD.child_pid IS NOT NULL AND
          ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at) IS DISTINCT FROM
          ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at))
      OR (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
      RAISE EXCEPTION 'Ingest execution ownership is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NEW.state='RUNNING' AND (
    NEW.io_deadline_at<=clock_timestamp() OR (
      OLD.state='RUNNING' AND OLD.io_deadline_at<=clock_timestamp()
      AND ROW(NEW.heartbeat_at,NEW.io_deadline_at) IS DISTINCT FROM
          ROW(OLD.heartbeat_at,OLD.io_deadline_at)
    )
  ) THEN
    RAISE EXCEPTION 'ingest_execution_stale' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ingest_execution_guard BEFORE INSERT OR UPDATE OR DELETE ON vault.ingest_execution
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_ingest_execution();

CREATE FUNCTION app_private.protect_ingest_upload() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.ingest_execution e WHERE e.upload_session_id=OLD.upload_session_id)
  THEN
    IF TG_OP='DELETE' THEN
      RAISE EXCEPTION 'Ingest upload ownership cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF ROW(NEW.upload_session_id,NEW.user_id,NEW.device_id,NEW.actor_kind,NEW.staging_key,
           NEW.target_recording_id,NEW.expected_size,NEW.declared_sha256,NEW.job_id,
           NEW.source_candidate_id,NEW.source_acquisition_attempt_id,
           NEW.source_internet_acquisition_id,NEW.received_size,NEW.chunk_count) IS DISTINCT FROM
       ROW(OLD.upload_session_id,OLD.user_id,OLD.device_id,OLD.actor_kind,OLD.staging_key,
           OLD.target_recording_id,OLD.expected_size,OLD.declared_sha256,OLD.job_id,
           OLD.source_candidate_id,OLD.source_acquisition_attempt_id,
           OLD.source_internet_acquisition_id,OLD.received_size,OLD.chunk_count)
       OR NEW.state IN ('OPEN','SEALED','CANCELLED','EXPIRED') THEN
      RAISE EXCEPTION 'Ingest upload identity cannot rewind' USING ERRCODE='23514';
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ingest_upload_guard BEFORE UPDATE OR DELETE ON vault.upload_session
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_ingest_upload();
REVOKE ALL ON TABLE vault.ingest_execution FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_ingest_execution() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_ingest_upload() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.upload_session, vault.ingest_execution IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.ingest_execution) THEN
    RAISE EXCEPTION 'Refusing to discard ingest execution ownership';
  END IF;
END $$;
DROP TRIGGER ingest_upload_guard ON vault.upload_session;
DROP FUNCTION app_private.protect_ingest_upload();
DROP TABLE vault.ingest_execution;
DROP FUNCTION app_private.protect_ingest_execution();
    """)
