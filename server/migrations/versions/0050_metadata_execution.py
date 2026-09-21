"""Retained metadata bytes, pinned source authority and durable provider pacing."""

from alembic import op

revision = "0050_metadata_execution"
down_revision = "0049_internal_io_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""

CREATE TABLE library.metadata_execution (
    execution_id UUID NOT NULL,
    owner_run_id UUID NOT NULL,
    user_id UUID NOT NULL,
    user_track_ref_id UUID NOT NULL,
    generation BIGINT NOT NULL,
    authority_generation BIGINT NOT NULL,
    audio JSONB,
    job_id UUID NOT NULL,
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
    CONSTRAINT metadata_execution_pkey PRIMARY KEY (execution_id),
    CONSTRAINT metadata_execution_target_check CHECK (generation>0 AND authority_generation>0 AND
    length(worker_id) BETWEEN 1 AND 300 AND attempt_no>0 AND state IN
    ('PREPARED','RUNNING','CLOSED')),
    CONSTRAINT metadata_execution_child_check CHECK ((child_pid IS NULL AND child_identity_sha256
    IS NULL AND started_at IS NULL AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR
    (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL AND
    octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL AND started_at>=created_at
    AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at AND io_deadline_at IS NOT NULL AND
    io_deadline_at>heartbeat_at AND io_deadline_at<=heartbeat_at+interval '5 seconds')),
    CONSTRAINT metadata_execution_times_check CHECK ((state='PREPARED' AND started_at IS NULL AND
    closed_at IS NULL) OR (state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR
    (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at AND (heartbeat_at IS NULL
    OR closed_at>=heartbeat_at))),
    CONSTRAINT metadata_execution_closure_check CHECK ((state<>'CLOSED' AND closure_kind IS NULL
    AND closure_evidence_sha256 IS NULL AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind
    IS NOT NULL AND closure_evidence_sha256 IS NOT NULL AND
    octet_length(closure_evidence_sha256)=32 AND ((closure_kind='NOT_STARTED' AND child_pid IS
    NULL AND exit_code IS NULL) OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND
    exit_code IS NOT NULL) OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))),
    CONSTRAINT metadata_execution_audio_check CHECK (audio IS NULL OR
    (jsonb_typeof(audio)='object' AND octet_length(audio::text)<=2048)),
    CONSTRAINT metadata_execution_user_id_fkey FOREIGN KEY(user_id) REFERENCES
    account.user_account (user_id),
    CONSTRAINT metadata_execution_user_track_ref_id_fkey FOREIGN KEY(user_track_ref_id) REFERENCES
    library.user_track_ref (user_track_ref_id),
    CONSTRAINT metadata_execution_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job (job_id)
)

;
CREATE UNIQUE INDEX uq_metadata_execution_ref ON library.metadata_execution (user_track_ref_id)
    WHERE closed_at IS NULL;

CREATE TABLE library.metadata_provider_gate (
    singleton_id BIGINT NOT NULL,
    execution_id UUID,
    request_id UUID,
    next_request_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT metadata_provider_gate_pkey PRIMARY KEY (singleton_id),
    CONSTRAINT metadata_provider_gate_singleton_check CHECK (singleton_id=1),
    CONSTRAINT metadata_provider_gate_owner_check CHECK ((execution_id IS NULL)=(request_id IS
    NULL)),
    CONSTRAINT metadata_provider_gate_execution_id_fkey FOREIGN KEY(execution_id) REFERENCES
    library.metadata_execution (execution_id)
)

;

ALTER TABLE account.internal_io_policy ADD COLUMN workload_version SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE account.internal_io_policy ADD CONSTRAINT internal_io_workload_version_check
 CHECK(workload_version IN (1,2));
INSERT INTO library.metadata_provider_gate(singleton_id,next_request_at) VALUES(1,now());
CREATE FUNCTION app_private.protect_metadata_execution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'Metadata execution ownership cannot be deleted' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.state<>'PREPARED' THEN
     RAISE EXCEPTION 'metadata_execution_stale' USING ERRCODE='55000';
   END IF;
 ELSE
   IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.user_id,NEW.user_track_ref_id,NEW.generation,
          NEW.authority_generation,NEW.job_id,NEW.worker_id,NEW.attempt_no,NEW.audio,NEW.created_at)
      IS DISTINCT FROM
      ROW(OLD.execution_id,OLD.owner_run_id,OLD.user_id,OLD.user_track_ref_id,OLD.generation,
          OLD.authority_generation,OLD.job_id,OLD.worker_id,OLD.attempt_no,OLD.audio,OLD.created_at)
      OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
      OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','CLOSED'))
      OR (OLD.child_pid IS NOT NULL AND
          ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at) IS DISTINCT FROM
          ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at))
      OR (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
     RAISE EXCEPTION 'Metadata execution ownership is immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 IF TG_OP='INSERT' OR NEW.state='RUNNING' THEN
   IF NOT EXISTS (
     SELECT 1 FROM jobs.job j
     JOIN account.user_account a ON a.user_id=j.user_id
     JOIN library.user_track_ref r ON r.user_track_ref_id=NEW.user_track_ref_id AND
    r.user_id=a.user_id
     JOIN library.library_entry e ON e.user_track_ref_id=r.user_track_ref_id AND e.user_id=a.user_id
     JOIN library.track_metadata m ON m.user_track_ref_id=r.user_track_ref_id AND m.job_id=j.job_id
     WHERE j.job_id=NEW.job_id AND a.user_id=NEW.user_id AND a.status='ACTIVE'
       AND a.deleted_at IS NULL AND a.authority_generation=NEW.authority_generation
       AND r.deleted_at IS NULL AND e.removed_at IS NULL AND m.generation=NEW.generation
       AND j.job_type='music.metadata.enrich' AND j.schema_version=1 AND j.state='RUNNING'
       AND j.lease_owner=NEW.worker_id AND j.attempt_count=NEW.attempt_no
       AND j.cancel_requested_at IS NULL AND j.lease_deadline>clock_timestamp()
       AND (NEW.io_deadline_at IS NULL OR NEW.io_deadline_at<=j.lease_deadline)
       AND j.payload->>'user_track_ref_id'=NEW.user_track_ref_id::text
       AND j.payload->'generation'=to_jsonb(NEW.generation)
       AND j.payload->'authority_generation'=to_jsonb(NEW.authority_generation)
   ) THEN
     RAISE EXCEPTION 'metadata_execution_stale' USING ERRCODE='55000';
   END IF;
   IF NEW.audio IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM library.user_track_ref r
     JOIN catalog.recording cr ON cr.recording_id=r.recording_id AND cr.deleted_at IS NULL
     JOIN vault.recording_canonical_variant c ON c.recording_id=r.recording_id
     JOIN vault.audio_variant v ON v.audio_variant_id=c.audio_variant_id
     JOIN vault.vault_object o ON o.vault_object_id=v.vault_object_id
     JOIN vault.vault_replica p ON p.vault_object_id=o.vault_object_id
     WHERE r.user_track_ref_id=NEW.user_track_ref_id AND r.resolution_status='RESOLVED'
       AND v.validation_status='VALID' AND v.deleted_at IS NULL AND o.commit_status='COMMITTED'
       AND p.storage_backend='LOCAL_FILESYSTEM' AND p.replica_status='AVAILABLE' AND p.verified_at
    IS NOT NULL
       AND NOT EXISTS(SELECT 1 FROM identity.recording_redirect d WHERE
    d.source_recording_id=r.recording_id)
       AND NEW.audio=jsonb_build_object('recording_id',r.recording_id::text,
         'audio_variant_id',v.audio_variant_id::text,'vault_object_id',o.vault_object_id::text,
         'storage_key',p.storage_key,'sha256',encode(o.sha256,'hex'),'byte_size',o.byte_size)
   ) THEN
     RAISE EXCEPTION 'metadata_audio_stale' USING ERRCODE='55000';
   END IF;
 END IF;
 IF NEW.state='RUNNING' AND (NEW.io_deadline_at<=clock_timestamp()
    OR (OLD.state='RUNNING' AND OLD.io_deadline_at<=clock_timestamp())) THEN
   RAISE EXCEPTION 'metadata_execution_stale' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER metadata_execution_guard BEFORE INSERT OR UPDATE OR DELETE ON
    library.metadata_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_metadata_execution();
