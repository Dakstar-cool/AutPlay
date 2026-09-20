"""Inventory retains the existing singleton maintenance slot until exact exit."""

from alembic import op

revision = "0044_inventory_maintenance"
down_revision = "0043_orphan_object_claim"
branch_labels = None
depends_on = None


def _constraints(*, inventory: bool) -> str:
    actions = "'CLEANUP','SCRATCH','ORPHAN_OBJECT'"
    target = ""
    if inventory:
        actions += ",'INVENTORY'"
        target = """ OR (action='INVENTORY' AND provider_execution_id IS NULL
          AND orphan_claim_id IS NULL AND storage_key IS NULL AND claim_id=execution_id)"""
    return f"""
ALTER TABLE vault.provider_maintenance
  DROP CONSTRAINT provider_maintenance_target_check,
  DROP CONSTRAINT provider_maintenance_state_check,
  ADD CONSTRAINT provider_maintenance_target_check CHECK (
    (action IN ('CLEANUP','SCRATCH') AND provider_execution_id IS NOT NULL
    AND orphan_claim_id IS NULL AND storage_key IS NULL) OR
    (action='ORPHAN_OBJECT' AND provider_execution_id IS NULL AND orphan_claim_id IS NOT NULL
    AND orphan_claim_id=claim_id AND storage_key IS NOT NULL){target}),
  ADD CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN ({actions}) AND state IN ('PREPARED','RUNNING','CLOSED'));
"""


def upgrade() -> None:
    op.execute(_constraints(inventory=True))


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.provider_maintenance IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.provider_maintenance WHERE action='INVENTORY') THEN
    RAISE EXCEPTION 'Refusing to discard inventory ownership';
  END IF;
END $$;
    """)
    op.execute(_constraints(inventory=False))
