"""Keep maintenance capacity charged until exact process exit is acknowledged."""

from alembic import op

revision = "0042_provider_maintenance"
down_revision = "0041_provider_scratch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE vault.provider_maintenance (
  execution_id UUID CONSTRAINT provider_maintenance_pkey PRIMARY KEY,
  owner_run_id UUID NOT NULL,
  provider_execution_id UUID NOT NULL
    CONSTRAINT provider_maintenance_provider_execution_id_fkey REFERENCES vault.provider_staging,
  claim_id UUID NOT NULL,
  action TEXT NOT NULL,
  singleton_id SMALLINT NOT NULL,
  state TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE,
  closed_at TIMESTAMP WITH TIME ZONE,
  child_pid BIGINT,
  child_identity_sha256 BYTEA,
  closure_kind TEXT,
  closure_evidence_sha256 BYTEA,
  exit_code BIGINT,
  CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN ('CLEANUP','SCRATCH')
    AND state IN ('PREPARED','RUNNING','CLOSED')),
  CONSTRAINT provider_maintenance_child_check CHECK (
    (child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL) OR
    (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL
    AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL
    AND started_at>=created_at)),
  CONSTRAINT provider_maintenance_times_check CHECK (
    (state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR
    (state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR
    (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at
    AND (started_at IS NULL OR closed_at>=started_at))),
  CONSTRAINT provider_maintenance_closure_check CHECK (
    (state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL
    AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL
    AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32
    AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL)
    OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL)
    OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL))))
);
CREATE UNIQUE INDEX uq_provider_maintenance_active ON vault.provider_maintenance(singleton_id)
  WHERE closed_at IS NULL;
CREATE FUNCTION app_private.protect_provider_maintenance() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Maintenance ownership cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.provider_execution_id,NEW.claim_id,
         NEW.action,NEW.singleton_id,NEW.created_at) IS DISTINCT FROM
     ROW(OLD.execution_id,OLD.owner_run_id,OLD.provider_execution_id,OLD.claim_id,
         OLD.action,OLD.singleton_id,OLD.created_at)
     OR (OLD.started_at IS NOT NULL AND
       ROW(NEW.started_at,NEW.child_pid,NEW.child_identity_sha256) IS DISTINCT FROM
       ROW(OLD.started_at,OLD.child_pid,OLD.child_identity_sha256))
     OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
     OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','CLOSED'))
  THEN
    RAISE EXCEPTION 'Maintenance ownership is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_maintenance_guard BEFORE UPDATE OR DELETE ON vault.provider_maintenance
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_provider_maintenance();
REVOKE ALL ON FUNCTION app_private.protect_provider_maintenance() FROM PUBLIC;
REVOKE ALL ON TABLE vault.provider_maintenance FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.provider_maintenance IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.provider_maintenance) THEN
    RAISE EXCEPTION 'Refusing to discard maintenance ownership';
  END IF;
END $$;
DROP TABLE vault.provider_maintenance;
DROP FUNCTION app_private.protect_provider_maintenance();
    """)
