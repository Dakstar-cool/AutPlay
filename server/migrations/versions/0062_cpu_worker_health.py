"""Add a bounded latest CPU worker observation for the authenticated dashboard."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062_cpu_worker_health"
down_revision = "0061_admission_device_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_health",
        sa.Column("server_instance_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("running", sa.Boolean(), nullable=False),
        sa.Column("busy", sa.Boolean(), nullable=False),
        sa.Column("cpu_percent", sa.Float()),
        sa.Column("memory_bytes", sa.BigInteger()),
        sa.Column("memory_limit_bytes", sa.BigInteger()),
        sa.ForeignKeyConstraint(
            ["server_instance_id"],
            ["account.server_instance.server_instance_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "cpu_percent IS NULL OR cpu_percent BETWEEN 0 AND 100", name="worker_cpu_range"
        ),
        sa.CheckConstraint("memory_bytes IS NULL OR memory_bytes >= 0", name="worker_memory_range"),
        sa.CheckConstraint(
            "memory_limit_bytes IS NULL OR memory_limit_bytes > 0", name="worker_memory_limit_range"
        ),
        schema="jobs",
    )


def downgrade() -> None:
    # Only replaceable runtime observations are removed; no durable job facts.
    op.drop_table("worker_health", schema="jobs")
