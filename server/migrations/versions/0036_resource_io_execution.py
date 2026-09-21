"""Retain capacity for process executions until confirmed exit."""

from alembic import op

revision = "0036_resource_io_execution"
down_revision = "0035_resource_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE account.resource_io_permit ADD CONSTRAINT io_permit_execution_key UNIQUE (permit_id, operation_id, activation_id, generation, target_id);

CREATE TABLE account.resource_io_execution (
	execution_id UUID NOT NULL,
	permit_id UUID NOT NULL,
	operation_id UUID NOT NULL,
	activation_id UUID NOT NULL,
	generation BIGINT NOT NULL,
	target_id UUID NOT NULL,
	owner_run_id UUID NOT NULL,
	kind TEXT NOT NULL,
	actual_target_id UUID NOT NULL,
	state TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL,
	child_pid BIGINT,
	child_identity_sha256 BYTEA,
	started_at TIMESTAMP WITH TIME ZONE,
	stop_requested_at TIMESTAMP WITH TIME ZONE,
	closed_at TIMESTAMP WITH TIME ZONE,
	exit_code BIGINT,
	closure_kind TEXT,
	closure_evidence_sha256 BYTEA,
	CONSTRAINT resource_io_execution_pkey PRIMARY KEY (execution_id),
	CONSTRAINT io_execution_permit_key UNIQUE (permit_id),
	CONSTRAINT io_execution_permit_fkey FOREIGN KEY(permit_id, operation_id, activation_id, generation, target_id) REFERENCES account.resource_io_permit (permit_id, operation_id, activation_id, generation, target_id),
	CONSTRAINT io_execution_generation_check CHECK (generation>=1),
	CONSTRAINT io_execution_kind_check CHECK (kind IN ('VAULT_STREAM','VAULT_UPLOAD','PROVIDER')),
	CONSTRAINT io_execution_state_check CHECK (state IN ('PREPARED','RUNNING','STOPPING','ORPHANED','CLOSED')),
	CONSTRAINT io_execution_timestamps_check CHECK (heartbeat_at>=created_at AND (started_at IS NULL OR started_at>=created_at) AND (stop_requested_at IS NULL OR stop_requested_at>=created_at) AND (closed_at IS NULL OR closed_at>=heartbeat_at)),
	CONSTRAINT io_execution_child_check CHECK ((child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL) OR (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL)),
	CONSTRAINT io_execution_running_check CHECK ((state<>'PREPARED' OR child_pid IS NULL) AND (state<>'RUNNING' OR child_pid IS NOT NULL) AND (state<>'STOPPING' OR stop_requested_at IS NOT NULL)),
	CONSTRAINT io_execution_closure_check CHECK ((state<>'CLOSED' AND closed_at IS NULL AND exit_code IS NULL AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL) OR (state='CLOSED' AND closed_at IS NOT NULL AND closure_kind IS NOT NULL AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL))))
)

;
CREATE INDEX ix_io_execution_owner ON account.resource_io_execution (owner_run_id, heartbeat_at) WHERE closed_at IS NULL;
CREATE INDEX ix_io_execution_unclosed ON account.resource_io_execution (operation_id) WHERE closed_at IS NULL;
CREATE UNIQUE INDEX ix_io_execution_writer ON account.resource_io_execution (kind, actual_target_id) WHERE closed_at IS NULL AND kind IN ('VAULT_UPLOAD','PROVIDER');

CREATE FUNCTION account.guard_resource_io_execution() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP='DELETE' THEN
    IF OLD.closed_at IS NULL THEN
        RAISE EXCEPTION 'Cannot discard unclosed resource execution' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END IF;
IF (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','STOPPING','ORPHANED','CLOSED'))
   OR (OLD.state='STOPPING' AND NEW.state NOT IN ('STOPPING','ORPHANED','CLOSED'))
   OR (OLD.state='ORPHANED' AND NEW.state NOT IN ('ORPHANED','CLOSED'))
   OR (OLD.state='ORPHANED' AND NEW.closure_kind='NOT_STARTED')
   OR (OLD.child_pid IS NULL AND NEW.child_pid IS NOT NULL
       AND NOT (OLD.state='PREPARED' AND NEW.state='RUNNING'))
   OR NEW.heartbeat_at<OLD.heartbeat_at
   OR (OLD.stop_requested_at IS NOT NULL
       AND NEW.stop_requested_at IS DISTINCT FROM OLD.stop_requested_at) THEN
    RAISE EXCEPTION 'Resource execution cannot resume or weaken stop evidence' USING ERRCODE='23514';
END IF;
IF ROW(NEW.execution_id,NEW.permit_id,NEW.operation_id,NEW.activation_id,NEW.generation,
       NEW.target_id,NEW.owner_run_id,NEW.kind,NEW.actual_target_id,NEW.created_at)
   IS DISTINCT FROM
   ROW(OLD.execution_id,OLD.permit_id,OLD.operation_id,OLD.activation_id,OLD.generation,
       OLD.target_id,OLD.owner_run_id,OLD.kind,OLD.actual_target_id,OLD.created_at)
   OR (OLD.closed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
   OR (OLD.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at)
       IS DISTINCT FROM ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at)) THEN
    RAISE EXCEPTION 'Resource execution identity or closure is immutable' USING ERRCODE='23514';
END IF;
RETURN NEW;
END $$;
CREATE TRIGGER resource_io_execution_guard BEFORE UPDATE OR DELETE
ON account.resource_io_execution FOR EACH ROW EXECUTE FUNCTION account.guard_resource_io_execution();
REVOKE ALL ON account.resource_io_execution FROM PUBLIC;
REVOKE ALL ON FUNCTION account.guard_resource_io_execution() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
IF EXISTS(SELECT 1 FROM account.resource_io_execution) THEN
    RAISE EXCEPTION 'Refusing to discard resource execution evidence';
END IF;
END $$;
DROP TABLE account.resource_io_execution;
DROP FUNCTION account.guard_resource_io_execution();
ALTER TABLE account.resource_io_permit DROP CONSTRAINT io_permit_execution_key;
    """)
