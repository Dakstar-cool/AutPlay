"""Allow persisted Mac platform evidence without changing legacy device proofs."""

from alembic import op

revision = "0067_macos_platform_foundation"
down_revision = "0067_sona_native_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
LOCK TABLE account.device, account.device_admission IN ACCESS EXCLUSIVE MODE;
ALTER TABLE account.device DROP CONSTRAINT ck_device_platform;
ALTER TABLE account.device ADD CONSTRAINT ck_device_platform
 CHECK (platform IN ('ANDROID', 'WEB', 'OTHER', 'MACOS'));
ALTER TABLE account.device_admission DROP CONSTRAINT device_admission_platform_check;
ALTER TABLE account.device_admission ADD CONSTRAINT device_admission_platform_check
 CHECK (platform IN ('ANDROID', 'MACOS'));
""")


def downgrade() -> None:
    # Lock both tables before the preflight so no concurrent writer can introduce
    # incompatible evidence between the check and either constraint replacement.
    # Never rewrite/delete rows or immutable request hashes to make downgrade fit.
    op.execute("""
LOCK TABLE account.device, account.device_admission IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (
  SELECT 1 FROM account.device WHERE platform NOT IN ('ANDROID', 'WEB', 'OTHER')
 ) OR EXISTS (
  SELECT 1 FROM account.device_admission WHERE platform <> 'ANDROID'
 ) THEN
  RAISE EXCEPTION 'refusing platform downgrade with Mac or unknown platform evidence'
   USING ERRCODE='55000';
 END IF;
END $$;
ALTER TABLE account.device DROP CONSTRAINT ck_device_platform;
ALTER TABLE account.device ADD CONSTRAINT ck_device_platform
 CHECK (platform IN ('ANDROID', 'WEB', 'OTHER'));
ALTER TABLE account.device_admission DROP CONSTRAINT device_admission_platform_check;
ALTER TABLE account.device_admission ADD CONSTRAINT device_admission_platform_check
 CHECK (platform = 'ANDROID');
""")
