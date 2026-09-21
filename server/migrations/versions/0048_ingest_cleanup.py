"""Commit finalized staging cleanup intent with the exact registered WORK receipt."""

from alembic import op

revision = "0048_ingest_cleanup"
down_revision = "0047_ingest_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE vault.ingest_cleanup_claim (
 claim_id UUID CONSTRAINT ingest_cleanup_claim_pkey PRIMARY KEY
   CONSTRAINT ingest_cleanup_claim_claim_id_fkey REFERENCES vault.upload_session(upload_session_id),
 staging_key TEXT NOT NULL, final_state TEXT NOT NULL, expected_size BIGINT NOT NULL,
 sha256 BYTEA NOT NULL,
 work_execution_id UUID NOT NULL CONSTRAINT ingest_cleanup_claim_work_execution_id_fkey
   REFERENCES vault.ingest_execution(execution_id),
 vault_object_id UUID NOT NULL CONSTRAINT ingest_cleanup_claim_vault_object_id_fkey
   REFERENCES vault.vault_object(vault_object_id),
 audio_variant_id UUID NOT NULL CONSTRAINT ingest_cleanup_claim_audio_variant_id_fkey
   REFERENCES vault.audio_variant(audio_variant_id),
 created_at TIMESTAMP WITH TIME ZONE NOT NULL,
 completed_at TIMESTAMP WITH TIME ZONE, completed_execution_id UUID,
 CONSTRAINT ingest_cleanup_claim_staging_key UNIQUE(staging_key),
 CONSTRAINT ingest_cleanup_claim_target_check CHECK (
   staging_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'
   AND final_state IN ('COMMITTED','REUSED') AND expected_size>0 AND octet_length(sha256)=32),
 CONSTRAINT ingest_cleanup_claim_completion_check CHECK (
   (completed_at IS NULL AND completed_execution_id IS NULL) OR
   (completed_at IS NOT NULL AND completed_execution_id IS NOT NULL AND completed_at>=created_at))
);
CREATE INDEX ix_ingest_cleanup_pending ON vault.ingest_cleanup_claim(claim_id) WHERE
   completed_at IS NULL;
CREATE TABLE vault.ingest_cleanup_execution (
 execution_id UUID CONSTRAINT ingest_cleanup_execution_pkey PRIMARY KEY,
 owner_run_id UUID NOT NULL,
 claim_id UUID NOT NULL CONSTRAINT ingest_cleanup_execution_claim_id_fkey
   REFERENCES vault.ingest_cleanup_claim(claim_id),
 state TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL,
 started_at TIMESTAMP WITH TIME ZONE, heartbeat_at TIMESTAMP WITH TIME ZONE,
 io_deadline_at TIMESTAMP WITH TIME ZONE, closed_at TIMESTAMP WITH TIME ZONE,
 child_pid BIGINT, child_identity_sha256 BYTEA, closure_kind TEXT,
 closure_evidence_sha256 BYTEA, exit_code BIGINT,
 CONSTRAINT ingest_cleanup_execution_state_check CHECK (state IN ('PREPARED','RUNNING','CLOSED')),
 CONSTRAINT ingest_cleanup_execution_child_check CHECK (
   (child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL
   AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR
   (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL
   AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL
   AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at
   AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at
   AND io_deadline_at<=heartbeat_at+interval '5 seconds')),
 CONSTRAINT ingest_cleanup_execution_times_check CHECK (
   (state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR
   (state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR
   (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at
   AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))),
 CONSTRAINT ingest_cleanup_execution_closure_check CHECK (
   (state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL AND exit_code
   IS NULL) OR
   (state='CLOSED' AND closure_kind IS NOT NULL AND closure_evidence_sha256 IS NOT NULL
   AND octet_length(closure_evidence_sha256)=32
   AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL)
   OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL)
   OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL))))
);
CREATE UNIQUE INDEX uq_ingest_cleanup_execution_claim ON vault.ingest_cleanup_execution(claim_id)
 WHERE closed_at IS NULL;
ALTER TABLE vault.ingest_cleanup_claim ADD CONSTRAINT
   ingest_cleanup_claim_completed_execution_id_fkey
 FOREIGN KEY(completed_execution_id) REFERENCES vault.ingest_cleanup_execution(execution_id);

