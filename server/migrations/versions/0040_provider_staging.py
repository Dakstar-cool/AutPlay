"""Persist provider staging ownership and exact closure independently of quota GC."""

from alembic import op

revision = "0040_provider_staging"
down_revision = "0039_internet_ingest_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE vault.provider_staging (
execution_id UUID NOT NULL,
user_id UUID NOT NULL,
resource_type TEXT NOT NULL,
acquisition_id UUID NOT NULL,
job_id UUID NOT NULL,
job_worker_id TEXT NOT NULL,
job_attempt BIGINT NOT NULL,
operation_id UUID NOT NULL,
activation_id UUID NOT NULL,
generation BIGINT NOT NULL,
permit_id UUID NOT NULL,
owner_run_id UUID NOT NULL,
staging_key TEXT NOT NULL,
state TEXT NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
closed_at TIMESTAMP WITH TIME ZONE,
closure_kind TEXT,
closure_evidence_sha256 BYTEA,
exit_code BIGINT,
child_pid BIGINT,
child_identity_sha256 BYTEA,
byte_size BIGINT,
sha256 BYTEA,
sealed_at TIMESTAMP WITH TIME ZONE,
upload_session_id UUID,
handed_off_at TIMESTAMP WITH TIME ZONE,
cleanup_reason TEXT,
cleanup_claim_id UUID,
cleanup_claimed_at TIMESTAMP WITH TIME ZONE,
cleaned_at TIMESTAMP WITH TIME ZONE,
CONSTRAINT provider_staging_pkey PRIMARY KEY (execution_id),
CONSTRAINT provider_staging_key_key UNIQUE (staging_key),
CONSTRAINT provider_staging_upload_key UNIQUE (upload_session_id),
CONSTRAINT provider_staging_identity_check CHECK (resource_type IN
  ('INTERNET_ACQUISITION','DISCOVERY_ACQUISITION') AND job_attempt>=1 AND generation>=1 AND
  length(job_worker_id) BETWEEN 1 AND 200),
CONSTRAINT provider_staging_key_check CHECK (staging_key='provider-' ||
  replace(execution_id::text,'-','')),
CONSTRAINT provider_staging_state_check CHECK (state IN
  ('OWNED','EXITED','SEALED','HANDED_OFF','CLEANUP_CLAIMED','CLEANED')),
CONSTRAINT provider_staging_closure_check CHECK ((state='OWNED' AND closed_at IS NULL AND
  closure_kind IS NULL AND closure_evidence_sha256 IS NULL AND exit_code IS NULL AND child_pid
  IS NULL AND child_identity_sha256 IS NULL) OR (state<>'OWNED' AND closed_at IS NOT NULL AND
  closure_kind IS NOT NULL AND closure_evidence_sha256 IS NOT NULL AND
  octet_length(closure_evidence_sha256)=32 AND ((closure_kind='NOT_STARTED' AND exit_code IS
  NULL AND child_pid IS NULL AND child_identity_sha256 IS NULL) OR (closure_kind IN
  ('PROCESS_EXIT','SUPERVISOR_EXIT') AND exit_code IS NOT NULL AND ((child_pid IS NOT NULL AND
  child_pid>0 AND child_identity_sha256 IS NOT NULL AND octet_length(child_identity_sha256)=32)
  OR (closure_kind='SUPERVISOR_EXIT' AND child_pid IS NULL AND child_identity_sha256 IS
  NULL)))))),
CONSTRAINT provider_staging_sealed_check CHECK ((sealed_at IS NULL AND byte_size IS NULL AND
  sha256 IS NULL AND state IN ('OWNED','EXITED','CLEANUP_CLAIMED','CLEANED')) OR (sealed_at IS
  NOT NULL AND byte_size IS NOT NULL AND byte_size>0 AND sha256 IS NOT NULL AND
  octet_length(sha256)=32 AND exit_code=0 AND closure_kind IN ('PROCESS_EXIT','SUPERVISOR_EXIT')
  AND state IN ('SEALED','HANDED_OFF','CLEANUP_CLAIMED','CLEANED'))),
CONSTRAINT provider_staging_handoff_check CHECK ((state='HANDED_OFF' AND upload_session_id IS
  NOT NULL AND handed_off_at IS NOT NULL) OR (state<>'HANDED_OFF' AND upload_session_id IS NULL
  AND handed_off_at IS NULL)),
CONSTRAINT provider_staging_cleanup_check CHECK ((state IN ('CLEANUP_CLAIMED','CLEANED') AND
  cleanup_claim_id IS NOT NULL AND cleanup_claimed_at IS NOT NULL AND cleanup_reason IS NOT NULL
  AND cleanup_reason IN ('CANCELLED','FAILED','AUTHORITY_REVOKED','SUPERSEDED')) OR (state NOT
  IN ('CLEANUP_CLAIMED','CLEANED') AND cleanup_claim_id IS NULL AND cleanup_claimed_at IS NULL
  AND cleanup_reason IS NULL)),
