"""Serialize orphan CAS retirement with publication and retain exact exit evidence."""

from alembic import op

revision = "0043_orphan_object_claim"
down_revision = "0042_provider_maintenance"
branch_labels = None
depends_on = None


def _maintenance_guard(*, orphan: bool) -> str:
    new_fields = ",NEW.orphan_claim_id,NEW.storage_key" if orphan else ""
    old_fields = ",OLD.orphan_claim_id,OLD.storage_key" if orphan else ""
    return f"""
CREATE OR REPLACE FUNCTION app_private.protect_provider_maintenance() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Maintenance ownership cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.provider_execution_id,NEW.claim_id,
         NEW.action,NEW.singleton_id,NEW.created_at{new_fields}) IS DISTINCT FROM
     ROW(OLD.execution_id,OLD.owner_run_id,OLD.provider_execution_id,OLD.claim_id,
         OLD.action,OLD.singleton_id,OLD.created_at{old_fields})
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
"""


def upgrade() -> None:
    op.execute("""
CREATE TABLE vault.orphan_object_claim (
  claim_id UUID CONSTRAINT orphan_object_claim_pkey PRIMARY KEY,
  storage_key TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  completed_at TIMESTAMP WITH TIME ZONE,
  completed_execution_id UUID
    CONSTRAINT orphan_object_claim_completed_execution_id_fkey
    REFERENCES vault.provider_maintenance(execution_id),
  CONSTRAINT orphan_object_claim_identity_key UNIQUE(claim_id, storage_key),
  CONSTRAINT orphan_object_claim_key_check CHECK (storage_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT orphan_object_claim_completion_check CHECK (
    (completed_at IS NULL AND completed_execution_id IS NULL) OR
    (completed_at IS NOT NULL AND completed_execution_id IS NOT NULL AND completed_at>=created_at))
);
CREATE UNIQUE INDEX uq_orphan_object_claim_active ON vault.orphan_object_claim(storage_key)
  WHERE completed_at IS NULL;
ALTER TABLE vault.provider_maintenance
  ALTER COLUMN provider_execution_id DROP NOT NULL,
  ADD COLUMN orphan_claim_id UUID,
  ADD COLUMN storage_key TEXT,
  ADD CONSTRAINT provider_maintenance_orphan_claim_fkey
    FOREIGN KEY(orphan_claim_id, storage_key)
    REFERENCES vault.orphan_object_claim(claim_id,storage_key),
  ADD CONSTRAINT provider_maintenance_target_check CHECK (
    (action IN ('CLEANUP','SCRATCH') AND provider_execution_id IS NOT NULL
    AND orphan_claim_id IS NULL AND storage_key IS NULL) OR
    (action='ORPHAN_OBJECT' AND provider_execution_id IS NULL AND orphan_claim_id IS NOT NULL
    AND orphan_claim_id=claim_id AND storage_key IS NOT NULL)),
  DROP CONSTRAINT provider_maintenance_state_check,
  ADD CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN ('CLEANUP','SCRATCH','ORPHAN_OBJECT')
    AND state IN ('PREPARED','RUNNING','CLOSED'));
CREATE FUNCTION app_private.protect_orphan_object_claim() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Orphan ownership cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF ROW(NEW.claim_id,NEW.storage_key,NEW.created_at) IS DISTINCT FROM
     ROW(OLD.claim_id,OLD.storage_key,OLD.created_at)
     OR (OLD.completed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'Orphan ownership is immutable' USING ERRCODE='23514';
  END IF;
  IF NEW.completed_at IS NOT NULL AND OLD.completed_at IS NULL AND (
    NOT EXISTS (SELECT 1 FROM vault.provider_maintenance m
      WHERE m.execution_id=NEW.completed_execution_id AND m.action='ORPHAN_OBJECT'
      AND m.orphan_claim_id=NEW.claim_id AND m.storage_key=NEW.storage_key
      AND m.state='CLOSED' AND m.closure_kind='PROCESS_EXIT' AND m.exit_code=0
      AND m.child_pid IS NOT NULL AND m.closed_at<=NEW.completed_at)
    OR EXISTS (SELECT 1 FROM vault.provider_maintenance m
      WHERE m.orphan_claim_id=NEW.claim_id AND m.closed_at IS NULL)
  ) THEN
    RAISE EXCEPTION 'Orphan exit is unconfirmed' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER orphan_object_claim_guard BEFORE UPDATE OR DELETE ON vault.orphan_object_claim
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_orphan_object_claim();
REVOKE ALL ON FUNCTION app_private.protect_orphan_object_claim() FROM PUBLIC;
REVOKE ALL ON TABLE vault.orphan_object_claim FROM PUBLIC;
    """)
    op.execute(_maintenance_guard(orphan=True))


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.orphan_object_claim, vault.provider_maintenance IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.orphan_object_claim)
     OR EXISTS (SELECT 1 FROM vault.provider_maintenance WHERE action='ORPHAN_OBJECT') THEN
    RAISE EXCEPTION 'Refusing to discard orphan ownership';
  END IF;
END $$;
ALTER TABLE vault.provider_maintenance
  DROP CONSTRAINT provider_maintenance_target_check,
  DROP CONSTRAINT provider_maintenance_orphan_claim_fkey,
  DROP COLUMN orphan_claim_id,
  DROP COLUMN storage_key,
  ALTER COLUMN provider_execution_id SET NOT NULL,
  DROP CONSTRAINT provider_maintenance_state_check,
  ADD CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN ('CLEANUP','SCRATCH')
    AND state IN ('PREPARED','RUNNING','CLOSED'));
DROP TABLE vault.orphan_object_claim;
DROP FUNCTION app_private.protect_orphan_object_claim();
    """)
    op.execute(_maintenance_guard(orphan=False))
