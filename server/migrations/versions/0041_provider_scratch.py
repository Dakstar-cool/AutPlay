"""Persist scratch-only retirement without changing successful Vault handoff receipts."""

from alembic import op

revision = "0041_provider_scratch"
down_revision = "0040_provider_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE vault.provider_staging
  ADD COLUMN scratch_claim_id UUID,
  ADD COLUMN scratch_claimed_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN scratch_retired_at TIMESTAMP WITH TIME ZONE,
  ADD CONSTRAINT provider_staging_scratch_check CHECK (
    (scratch_claim_id IS NULL AND scratch_claimed_at IS NULL AND scratch_retired_at IS NULL)
    OR (state='HANDED_OFF' AND scratch_claim_id IS NOT NULL AND scratch_claimed_at IS NOT NULL
      AND scratch_claimed_at>=handed_off_at
      AND (scratch_retired_at IS NULL OR scratch_retired_at>=scratch_claimed_at)));
CREATE INDEX ix_provider_staging_scratch_pending ON vault.provider_staging(execution_id)
  WHERE state='HANDED_OFF' AND scratch_retired_at IS NULL;
CREATE FUNCTION app_private.protect_provider_scratch() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF (OLD.scratch_claimed_at IS NOT NULL AND
       ROW(NEW.scratch_claim_id,NEW.scratch_claimed_at) IS DISTINCT FROM
       ROW(OLD.scratch_claim_id,OLD.scratch_claimed_at))
     OR (OLD.scratch_retired_at IS NOT NULL AND
       NEW.scratch_retired_at IS DISTINCT FROM OLD.scratch_retired_at)
  THEN
    RAISE EXCEPTION 'Provider scratch receipts are immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER provider_scratch_guard BEFORE UPDATE ON vault.provider_staging
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_provider_scratch();
REVOKE ALL ON FUNCTION app_private.protect_provider_scratch() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.provider_staging IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM vault.provider_staging WHERE scratch_claim_id IS NOT NULL) THEN
    RAISE EXCEPTION 'Refusing to discard provider scratch ownership';
  END IF;
END $$;
DROP TRIGGER provider_scratch_guard ON vault.provider_staging;
DROP FUNCTION app_private.protect_provider_scratch();
DROP INDEX vault.ix_provider_staging_scratch_pending;
ALTER TABLE vault.provider_staging
  DROP CONSTRAINT provider_staging_scratch_check,
  DROP COLUMN scratch_retired_at,
  DROP COLUMN scratch_claimed_at,
  DROP COLUMN scratch_claim_id;
    """)
