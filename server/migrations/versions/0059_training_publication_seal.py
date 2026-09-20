"""Durably seal the exact retained-child publication tuple."""

from alembic import op

revision = "0059_training_publication_seal"
down_revision = "0058_training_privacy_fence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ml.training_execution ADD COLUMN publication_seal_sha256 bytea;
ALTER TABLE ml.training_execution ADD CONSTRAINT training_execution_publication_seal_check
 CHECK(publication_seal_sha256 IS NULL OR
  (state IN ('STOPPING','CLOSED') AND checkpoint_manifest_sha256 IS NOT NULL
   AND octet_length(publication_seal_sha256)=32));
CREATE FUNCTION app_private.protect_training_publication_seal() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.publication_seal_sha256 IS NOT NULL THEN
  RAISE EXCEPTION 'training_publication_seal_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' AND NEW.publication_seal_sha256 IS DISTINCT FROM OLD.publication_seal_sha256
  AND NOT (OLD.publication_seal_sha256 IS NULL
   AND OLD.state IN ('PREPARED','RUNNING') AND NEW.state='STOPPING') THEN
  RAISE EXCEPTION 'training_publication_seal_immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_training_publication_seal_guard BEFORE INSERT OR UPDATE
 ON ml.training_execution FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_training_publication_seal();
REVOKE ALL ON FUNCTION app_private.protect_training_publication_seal() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.training_execution IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.training_execution WHERE publication_seal_sha256 IS NOT NULL) THEN
  RAISE EXCEPTION 'training_publication_seal_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_training_publication_seal_guard ON ml.training_execution;
DROP FUNCTION app_private.protect_training_publication_seal();
ALTER TABLE ml.training_execution DROP CONSTRAINT training_execution_publication_seal_check;
ALTER TABLE ml.training_execution DROP COLUMN publication_seal_sha256;
""")
