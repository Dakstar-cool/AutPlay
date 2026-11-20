"""Retain owner/device erasure floors after typed tombstone expiry.

Revision ID: 0070_sync_erasure_floor
Revises: 0069_face_tombstone_outbox
"""

from alembic import op

revision = "0070_sync_erasure_floor"
down_revision = "0069_face_tombstone_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE sync.device_event_inbox ADD COLUMN journal_epoch uuid;
UPDATE sync.device_event_inbox AS inbox SET journal_epoch=cursor.journal_epoch
 FROM sync.device_sync_cursor AS cursor
 WHERE cursor.device_id=inbox.device_id AND cursor.journal_epoch IS NOT NULL;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE journal_epoch IS NULL) THEN
  RAISE EXCEPTION 'inbox journal epoch backfill requires repair' USING ERRCODE='55000';
 END IF;
END $$;
ALTER TABLE sync.device_event_inbox ALTER COLUMN journal_epoch SET NOT NULL;
ALTER TABLE sync.device_event_inbox DROP CONSTRAINT uq_device_event_sequence;
ALTER TABLE sync.device_event_inbox ADD CONSTRAINT uq_device_event_sequence
 UNIQUE(device_id,journal_epoch,device_sequence);

CREATE TABLE sync.owner_erasure_floor (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 floor_sequence bigint NOT NULL CONSTRAINT ck_owner_erasure_floor_positive CHECK(floor_sequence>0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sync.device_erasure_floor (
 device_id uuid PRIMARY KEY,
 user_id uuid NOT NULL,
 floor_sequence bigint NOT NULL CONSTRAINT ck_device_erasure_floor_positive CHECK(floor_sequence>0),
 invalidated_epoch uuid,
 replacement_epoch uuid,
 bootstrap_completed boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_device_erasure_floor_owner FOREIGN KEY(user_id,device_id)
  REFERENCES account.device(user_id,device_id) ON DELETE CASCADE,
 CONSTRAINT ck_device_erasure_floor_new_epoch CHECK(
  replacement_epoch IS NULL OR replacement_epoch IS DISTINCT FROM invalidated_epoch),
 CONSTRAINT ck_device_erasure_floor_bootstrap_epoch CHECK(
  NOT bootstrap_completed OR replacement_epoch IS NOT NULL)
);
CREATE INDEX ix_device_erasure_floor_owner ON sync.device_erasure_floor(user_id,device_id);
REVOKE ALL ON sync.owner_erasure_floor,sync.device_erasure_floor FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM sync.owner_erasure_floor)
  OR EXISTS(SELECT 1 FROM sync.device_erasure_floor) THEN
  RAISE EXCEPTION 'refusing erasure floor downgrade with durable privacy fences'
   USING ERRCODE='55000';
 END IF;
 IF EXISTS(
  SELECT 1 FROM sync.device_event_inbox
  GROUP BY device_id,device_sequence HAVING count(*)>1
 ) THEN
  RAISE EXCEPTION 'refusing inbox epoch downgrade with reused sequences'
   USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE sync.device_erasure_floor;
DROP TABLE sync.owner_erasure_floor;
ALTER TABLE sync.device_event_inbox DROP CONSTRAINT uq_device_event_sequence;
ALTER TABLE sync.device_event_inbox ADD CONSTRAINT uq_device_event_sequence
 UNIQUE(device_id,device_sequence);
ALTER TABLE sync.device_event_inbox DROP COLUMN journal_epoch;
""")