CREATE FUNCTION app_private.protect_ingest_cleanup_claim() RETURNS trigger LANGUAGE plpgsql AS
   $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Ingest cleanup intent cannot be deleted' USING
   ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.completed_at IS NOT NULL OR NOT EXISTS (
     SELECT 1 FROM vault.upload_session u JOIN vault.ingest_execution e
       ON e.execution_id=NEW.work_execution_id AND e.upload_session_id=u.upload_session_id
     WHERE u.upload_session_id=NEW.claim_id AND u.staging_key=NEW.staging_key
     AND u.state=NEW.final_state AND u.expected_size=NEW.expected_size AND
   u.computed_sha256=NEW.sha256
     AND u.vault_object_id=NEW.vault_object_id AND u.audio_variant_id=NEW.audio_variant_id
     AND e.staging_key=NEW.staging_key AND e.state='RUNNING' AND e.child_pid IS NOT NULL
     AND e.io_deadline_at>clock_timestamp()) THEN
     RAISE EXCEPTION 'Ingest cleanup intent requires current finalized WORK' USING ERRCODE='23514';
   END IF;
 ELSE
   IF ROW(NEW.claim_id,NEW.staging_key,NEW.final_state,NEW.expected_size,NEW.sha256,
          NEW.work_execution_id,NEW.vault_object_id,NEW.audio_variant_id,NEW.created_at) IS
   DISTINCT FROM
      ROW(OLD.claim_id,OLD.staging_key,OLD.final_state,OLD.expected_size,OLD.sha256,
          OLD.work_execution_id,OLD.vault_object_id,OLD.audio_variant_id,OLD.created_at)
     OR (OLD.completed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
     RAISE EXCEPTION 'Ingest cleanup intent is immutable' USING ERRCODE='23514';
   END IF;
   IF NEW.completed_at IS NOT NULL AND OLD.completed_at IS NULL AND (
     NOT EXISTS (SELECT 1 FROM vault.ingest_cleanup_execution e WHERE
   e.execution_id=NEW.completed_execution_id
       AND e.claim_id=NEW.claim_id AND e.state='CLOSED' AND e.closure_kind='PROCESS_EXIT' AND
   e.exit_code=0)
     OR EXISTS (SELECT 1 FROM vault.ingest_cleanup_execution e WHERE e.claim_id=NEW.claim_id
   AND e.closed_at IS NULL)
     OR EXISTS (SELECT 1 FROM vault.ingest_execution e WHERE e.upload_session_id=NEW.claim_id
   AND e.closed_at IS NULL)
     OR EXISTS (SELECT 1 FROM account.resource_io_execution e WHERE e.kind='VAULT_UPLOAD'
       AND e.actual_target_id=NEW.claim_id AND e.closed_at IS NULL)) THEN
     RAISE EXCEPTION 'Ingest cleanup execution is unconfirmed' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ingest_cleanup_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON
   vault.ingest_cleanup_claim
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_ingest_cleanup_claim();

CREATE FUNCTION app_private.queue_ingest_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE work_id UUID;
BEGIN
 IF NEW.state IN ('COMMITTED','REUSED') AND OLD.state NOT IN ('COMMITTED','REUSED')
   AND EXISTS (SELECT 1 FROM vault.ingest_execution WHERE
   upload_session_id=NEW.upload_session_id) THEN
   SELECT execution_id INTO work_id FROM vault.ingest_execution
     WHERE upload_session_id=NEW.upload_session_id AND state='RUNNING' AND closed_at IS NULL;
   IF work_id IS NULL THEN
     RAISE EXCEPTION 'Registered finalization requires current WORK' USING ERRCODE='23514';
   END IF;
   INSERT INTO vault.ingest_cleanup_claim(claim_id,staging_key,final_state,expected_size,sha256,
     work_execution_id,vault_object_id,audio_variant_id,created_at)
   VALUES(NEW.upload_session_id,NEW.staging_key,NEW.state,NEW.expected_size,NEW.computed_sha256,
     work_id,NEW.vault_object_id,NEW.audio_variant_id,clock_timestamp());
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ingest_cleanup_queue AFTER UPDATE ON vault.upload_session
 FOR EACH ROW EXECUTE FUNCTION app_private.queue_ingest_cleanup();

CREATE FUNCTION app_private.protect_finalized_ingest_upload() RETURNS trigger LANGUAGE plpgsql
   AS $$ BEGIN
 IF EXISTS (SELECT 1 FROM vault.ingest_cleanup_claim WHERE claim_id=OLD.upload_session_id) THEN
   IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Finalized ingest upload is retained' USING
   ERRCODE='23514'; END IF;
   IF ROW(NEW.state,NEW.staging_key,NEW.expected_size,NEW.computed_sha256,NEW.vault_object_id,
   NEW.audio_variant_id)
      IS DISTINCT FROM ROW(OLD.state,OLD.staging_key,OLD.expected_size,OLD.computed_sha256,
   OLD.vault_object_id,OLD.audio_variant_id) THEN
     RAISE EXCEPTION 'Finalized ingest cleanup target is immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER finalized_ingest_upload_guard BEFORE UPDATE OR DELETE ON vault.upload_session
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_finalized_ingest_upload();

CREATE FUNCTION app_private.protect_ingest_cleanup_execution() RETURNS trigger LANGUAGE plpgsql
   AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Ingest cleanup ownership cannot be deleted' USING
   ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
   PERFORM 1 FROM vault.upload_session WHERE upload_session_id=NEW.claim_id FOR UPDATE;
   IF NEW.state<>'PREPARED' OR NOT EXISTS (
     SELECT 1 FROM vault.ingest_cleanup_claim c JOIN vault.ingest_execution w ON
   w.execution_id=c.work_execution_id
     WHERE c.claim_id=NEW.claim_id AND c.completed_at IS NULL AND w.state='CLOSED'
       AND w.closure_kind='PROCESS_EXIT')
     OR EXISTS (SELECT 1 FROM vault.ingest_execution e WHERE e.upload_session_id=NEW.claim_id
   AND e.closed_at IS NULL)
     OR EXISTS (SELECT 1 FROM account.resource_io_execution e WHERE e.kind='VAULT_UPLOAD'
       AND e.actual_target_id=NEW.claim_id AND e.closed_at IS NULL) THEN
     RAISE EXCEPTION 'Ingest cleanup target is unavailable' USING ERRCODE='23514';
   END IF;
 ELSE
   IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.claim_id,NEW.created_at) IS DISTINCT FROM
      ROW(OLD.execution_id,OLD.owner_run_id,OLD.claim_id,OLD.created_at)
     OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
     OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','CLOSED'))
     OR (OLD.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at)
       IS DISTINCT FROM ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at))
     OR (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
     RAISE EXCEPTION 'Ingest cleanup ownership is immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.state='RUNNING' AND (NEW.io_deadline_at<=clock_timestamp() OR
   (OLD.state='RUNNING' AND OLD.io_deadline_at<=clock_timestamp()
   AND ROW(NEW.heartbeat_at,NEW.io_deadline_at) IS DISTINCT FROM ROW(OLD.heartbeat_at,
   OLD.io_deadline_at))) THEN
   RAISE EXCEPTION 'ingest_cleanup_stale' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ingest_cleanup_execution_guard BEFORE INSERT OR UPDATE OR DELETE ON
   vault.ingest_cleanup_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_ingest_cleanup_execution();