CREATE FUNCTION app_private.protect_metadata_provider_gate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR NEW.next_request_at<OLD.next_request_at
    OR (OLD.execution_id IS NOT NULL AND NEW.execution_id IS NOT NULL AND
        ROW(NEW.execution_id,NEW.request_id) IS DISTINCT FROM
    ROW(OLD.execution_id,OLD.request_id)) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
 END IF;
 IF OLD.execution_id IS NULL AND NEW.execution_id IS NOT NULL AND (
     OLD.next_request_at>clock_timestamp() OR NOT EXISTS (
       SELECT 1 FROM library.metadata_execution e WHERE e.execution_id=NEW.execution_id
       AND e.state='RUNNING' AND e.io_deadline_at>clock_timestamp())) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
 END IF;
 IF OLD.execution_id IS NOT NULL AND NEW.execution_id IS NULL AND NOT EXISTS (
     SELECT 1 FROM library.metadata_execution e WHERE e.execution_id=OLD.execution_id
     AND (e.state='CLOSED' OR (e.state='RUNNING' AND e.io_deadline_at>clock_timestamp()))) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER metadata_provider_gate_guard BEFORE UPDATE OR DELETE ON
    library.metadata_provider_gate
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_metadata_provider_gate();
CREATE OR REPLACE FUNCTION app_private.admit_internal_io() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE capacity INTEGER; occupied BIGINT;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801::BIGINT);
 SELECT p.active_limit INTO capacity FROM account.internal_io_policy p
 JOIN account.server_instance s ON s.server_instance_id=p.server_instance_id
   AND s.identity_epoch=p.identity_epoch
 JOIN account.resource_quota_policy q ON q.singleton_id=1
   AND q.playback_ceiling<=p.playback_ceiling AND q.transfer_ceiling<=p.transfer_ceiling
 WHERE p.singleton_id=1 AND (TG_TABLE_NAME<>'metadata_execution' OR p.workload_version>=2);
 IF capacity IS NULL THEN
   RAISE EXCEPTION 'internal_io_budget_unconfigured' USING ERRCODE='55000';
 END IF;
 SELECT (SELECT count(*) FROM vault.ingest_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.provider_maintenance WHERE closed_at IS NULL)
      + (SELECT count(*) FROM library.metadata_execution WHERE closed_at IS NULL) INTO occupied;
 IF occupied>=capacity THEN
   RAISE EXCEPTION 'internal_io_busy' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER a_internal_io_admission BEFORE INSERT ON library.metadata_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.admit_internal_io();
