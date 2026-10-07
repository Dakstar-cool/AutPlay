"""Share retained MusicBrainz occupancy with authenticated catalog executors."""

from alembic import op
from sqlalchemy import text

revision = "0064_metadata_catalog_gate"
down_revision = "0063_social_public_id"
branch_labels = None
depends_on = None


def _extend_privacy(name: str, replacements: tuple[tuple[str, str], ...]) -> None:
    connection = op.get_bind()
    original = connection.scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name}
    )
    if not isinstance(original, str):
        raise RuntimeError("missing catalog privacy definition")
    changed = original
    for anchor, replacement in replacements:
        if changed.count(anchor) != 1:
            raise RuntimeError("unexpected catalog privacy definition")
        changed = changed.replace(anchor, replacement, 1)
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0064:" + name, "ddl": original},
    )
    op.execute(changed)


def upgrade() -> None:
    op.execute("""
CREATE TABLE library.catalog_execution (
 execution_id UUID PRIMARY KEY, owner_run_id UUID NOT NULL,
 user_id UUID NOT NULL, device_id UUID NOT NULL, session_id UUID NOT NULL,
 authority_generation BIGINT NOT NULL, request_id UUID NOT NULL, state TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL, deadline_at TIMESTAMPTZ NOT NULL,
 started_at TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ, io_deadline_at TIMESTAMPTZ,
 closed_at TIMESTAMPTZ, child_pid BIGINT, child_identity_sha256 BYTEA,
 closure_kind TEXT, closure_evidence_sha256 BYTEA, exit_code BIGINT,
 CONSTRAINT catalog_execution_actor_fkey FOREIGN KEY(user_id,device_id,session_id)
 REFERENCES account.user_session(user_id,device_id,session_id),
 CONSTRAINT catalog_execution_target_check CHECK (
  authority_generation>0 AND state IN ('PREPARED','RUNNING','CLOSED')
  AND deadline_at>created_at AND deadline_at<=created_at+interval '30 seconds'),
 CONSTRAINT catalog_execution_child_check CHECK (
  (child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL
   AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR
  (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL
   AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL
   AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at
   AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at
   AND io_deadline_at<=heartbeat_at+interval '5 seconds' AND io_deadline_at<=deadline_at)),
 CONSTRAINT catalog_execution_times_check CHECK (
  (state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR
  (state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR
  (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at
   AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))),
 CONSTRAINT catalog_execution_closure_check CHECK (
  (state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL
   AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL
   AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32
   AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL)
    OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL)
    OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL))))
);
ALTER TABLE library.metadata_provider_gate ADD COLUMN catalog_execution_id UUID
 REFERENCES library.catalog_execution(execution_id);
ALTER TABLE library.metadata_provider_gate DROP CONSTRAINT metadata_provider_gate_owner_check;
ALTER TABLE library.metadata_provider_gate ADD CONSTRAINT metadata_provider_gate_owner_check
 CHECK ((request_id IS NULL AND execution_id IS NULL AND catalog_execution_id IS NULL) OR
 (request_id IS NOT NULL AND ((execution_id IS NOT NULL AND catalog_execution_id IS NULL)
 OR (execution_id IS NULL AND catalog_execution_id IS NOT NULL))));

CREATE FUNCTION app_private.protect_catalog_execution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  IF OLD.state='CLOSED' AND OLD.closed_at IS NOT NULL
   AND app_private.privacy_entity_allowed('library.catalog_execution',OLD.execution_id) THEN
   RETURN OLD;
  END IF;
  RAISE EXCEPTION 'catalog_execution_immutable' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PREPARED' OR NEW.deadline_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'metadata_catalog_stale' USING ERRCODE='55000';
  END IF;
 ELSE
  IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.user_id,NEW.device_id,NEW.session_id,
    NEW.authority_generation,NEW.request_id,NEW.created_at,NEW.deadline_at) IS DISTINCT FROM
   ROW(OLD.execution_id,OLD.owner_run_id,OLD.user_id,OLD.device_id,OLD.session_id,
    OLD.authority_generation,OLD.request_id,OLD.created_at,OLD.deadline_at)
   OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
   OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','CLOSED'))
   OR (OLD.child_pid IS NOT NULL AND
    ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at) IS DISTINCT FROM
    ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at))
   OR (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
   RAISE EXCEPTION 'catalog_execution_immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 IF TG_OP='INSERT' OR NEW.state='RUNNING' THEN
  IF NOT EXISTS (
   SELECT 1 FROM account.user_account a
   JOIN account.device d ON d.user_id=a.user_id
   JOIN account.user_session s ON s.user_id=a.user_id AND s.device_id=d.device_id
   WHERE a.user_id=NEW.user_id AND a.status='ACTIVE' AND a.deleted_at IS NULL
   AND a.authority_generation=NEW.authority_generation AND d.device_id=NEW.device_id
   AND d.revoked_at IS NULL AND s.session_id=NEW.session_id AND s.revoked_at IS NULL
   AND s.expires_at>clock_timestamp()
  ) THEN
   RAISE EXCEPTION 'metadata_catalog_unauthorized' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.state='RUNNING' AND (NEW.io_deadline_at<=clock_timestamp()
  OR (OLD.state='RUNNING' AND OLD.io_deadline_at<=clock_timestamp())
  OR NOT EXISTS (SELECT 1 FROM library.metadata_provider_gate g
   WHERE g.catalog_execution_id=NEW.execution_id AND g.request_id=NEW.request_id)) THEN
  RAISE EXCEPTION 'metadata_catalog_stale' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER catalog_execution_guard BEFORE INSERT OR UPDATE OR DELETE
 ON library.catalog_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_catalog_execution();

CREATE OR REPLACE FUNCTION app_private.protect_metadata_provider_gate() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR NEW.next_request_at<OLD.next_request_at
 OR (OLD.request_id IS NOT NULL AND NEW.request_id IS NOT NULL AND
  ROW(NEW.execution_id,NEW.catalog_execution_id,NEW.request_id) IS DISTINCT FROM
  ROW(OLD.execution_id,OLD.catalog_execution_id,OLD.request_id)) THEN
  RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
 END IF;
 IF OLD.request_id IS NULL AND NEW.request_id IS NOT NULL THEN
  IF OLD.next_request_at>clock_timestamp() THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
  END IF;
  IF NEW.execution_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM library.metadata_execution e WHERE e.execution_id=NEW.execution_id
   AND e.state='RUNNING' AND e.io_deadline_at>clock_timestamp()) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
  END IF;
  IF NEW.catalog_execution_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM library.catalog_execution e WHERE e.execution_id=NEW.catalog_execution_id
   AND e.request_id=NEW.request_id AND e.state='PREPARED' AND e.deadline_at>clock_timestamp()) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
  END IF;
 END IF;
 IF OLD.request_id IS NOT NULL AND NEW.request_id IS NULL THEN
  IF OLD.execution_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM library.metadata_execution e WHERE e.execution_id=OLD.execution_id
   AND (e.state='CLOSED' OR (e.state='RUNNING' AND e.io_deadline_at>clock_timestamp()))) THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
  END IF;
  IF OLD.catalog_execution_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM library.catalog_execution e WHERE e.execution_id=OLD.catalog_execution_id
   AND e.request_id=OLD.request_id AND e.state='CLOSED') THEN
   RAISE EXCEPTION 'metadata_provider_gate_stale' USING ERRCODE='55000';
  END IF;
  NEW.next_request_at := greatest(NEW.next_request_at,
   clock_timestamp()+interval '1100 milliseconds');
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON TABLE library.catalog_execution FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_catalog_execution() FROM PUBLIC;
""")
    ready = " OR EXISTS(SELECT 1 FROM library.metadata_execution WHERE user_id=target AND"
    _extend_privacy(
        "account.verify_account_purge_ready(uuid)",
        (
            (
                ready,
                " OR EXISTS(SELECT 1 FROM library.catalog_execution WHERE user_id=target AND "
                "(state<>'CLOSED' OR closed_at IS NULL))\n" + ready,
            ),
        ),
    )
    entity = " UNION ALL SELECT transaction_key,'library.metadata_execution',execution_id"
    clear = " UPDATE library.metadata_provider_gate SET execution_id=NULL,request_id=NULL"
    delete = "$q$DELETE FROM library.metadata_execution WHERE user_id=$1$q$"
    _extend_privacy(
        "account.purge_account(uuid,uuid)",
        (
            (
                entity,
                " UNION ALL SELECT transaction_key,'library.catalog_execution',execution_id\n"
                " FROM library.catalog_execution WHERE user_id=target\n" + entity,
            ),
            (
                clear,
                " UPDATE library.metadata_provider_gate "
                "SET catalog_execution_id=NULL,request_id=NULL\n"
                " WHERE catalog_execution_id IN "
                "(SELECT execution_id FROM library.catalog_execution\n"
                " WHERE user_id=target);\n" + clear,
            ),
            (delete, "$q$DELETE FROM library.catalog_execution WHERE user_id=$1$q$,\n " + delete),
        ),
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE library.catalog_execution, library.metadata_provider_gate IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM library.catalog_execution)
 OR EXISTS(SELECT 1 FROM library.metadata_provider_gate WHERE catalog_execution_id IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing to discard catalog process ownership or exit history'
   USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    for name in ("account.purge_account(uuid,uuid)", "account.verify_account_purge_ready(uuid)"):
        original = connection.scalar(
            text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
            {"key": "0064:" + name},
        )
        if not isinstance(original, str):
            raise RuntimeError("missing catalog privacy rollback definition")
        op.execute(original)
        connection.execute(
            text("DELETE FROM app_private.privacy_prior_definition WHERE name=:key"),
            {"key": "0064:" + name},
        )
    op.execute("""
ALTER TABLE library.metadata_provider_gate DROP CONSTRAINT metadata_provider_gate_owner_check;
ALTER TABLE library.metadata_provider_gate DROP COLUMN catalog_execution_id;
ALTER TABLE library.metadata_provider_gate ADD CONSTRAINT metadata_provider_gate_owner_check
 CHECK ((execution_id IS NULL)=(request_id IS NULL));
DROP TABLE library.catalog_execution;
DROP FUNCTION app_private.protect_catalog_execution();
CREATE OR REPLACE FUNCTION app_private.protect_metadata_provider_gate() RETURNS trigger
 LANGUAGE plpgsql AS $$
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
""")