CREATE FUNCTION app_private.exclude_ingest_cleanup_writer() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target UUID;
BEGIN
 IF TG_TABLE_NAME='resource_io_execution' THEN
   IF NEW.kind<>'VAULT_UPLOAD' THEN RETURN NEW; END IF;
   target=NEW.actual_target_id;
 ELSE target=NEW.upload_session_id;
 END IF;
 PERFORM 1 FROM vault.upload_session WHERE upload_session_id=target FOR UPDATE;
 IF EXISTS (SELECT 1 FROM vault.ingest_cleanup_execution WHERE claim_id=target AND closed_at IS
   NULL) THEN
   RAISE EXCEPTION 'Ingest cleanup still owns staging' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ingest_cleanup_writer_guard BEFORE INSERT ON vault.ingest_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.exclude_ingest_cleanup_writer();
CREATE TRIGGER ingest_cleanup_writer_guard BEFORE INSERT ON account.resource_io_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.exclude_ingest_cleanup_writer();
REVOKE ALL ON TABLE vault.ingest_cleanup_claim, vault.ingest_cleanup_execution FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_ingest_cleanup_claim(),
   app_private.queue_ingest_cleanup(),
 app_private.protect_finalized_ingest_upload(), app_private.protect_ingest_cleanup_execution(),
 app_private.exclude_ingest_cleanup_writer() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.upload_session, vault.ingest_execution, vault.ingest_cleanup_claim,
 vault.ingest_cleanup_execution IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM vault.ingest_cleanup_claim) OR EXISTS (SELECT 1 FROM
   vault.ingest_cleanup_execution) THEN
   RAISE EXCEPTION 'Refusing to discard ingest cleanup ownership';
 END IF;
END $$;
DROP TRIGGER ingest_cleanup_queue ON vault.upload_session;
DROP TRIGGER finalized_ingest_upload_guard ON vault.upload_session;
DROP TRIGGER ingest_cleanup_writer_guard ON vault.ingest_execution;
DROP TRIGGER ingest_cleanup_writer_guard ON account.resource_io_execution;
DROP FUNCTION app_private.exclude_ingest_cleanup_writer();
DROP FUNCTION app_private.queue_ingest_cleanup();
DROP FUNCTION app_private.protect_finalized_ingest_upload();
ALTER TABLE vault.ingest_cleanup_claim DROP CONSTRAINT
   ingest_cleanup_claim_completed_execution_id_fkey;
DROP TABLE vault.ingest_cleanup_execution;
DROP TABLE vault.ingest_cleanup_claim;
DROP FUNCTION app_private.protect_ingest_cleanup_execution();
DROP FUNCTION app_private.protect_ingest_cleanup_claim();
    """)