CREATE FUNCTION app_private.protect_internal_io_workload() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR NEW.workload_version<OLD.workload_version THEN
   RAISE EXCEPTION 'internal_io_workload_downgrade' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER internal_io_workload_guard BEFORE UPDATE OR DELETE ON account.internal_io_policy
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_internal_io_workload();
REVOKE ALL ON TABLE library.metadata_execution, library.metadata_provider_gate FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_metadata_execution() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_metadata_provider_gate() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_internal_io_workload() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.internal_io_policy, library.metadata_execution,
 library.metadata_provider_gate IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM library.metadata_execution)
    OR EXISTS(SELECT 1 FROM library.metadata_provider_gate WHERE execution_id IS NOT NULL)
    OR EXISTS(SELECT 1 FROM account.internal_io_policy WHERE workload_version=2 AND active_limit
    IS NOT NULL) THEN
   RAISE EXCEPTION 'Refusing to discard metadata execution or measured workload history';
 END IF;
END $$;
DROP TABLE library.metadata_provider_gate;
DROP TABLE library.metadata_execution;
DROP FUNCTION app_private.protect_metadata_provider_gate();
DROP FUNCTION app_private.protect_metadata_execution();
DROP TRIGGER internal_io_workload_guard ON account.internal_io_policy;
DROP FUNCTION app_private.protect_internal_io_workload();
ALTER TABLE account.internal_io_policy DROP CONSTRAINT internal_io_workload_version_check;
ALTER TABLE account.internal_io_policy DROP COLUMN workload_version;
CREATE OR REPLACE FUNCTION app_private.admit_internal_io() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE capacity INTEGER; occupied BIGINT;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801::BIGINT);
 SELECT p.active_limit INTO capacity FROM account.internal_io_policy p
 JOIN account.server_instance s ON s.server_instance_id=p.server_instance_id
   AND s.identity_epoch=p.identity_epoch
 JOIN account.resource_quota_policy q ON q.singleton_id=1
   AND q.playback_ceiling<=p.playback_ceiling AND q.transfer_ceiling<=p.transfer_ceiling
 WHERE p.singleton_id=1;
 IF capacity IS NULL THEN
   RAISE EXCEPTION 'internal_io_budget_unconfigured' USING ERRCODE='55000';
 END IF;
 SELECT (SELECT count(*) FROM vault.ingest_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.provider_maintenance WHERE closed_at IS NULL) INTO occupied;
 IF occupied>=capacity THEN
   RAISE EXCEPTION 'internal_io_busy' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
    """)
