"""Retain platform-bound Linux cgroup incarnation for bounded ingest startup."""

from alembic import op

revision = "0091_ingest_linux_owner"
down_revision = "0090_ingest_startup_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE vault.ingest_process_owner ADD COLUMN platform TEXT NOT NULL DEFAULT 'WINDOWS';
ALTER TABLE vault.ingest_process_owner ADD COLUMN tree_device BIGINT;
ALTER TABLE vault.ingest_process_owner ADD COLUMN tree_inode BIGINT;
ALTER TABLE vault.ingest_process_owner DROP CONSTRAINT ingest_process_owner_identity_check;
ALTER TABLE vault.ingest_process_owner ADD CONSTRAINT ingest_process_owner_identity_check CHECK (
 octet_length(host_sha256)=32 AND owner_pid BETWEEN 1 AND 4294967295 AND owner_created>0
 AND ((platform='WINDOWS' AND tree_name='Local\AutPlay-Ingest-'||replace(execution_id::text,'-',''))
   OR (platform='LINUX' AND tree_name='autplay-ingest-'||replace(execution_id::text,'-',''))));
ALTER TABLE vault.ingest_process_owner ADD CONSTRAINT ingest_process_owner_tree_check CHECK (
 (platform='WINDOWS' AND tree_device IS NULL AND tree_inode IS NULL) OR
 (platform='LINUX' AND ((child_pid IS NULL AND tree_device IS NULL AND tree_inode IS NULL) OR
   (child_pid IS NOT NULL AND tree_device IS NOT NULL AND tree_device>0
      AND tree_inode IS NOT NULL AND tree_inode>0))));
CREATE OR REPLACE FUNCTION app_private.protect_ingest_process_owner() RETURNS trigger
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
       NEW.host_sha256,NEW.owner_pid,NEW.owner_created,NEW.tree_name,NEW.platform) IS DISTINCT FROM
      ROW(OLD.execution_id,OLD.work_execution_id,OLD.cleanup_execution_id,OLD.owner_run_id,
       OLD.host_sha256,OLD.owner_pid,OLD.owner_created,OLD.tree_name,OLD.platform) OR
      (OLD.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_created,NEW.child_identity_sha256)
       IS DISTINCT FROM ROW(OLD.child_pid,OLD.child_created,OLD.child_identity_sha256)) OR
      (OLD.tree_device IS NOT NULL AND ROW(NEW.tree_device,NEW.tree_inode)
       IS DISTINCT FROM ROW(OLD.tree_device,OLD.tree_inode)) OR
      NEW.inspection_count<OLD.inspection_count OR
      (OLD.checked_at IS NOT NULL AND (NEW.checked_at IS NULL OR NEW.checked_at<OLD.checked_at)) OR
      (OLD.closed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
     RAISE EXCEPTION 'Ingest process registration is immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;
""")


def downgrade() -> None:
    op.execute(r"""
LOCK TABLE vault.ingest_process_owner IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM vault.ingest_process_owner WHERE platform='LINUX') THEN
   RAISE EXCEPTION 'Retained Linux ingest ownership prevents downgrade' USING ERRCODE='23514';
 END IF;
END $$;
ALTER TABLE vault.ingest_process_owner DROP CONSTRAINT ingest_process_owner_tree_check;
ALTER TABLE vault.ingest_process_owner DROP CONSTRAINT ingest_process_owner_identity_check;
ALTER TABLE vault.ingest_process_owner ADD CONSTRAINT ingest_process_owner_identity_check CHECK (
 octet_length(host_sha256)=32 AND owner_pid BETWEEN 1 AND 4294967295 AND owner_created>0
 AND tree_name='Local\AutPlay-Ingest-'||replace(execution_id::text,'-',''));
ALTER TABLE vault.ingest_process_owner DROP COLUMN tree_inode;
ALTER TABLE vault.ingest_process_owner DROP COLUMN tree_device;
ALTER TABLE vault.ingest_process_owner DROP COLUMN platform;
CREATE OR REPLACE FUNCTION app_private.protect_ingest_process_owner() RETURNS trigger
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
""")
