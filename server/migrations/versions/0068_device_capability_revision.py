"""Retain bounded v2 capability high-water evidence without rewriting v1 rows."""

from alembic import op

revision = "0068_device_capability_revision"
down_revision = "0067_macos_platform_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.device_capability_revision (
 server_instance_id UUID NOT NULL, identity_epoch BIGINT NOT NULL,
 user_id UUID NOT NULL, device_id UUID NOT NULL, capability_revision BIGINT NOT NULL,
 effective_content_sha256 BYTEA NOT NULL, identity_thumbprint_sha256 BYTEA NOT NULL,
 device_key_thumbprint_sha256 BYTEA NOT NULL,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT device_capability_revision_pkey
 PRIMARY KEY(server_instance_id,identity_epoch,user_id,device_id),
 CONSTRAINT device_capability_revision_instance_fkey FOREIGN KEY(server_instance_id)
 REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
 CONSTRAINT device_capability_revision_device_fkey FOREIGN KEY(user_id,device_id)
 REFERENCES account.device(user_id,device_id) ON DELETE CASCADE,
 CONSTRAINT device_capability_revision_epoch_check
 CHECK(identity_epoch BETWEEN 1 AND 9007199254740991),
 CONSTRAINT device_capability_revision_value_check
 CHECK(capability_revision BETWEEN 1 AND 9007199254740991),
 CONSTRAINT device_capability_revision_digest_check CHECK(
  octet_length(effective_content_sha256)=32 AND octet_length(identity_thumbprint_sha256)=32
  AND octet_length(device_key_thumbprint_sha256)=32),
 CONSTRAINT device_capability_revision_time_check CHECK(updated_at>=created_at)
);
CREATE FUNCTION app_private.guard_device_capability_revision() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='TRUNCATE' THEN
  RAISE EXCEPTION 'capability_high_water_retained' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN
  -- Only the existing restricted account erasure may remove personal evidence.
  -- The owner/device FK cascade participates in that same purge transaction.
  IF app_private.privacy_owner_allowed(OLD.user_id) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'capability_high_water_retained' USING ERRCODE='23514';
 END IF;
 IF ROW(NEW.server_instance_id,NEW.identity_epoch,NEW.user_id,NEW.device_id,
        NEW.identity_thumbprint_sha256,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.server_instance_id,OLD.identity_epoch,OLD.user_id,OLD.device_id,
        OLD.identity_thumbprint_sha256,OLD.created_at)
 OR NEW.updated_at<OLD.updated_at THEN
  RAISE EXCEPTION 'capability_scope_immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.effective_content_sha256=OLD.effective_content_sha256 THEN
  IF NEW.capability_revision<>OLD.capability_revision
  OR NEW.device_key_thumbprint_sha256<>OLD.device_key_thumbprint_sha256 THEN
   RAISE EXCEPTION 'capability_revision_content_mismatch' USING ERRCODE='23514';
  END IF;
 ELSIF OLD.capability_revision>=9007199254740991
 OR NEW.capability_revision<>OLD.capability_revision+1 THEN
  RAISE EXCEPTION 'capability_revision_content_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER device_capability_revision_guard BEFORE UPDATE OR DELETE
 ON account.device_capability_revision FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_device_capability_revision();
CREATE TRIGGER device_capability_revision_no_truncate BEFORE TRUNCATE
 ON account.device_capability_revision FOR EACH STATEMENT
 EXECUTE FUNCTION app_private.guard_device_capability_revision();
REVOKE ALL ON account.device_capability_revision FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.guard_device_capability_revision() FROM PUBLIC;
""")


def downgrade() -> None:
    # A single lock/preflight precedes all schema removal. Never delete evidence
    # to enable rollback, including empty-family or disabled-gate evidence.
    op.execute("""
LOCK TABLE account.device_capability_revision IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.device_capability_revision) THEN
  RAISE EXCEPTION 'refusing to discard device capability high-water evidence'
   USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE account.device_capability_revision;
DROP FUNCTION app_private.guard_device_capability_revision();
""")
