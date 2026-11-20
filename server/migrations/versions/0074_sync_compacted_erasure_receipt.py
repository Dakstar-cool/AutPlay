"""Bind compacted erasure floors to independent evidence and retain epoch fences.

Revision ID: 0074_sync_erasure_receipt
Revises: 0073_face_projection_binding
"""

from alembic import op

revision = "0074_sync_erasure_receipt"
down_revision = "0073_face_projection_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE sync.owner_erasure_floor ADD COLUMN ledger_sequence bigint;
ALTER TABLE sync.owner_erasure_floor ADD COLUMN ledger_digest bytea;
ALTER TABLE sync.owner_erasure_floor ADD CONSTRAINT ck_owner_erasure_floor_receipt
 CHECK ((ledger_sequence IS NULL AND ledger_digest IS NULL) OR
        (ledger_sequence > 0 AND octet_length(ledger_digest)=32));
ALTER TABLE sync.device_erasure_floor ADD COLUMN ledger_sequence bigint;
ALTER TABLE sync.device_erasure_floor ADD COLUMN ledger_digest bytea;
ALTER TABLE sync.device_erasure_floor ADD CONSTRAINT ck_device_erasure_floor_receipt
 CHECK ((ledger_sequence IS NULL AND ledger_digest IS NULL) OR
        (ledger_sequence > 0 AND octet_length(ledger_digest)=32));

CREATE TABLE sync.device_erasure_invalidated_epoch (
 device_id uuid NOT NULL,
 user_id uuid NOT NULL,
 journal_epoch uuid NOT NULL,
 floor_sequence bigint NOT NULL CHECK(floor_sequence>0),
 ledger_sequence bigint NOT NULL CHECK(ledger_sequence>0),
 ledger_digest bytea NOT NULL CHECK(octet_length(ledger_digest)=32),
 invalidated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(device_id,journal_epoch),
 CONSTRAINT fk_device_erasure_invalidated_epoch_owner FOREIGN KEY(user_id,device_id)
  REFERENCES account.device(user_id,device_id) ON DELETE CASCADE
);
CREATE INDEX ix_device_erasure_invalidated_epoch_owner
 ON sync.device_erasure_invalidated_epoch(user_id,device_id);
REVOKE ALL ON sync.device_erasure_invalidated_epoch FROM PUBLIC;

CREATE FUNCTION app_private.protect_erasure_floor() RETURNS trigger
 LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='UPDATE' THEN
  IF TG_TABLE_NAME='device_erasure_invalidated_epoch' THEN
   RAISE EXCEPTION 'invalidated epoch is immutable' USING ERRCODE='55000';
  END IF;
  IF NEW.floor_sequence < OLD.floor_sequence
   OR (OLD.ledger_sequence IS NOT NULL AND
       (NEW.ledger_sequence IS NULL OR NEW.ledger_sequence < OLD.ledger_sequence))
   OR (NEW.floor_sequence=OLD.floor_sequence AND OLD.ledger_digest IS NOT NULL
       AND NEW.ledger_digest IS DISTINCT FROM OLD.ledger_digest)
   OR (NEW.floor_sequence>OLD.floor_sequence AND
       (NEW.ledger_sequence IS NULL OR
        (OLD.ledger_sequence IS NOT NULL AND NEW.ledger_sequence<=OLD.ledger_sequence)))
  THEN
   RAISE EXCEPTION 'erasure floor rollback' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='owner_erasure_floor' THEN
  IF EXISTS(SELECT 1 FROM account.user_account WHERE user_id=OLD.user_id) THEN
   RAISE EXCEPTION 'erasure floor delete requires account purge' USING ERRCODE='55000';
  END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM account.device WHERE device_id=OLD.device_id) THEN
   RAISE EXCEPTION 'erasure epoch delete requires device purge' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN OLD;
 END $$;
REVOKE ALL ON FUNCTION app_private.protect_erasure_floor() FROM PUBLIC;
CREATE TRIGGER protect_owner_erasure_floor
 BEFORE UPDATE OR DELETE ON sync.owner_erasure_floor FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_erasure_floor();
CREATE TRIGGER protect_device_erasure_floor
 BEFORE UPDATE OR DELETE ON sync.device_erasure_floor FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_erasure_floor();
CREATE TRIGGER protect_device_erasure_invalidated_epoch
 BEFORE UPDATE OR DELETE ON sync.device_erasure_invalidated_epoch FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_erasure_floor();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.device_erasure_invalidated_epoch)
  OR EXISTS(SELECT 1 FROM sync.owner_erasure_floor WHERE ledger_sequence IS NOT NULL)
  OR EXISTS(SELECT 1 FROM sync.device_erasure_floor WHERE ledger_sequence IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing compacted erasure receipt downgrade with privacy fences'
   USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER protect_device_erasure_invalidated_epoch
 ON sync.device_erasure_invalidated_epoch;
DROP TRIGGER protect_device_erasure_floor ON sync.device_erasure_floor;
DROP TRIGGER protect_owner_erasure_floor ON sync.owner_erasure_floor;
DROP FUNCTION app_private.protect_erasure_floor();
DROP TABLE sync.device_erasure_invalidated_epoch;
ALTER TABLE sync.device_erasure_floor DROP CONSTRAINT ck_device_erasure_floor_receipt;
ALTER TABLE sync.device_erasure_floor DROP COLUMN ledger_digest;
ALTER TABLE sync.device_erasure_floor DROP COLUMN ledger_sequence;
ALTER TABLE sync.owner_erasure_floor DROP CONSTRAINT ck_owner_erasure_floor_receipt;
ALTER TABLE sync.owner_erasure_floor DROP COLUMN ledger_digest;
ALTER TABLE sync.owner_erasure_floor DROP COLUMN ledger_sequence;
""")
