"""Bind newly queued provider acquisitions to their original account/session authority."""

from alembic import op

revision = "0037_acquisition_authority"
down_revision = "0036_resource_io_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows deliberately remain unbound: current credentials cannot
    # reconstruct authority held when an older acquisition was selected.
    op.execute("""
ALTER TABLE discovery.internet_acquisition
  ADD COLUMN authority_generation bigint,
  ADD COLUMN source_session_family_id uuid,
  ADD COLUMN source_session_mode text,
  ADD CONSTRAINT internet_acquisition_authority_check CHECK (
    (authority_generation IS NULL AND source_session_family_id IS NULL
     AND source_session_mode IS NULL)
    OR (authority_generation IS NOT NULL AND authority_generation >= 1
        AND source_session_family_id IS NOT NULL AND source_session_mode IS NOT NULL
        AND source_session_mode IN ('LEGACY','V2')));
ALTER TABLE discovery.acquisition_attempt ADD COLUMN authority_generation bigint,
  ADD CONSTRAINT ck_acquisition_attempt_authority CHECK (
    authority_generation IS NULL OR authority_generation >= 1);

CREATE FUNCTION app_private.protect_acquisition_authority() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.authority_generation IS DISTINCT FROM OLD.authority_generation THEN
    RAISE EXCEPTION 'Acquisition authority is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='internet_acquisition' THEN
    IF ROW(NEW.source_session_family_id,NEW.source_session_mode)
       IS DISTINCT FROM ROW(OLD.source_session_family_id,OLD.source_session_mode) THEN
      RAISE EXCEPTION 'Acquisition authority is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER internet_acquisition_authority_guard BEFORE UPDATE
  ON discovery.internet_acquisition FOR EACH ROW
  EXECUTE FUNCTION app_private.protect_acquisition_authority();
CREATE TRIGGER acquisition_attempt_authority_guard BEFORE UPDATE
  ON discovery.acquisition_attempt FOR EACH ROW
  EXECUTE FUNCTION app_private.protect_acquisition_authority();
REVOKE ALL ON FUNCTION app_private.protect_acquisition_authority() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM discovery.internet_acquisition WHERE authority_generation IS NOT NULL)
     OR EXISTS(SELECT 1 FROM discovery.acquisition_attempt WHERE authority_generation IS NOT NULL)
  THEN
    RAISE EXCEPTION 'Refusing to discard acquisition authority evidence';
  END IF;
END $$;
DROP TRIGGER internet_acquisition_authority_guard ON discovery.internet_acquisition;
DROP TRIGGER acquisition_attempt_authority_guard ON discovery.acquisition_attempt;
DROP FUNCTION app_private.protect_acquisition_authority();
ALTER TABLE discovery.internet_acquisition DROP CONSTRAINT internet_acquisition_authority_check,
  DROP COLUMN authority_generation, DROP COLUMN source_session_family_id,
  DROP COLUMN source_session_mode;
ALTER TABLE discovery.acquisition_attempt DROP CONSTRAINT ck_acquisition_attempt_authority,
  DROP COLUMN authority_generation;
    """)