CONSTRAINT provider_staging_timestamps_check CHECK ((state='CLEANED')=(cleaned_at IS NOT NULL)
  AND updated_at>=created_at AND (closed_at IS NULL OR closed_at>=created_at) AND (sealed_at IS
  NULL OR sealed_at>=closed_at) AND (handed_off_at IS NULL OR handed_off_at>=sealed_at) AND
  (cleanup_claimed_at IS NULL OR cleanup_claimed_at>=closed_at) AND (cleaned_at IS NULL OR
  cleaned_at>=cleanup_claimed_at)),
CONSTRAINT provider_staging_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account
  (user_id),
CONSTRAINT provider_staging_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job (job_id),
CONSTRAINT provider_staging_upload_session_id_fkey FOREIGN KEY(upload_session_id) REFERENCES
  vault.upload_session (upload_session_id)
);
CREATE INDEX ix_provider_staging_cleanup ON vault.provider_staging (state, updated_at);
CREATE INDEX ix_provider_staging_target ON vault.provider_staging (resource_type,
  acquisition_id, created_at);

CREATE FUNCTION app_private.protect_provider_staging() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF ROW(NEW.execution_id,NEW.user_id,NEW.resource_type,NEW.acquisition_id,NEW.job_id,
         NEW.job_worker_id,NEW.job_attempt,NEW.operation_id,NEW.activation_id,NEW.generation,
         NEW.permit_id,NEW.owner_run_id,NEW.staging_key,NEW.created_at)
     IS DISTINCT FROM
     ROW(OLD.execution_id,OLD.user_id,OLD.resource_type,OLD.acquisition_id,OLD.job_id,
         OLD.job_worker_id,OLD.job_attempt,OLD.operation_id,OLD.activation_id,OLD.generation,
         OLD.permit_id,OLD.owner_run_id,OLD.staging_key,OLD.created_at)
     OR (OLD.closed_at IS NOT NULL AND
       ROW(NEW.closed_at,NEW.closure_kind,NEW.closure_evidence_sha256,NEW.exit_code,
           NEW.child_pid,NEW.child_identity_sha256) IS DISTINCT FROM
       ROW(OLD.closed_at,OLD.closure_kind,OLD.closure_evidence_sha256,OLD.exit_code,
           OLD.child_pid,OLD.child_identity_sha256))
     OR (OLD.sealed_at IS NOT NULL AND
       ROW(NEW.sealed_at,NEW.byte_size,NEW.sha256) IS DISTINCT FROM
       ROW(OLD.sealed_at,OLD.byte_size,OLD.sha256))
     OR (OLD.handed_off_at IS NOT NULL AND
       ROW(NEW.handed_off_at,NEW.upload_session_id) IS DISTINCT FROM
       ROW(OLD.handed_off_at,OLD.upload_session_id))
     OR (OLD.cleanup_claimed_at IS NOT NULL AND
       ROW(NEW.cleanup_claimed_at,NEW.cleanup_claim_id,NEW.cleanup_reason) IS DISTINCT FROM
       ROW(OLD.cleanup_claimed_at,OLD.cleanup_claim_id,OLD.cleanup_reason))
     OR (OLD.cleaned_at IS NOT NULL AND NEW.cleaned_at IS DISTINCT FROM OLD.cleaned_at)
  THEN
    RAISE EXCEPTION 'Provider staging identity and receipts are immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.state<>OLD.state AND NOT (
    (OLD.state='OWNED' AND NEW.state='EXITED') OR
    (OLD.state='EXITED' AND NEW.state IN ('SEALED','CLEANUP_CLAIMED')) OR
    (OLD.state='SEALED' AND NEW.state IN ('HANDED_OFF','CLEANUP_CLAIMED')) OR
    (OLD.state='CLEANUP_CLAIMED' AND NEW.state='CLEANED'))
  THEN
    RAISE EXCEPTION 'Provider staging transition is invalid' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_staging_guard BEFORE UPDATE ON vault.provider_staging
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_provider_staging();
REVOKE ALL ON FUNCTION app_private.protect_provider_staging() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM vault.provider_staging) THEN
    RAISE EXCEPTION 'Refusing to discard provider staging ownership';
  END IF;
END $$;
DROP TRIGGER provider_staging_guard ON vault.provider_staging;
DROP FUNCTION app_private.protect_provider_staging();
DROP TABLE vault.provider_staging;
    """)
