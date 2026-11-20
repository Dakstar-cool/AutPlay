"""Resume finalized cleanup after verified offline WORK death; retain all evidence."""

from alembic import op

revision = "0088_ingest_offline_cleanup"
down_revision = "0087_normalized_sync_ingress"
branch_labels = None
depends_on = None

# Preserve the existing guard, including 0053's transaction-scoped privacy deletion.
_PRIVACY_DELETE = (
    "IF TG_OP='DELETE' AND app_private.privacy_entity_allowed("
    "TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,OLD.execution_id) THEN RETURN OLD; END IF;"
)
_GUARD = """
CREATE OR REPLACE FUNCTION app_private.protect_ingest_cleanup_execution() RETURNS trigger
 LANGUAGE plpgsql
   AS $$ BEGIN
{privacy_delete}
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Ingest cleanup ownership cannot be deleted' USING
   ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
   PERFORM 1 FROM vault.upload_session WHERE upload_session_id=NEW.claim_id FOR UPDATE;
   IF NEW.state<>'PREPARED' OR NOT EXISTS (
     SELECT 1 FROM vault.ingest_cleanup_claim c JOIN vault.ingest_execution w ON
   w.execution_id=c.work_execution_id
     WHERE c.claim_id=NEW.claim_id AND c.completed_at IS NULL AND w.state='CLOSED'
       AND {work_exit})
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
""".replace("{privacy_delete}", _PRIVACY_DELETE)


def upgrade() -> None:
    op.execute(
        _GUARD.format(
            work_exit="""
      w.closure_kind IN ('PROCESS_EXIT','SUPERVISOR_EXIT')
      AND w.upload_session_id=c.claim_id AND w.staging_key=c.staging_key
      AND w.child_pid IS NOT NULL AND w.child_identity_sha256 IS NOT NULL
      AND octet_length(w.child_identity_sha256)=32 AND w.closed_at IS NOT NULL
      AND w.closure_evidence_sha256 IS NOT NULL
      AND octet_length(w.closure_evidence_sha256)=32 AND w.exit_code IS NOT NULL
    """
        )
    )


def downgrade() -> None:
    # Do not restore a guard that could no longer accept retained cleanup facts.
    # The locks serialize this check with offline drain and cleanup preparation.
    op.execute("""
LOCK TABLE vault.upload_session, vault.ingest_execution, vault.ingest_cleanup_claim,
 vault.ingest_cleanup_execution IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (
   SELECT 1 FROM vault.ingest_cleanup_claim c
   JOIN vault.ingest_execution w ON w.execution_id=c.work_execution_id
   WHERE w.closure_kind='SUPERVISOR_EXIT'
 ) THEN
   RAISE EXCEPTION 'Refusing to downgrade retained offline WORK cleanup evidence'
     USING ERRCODE='23514';
 END IF;
END $$;
    """)
    op.execute(_GUARD.format(work_exit="w.closure_kind='PROCESS_EXIT'"))
