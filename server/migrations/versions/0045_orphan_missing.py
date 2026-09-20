"""Resolve absent orphan paths only with an exact acknowledged check-only process."""

from alembic import op

revision = "0045_orphan_missing"
down_revision = "0044_inventory_maintenance"
branch_labels = None
depends_on = None


def _constraints(*, missing: bool) -> str:
    orphan_actions = "'ORPHAN_OBJECT','ORPHAN_MISSING'" if missing else "'ORPHAN_OBJECT'"
    return f"""
ALTER TABLE vault.provider_maintenance
  DROP CONSTRAINT provider_maintenance_target_check,
  DROP CONSTRAINT provider_maintenance_state_check,
  ADD CONSTRAINT provider_maintenance_target_check CHECK (
    (action IN ('CLEANUP','SCRATCH') AND provider_execution_id IS NOT NULL
    AND orphan_claim_id IS NULL AND storage_key IS NULL) OR
    (action IN ({orphan_actions}) AND provider_execution_id IS NULL
    AND orphan_claim_id IS NOT NULL AND orphan_claim_id=claim_id AND storage_key IS NOT NULL) OR
    (action='INVENTORY' AND provider_execution_id IS NULL
    AND orphan_claim_id IS NULL AND storage_key IS NULL AND claim_id=execution_id)),
  ADD CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN ('CLEANUP','SCRATCH',{orphan_actions},'INVENTORY')
    AND state IN ('PREPARED','RUNNING','CLOSED'));
"""


def _orphan_guard(*, missing: bool) -> str:
    actions = "'ORPHAN_OBJECT','ORPHAN_MISSING'" if missing else "'ORPHAN_OBJECT'"
    return f"""
CREATE OR REPLACE FUNCTION app_private.protect_orphan_object_claim() RETURNS trigger
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
      WHERE m.execution_id=NEW.completed_execution_id AND m.action IN ({actions})
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
"""


def upgrade() -> None:
    op.execute(_constraints(missing=True))
    op.execute(_orphan_guard(missing=True))


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.orphan_object_claim, vault.provider_maintenance IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.provider_maintenance WHERE action='ORPHAN_MISSING') THEN
    RAISE EXCEPTION 'Refusing to discard orphan absence evidence';
  END IF;
END $$;
    """)
    op.execute(_orphan_guard(missing=False))
    op.execute(_constraints(missing=False))
