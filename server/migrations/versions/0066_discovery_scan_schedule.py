"""Add an administrator-owned global automatic release-scan interval.

Revision ID: 0066_discovery_scan_schedule
Revises: 0065_release_watch_follows
"""

from alembic import op

revision = "0066_discovery_scan_schedule"
down_revision = "0065_release_watch_follows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE discovery.scan_schedule (
 singleton_id integer NOT NULL PRIMARY KEY,
 interval_hours integer NOT NULL,
 revision bigint NOT NULL,
 changed_by uuid,
 changed_at timestamptz NOT NULL,
 CONSTRAINT ck_scan_schedule_singleton CHECK(singleton_id = 1),
 CONSTRAINT ck_scan_schedule_interval CHECK(interval_hours BETWEEN 1 AND 168),
 CONSTRAINT ck_scan_schedule_revision CHECK(revision >= 1)
);
CREATE TABLE discovery.scan_schedule_revision (
 revision bigint NOT NULL PRIMARY KEY,
 interval_hours integer NOT NULL,
 previous_interval_hours integer,
 changed_by uuid,
 operation_id uuid,
 changed_at timestamptz NOT NULL,
 reason text NOT NULL DEFAULT 'ADMIN',
 CONSTRAINT scan_schedule_revision_operation_id_key UNIQUE(operation_id),
 CONSTRAINT ck_scan_schedule_revision_interval CHECK(interval_hours BETWEEN 1 AND 168),
 CONSTRAINT ck_scan_schedule_revision_previous CHECK(
  previous_interval_hours IS NULL OR previous_interval_hours BETWEEN 1 AND 168
 ),
 CONSTRAINT ck_scan_schedule_revision_number CHECK(revision >= 1),
 CONSTRAINT ck_scan_schedule_revision_reason CHECK(reason IN ('DEFAULT', 'ADMIN')),
 CONSTRAINT ck_scan_schedule_revision_actor CHECK(
  (reason = 'DEFAULT' AND operation_id IS NULL AND changed_by IS NULL) OR
  (reason = 'ADMIN' AND operation_id IS NOT NULL AND changed_by IS NOT NULL)
 )
);
INSERT INTO discovery.scan_schedule(singleton_id, interval_hours, revision, changed_at)
 VALUES (1, 24, 1, now());
INSERT INTO discovery.scan_schedule_revision
 (revision, interval_hours, previous_interval_hours, changed_at, reason)
 VALUES (1, 24, NULL, now(), 'DEFAULT');
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM discovery.scan_schedule WHERE revision > 1) THEN
  RAISE EXCEPTION 'refusing to discard administrator scan interval history';
 END IF;
END $$;
DROP TABLE discovery.scan_schedule_revision;
DROP TABLE discovery.scan_schedule;
""")
