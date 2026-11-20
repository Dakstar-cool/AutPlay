"""Retain exact Windows ingest incarnations and fair startup exit receipts."""

from alembic import op

revision = "0090_ingest_startup_authority"
down_revision = "0089_normalized_listen_accept"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE vault.ingest_process_owner (
 execution_id UUID PRIMARY KEY,
 work_execution_id UUID REFERENCES vault.ingest_execution(execution_id) ON DELETE CASCADE,
 cleanup_execution_id UUID REFERENCES vault.ingest_cleanup_execution(execution_id)
   ON DELETE CASCADE,
 owner_run_id UUID NOT NULL,
 host_sha256 BYTEA NOT NULL,
 owner_pid BIGINT NOT NULL,
 owner_created BIGINT NOT NULL,
 tree_name TEXT NOT NULL UNIQUE,
 child_pid BIGINT,
 child_created BIGINT,
 child_identity_sha256 BYTEA,
 checked_at TIMESTAMPTZ,
 inspection_count BIGINT NOT NULL DEFAULT 0,
 closed_at TIMESTAMPTZ,
 exit_receipt_sha256 BYTEA,
 exit_receipt JSONB,
 CONSTRAINT ingest_process_owner_execution_check CHECK (
   num_nonnulls(work_execution_id,cleanup_execution_id)=1
   AND execution_id=coalesce(work_execution_id,cleanup_execution_id)),
 CONSTRAINT ingest_process_owner_identity_check CHECK (
   octet_length(host_sha256)=32 AND owner_pid BETWEEN 1 AND 4294967295 AND owner_created>0
   AND tree_name='Local\AutPlay-Ingest-'||replace(execution_id::text,'-','')),
 CONSTRAINT ingest_process_owner_child_check CHECK (
   (child_pid IS NULL AND child_created IS NULL AND child_identity_sha256 IS NULL) OR
   (child_pid IS NOT NULL AND child_created IS NOT NULL AND child_identity_sha256 IS NOT NULL
    AND child_pid BETWEEN 1 AND 4294967295 AND child_created>0
    AND octet_length(child_identity_sha256)=32)),
 CONSTRAINT ingest_process_owner_schedule_check CHECK (
   inspection_count>=0 AND (checked_at IS NULL)=(inspection_count=0)),
 CONSTRAINT ingest_process_owner_exit_check CHECK (
   (closed_at IS NULL AND exit_receipt_sha256 IS NULL AND exit_receipt IS NULL) OR
   (closed_at IS NOT NULL AND exit_receipt_sha256 IS NOT NULL
    AND octet_length(exit_receipt_sha256)=32
    AND (exit_receipt IS NULL OR (jsonb_typeof(exit_receipt)='object'
       AND octet_length(exit_receipt::text)<=4096))))
);
CREATE INDEX ix_ingest_process_owner_pending ON vault.ingest_process_owner
 (host_sha256,checked_at NULLS FIRST,execution_id) WHERE closed_at IS NULL;
ALTER TABLE vault.ingest_cleanup_claim ADD COLUMN checked_at TIMESTAMPTZ;
CREATE INDEX ix_ingest_cleanup_fair_pending ON vault.ingest_cleanup_claim
 (checked_at NULLS FIRST,claim_id) WHERE completed_at IS NULL;
CREATE FUNCTION app_private.protect_ingest_process_owner() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE target RECORD;
BEGIN
 IF TG_OP='DELETE' THEN
   IF app_private.privacy_entity_allowed(
      CASE WHEN OLD.work_execution_id IS NOT NULL THEN 'vault.ingest_execution'
      ELSE 'vault.ingest_cleanup_execution' END,OLD.execution_id) THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'Ingest process ownership cannot be deleted' USING ERRCODE='23514';
 END IF;
 IF NEW.work_execution_id IS NOT NULL THEN
   SELECT state,owner_run_id,child_pid,child_identity_sha256,closed_at,closure_evidence_sha256
   INTO target FROM vault.ingest_execution WHERE execution_id=NEW.execution_id;
 ELSE
   SELECT state,owner_run_id,child_pid,child_identity_sha256,closed_at,closure_evidence_sha256
   INTO target FROM vault.ingest_cleanup_execution WHERE execution_id=NEW.execution_id;
 END IF;
 IF NOT FOUND OR target.owner_run_id<>NEW.owner_run_id OR
   (NEW.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_identity_sha256)
      IS DISTINCT FROM ROW(target.child_pid,target.child_identity_sha256)) OR
   (NEW.closed_at IS NOT NULL AND ROW(NEW.closed_at,NEW.exit_receipt_sha256)
      IS DISTINCT FROM ROW(target.closed_at,target.closure_evidence_sha256)) THEN
   RAISE EXCEPTION 'Ingest process registration mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
   IF target.state<>'PREPARED' OR NEW.child_pid IS NOT NULL OR NEW.checked_at IS NOT NULL
     OR NEW.closed_at IS NOT NULL THEN
     RAISE EXCEPTION 'Ingest process registration must precede spawn' USING ERRCODE='23514';
   END IF;
 ELSE
   IF ROW(NEW.execution_id,NEW.work_execution_id,NEW.cleanup_execution_id,NEW.owner_run_id,
       NEW.host_sha256,NEW.owner_pid,NEW.owner_created,NEW.tree_name) IS DISTINCT FROM
      ROW(OLD.execution_id,OLD.work_execution_id,OLD.cleanup_execution_id,OLD.owner_run_id,
       OLD.host_sha256,OLD.owner_pid,OLD.owner_created,OLD.tree_name) OR
      (OLD.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_created,NEW.child_identity_sha256)
       IS DISTINCT FROM ROW(OLD.child_pid,OLD.child_created,OLD.child_identity_sha256)) OR
      NEW.inspection_count<OLD.inspection_count OR
      (OLD.checked_at IS NOT NULL AND (NEW.checked_at IS NULL OR NEW.checked_at<OLD.checked_at)) OR
      (OLD.closed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
     RAISE EXCEPTION 'Ingest process registration is immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app_private.protect_ingest_process_owner() FROM PUBLIC;
CREATE TRIGGER ingest_process_owner_guard BEFORE INSERT OR UPDATE OR DELETE
 ON vault.ingest_process_owner FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ingest_process_owner();
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.ingest_process_owner IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM vault.ingest_process_owner) THEN
   RAISE EXCEPTION 'Refusing to downgrade retained ingest process authority' USING ERRCODE='23514';
 END IF;
END $$;
DROP TABLE vault.ingest_process_owner;
DROP FUNCTION app_private.protect_ingest_process_owner();
DROP INDEX vault.ix_ingest_cleanup_fair_pending;
ALTER TABLE vault.ingest_cleanup_claim DROP COLUMN checked_at;
    """)
