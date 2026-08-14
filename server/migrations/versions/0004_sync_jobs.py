"""Create synchronization and durable-job relations.

Revision ID: 0004_sync_jobs
Revises: 0003_audit_identity
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0004_sync_jobs"
down_revision: str | None = "0003_audit_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "device_event_inbox",
    "sync_event",
    "device_sync_cursor",
    "tombstone",
    "idempotency_record",
    "job",
    "job_attempt",
    "job_dependency",
)
QUALIFIED_TABLES = (
    "sync.device_event_inbox",
    "sync.sync_event",
    "sync.device_sync_cursor",
    "sync.tombstone",
    "sync.idempotency_record",
    "jobs.job",
    "jobs.job_attempt",
    "jobs.job_dependency",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
