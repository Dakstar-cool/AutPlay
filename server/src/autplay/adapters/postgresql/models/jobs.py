# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the jobs PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .types import JsonValue


class JobRow(Base):
    """Persistence row for ``jobs.job``."""

    __tablename__ = "job"

    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    job_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
        server_default=text("3"),
    )
    state: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'QUEUED'"),
    )
    idempotency_scope: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    payload: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    checkpoint: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    progress_current: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    progress_total: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    lease_owner: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    lease_deadline: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    error_detail: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("job_id", name="job_pkey"),
        CheckConstraint(
            "length(job_type) BETWEEN 1 AND 200",
            name="job_job_type_check",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="job_schema_version_check",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="job_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "priority BETWEEN 0 AND 4",
            name="job_priority_check",
        ),
        CheckConstraint(
            "progress_current IS NULL OR progress_current >= 0",
            name="job_progress_current_check",
        ),
        CheckConstraint(
            "progress_total IS NULL OR progress_total >= 0",
            name="job_progress_total_check",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="job_attempt_count_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="job_row_version_check",
        ),
        CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_job_state",
        ),
        CheckConstraint(
            "(idempotency_scope IS NULL AND idempotency_key IS NULL) OR (idempotency_scope IS NOT NULL AND idempotency_key IS NOT NULL)",
            name="ck_job_idempotency_pair",
        ),
        CheckConstraint(
            "progress_total IS NULL OR progress_current IS NULL OR progress_current <= progress_total",
            name="ck_job_progress",
        ),
        CheckConstraint(
            "state = 'RUNNING' OR (lease_owner IS NULL AND lease_deadline IS NULL AND heartbeat_at IS NULL)",
            name="ck_job_lease_fields",
        ),
        {"schema": "jobs"},
    )


class JobAttemptRow(Base):
    """Persistence row for ``jobs.job_attempt``."""

    __tablename__ = "job_attempt"

    job_attempt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    worker_id: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    outcome: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    metrics: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("job_attempt_id", name="job_attempt_pkey"),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="job_attempt_job_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "attempt_no >= 1",
            name="job_attempt_attempt_no_check",
        ),
        CheckConstraint(
            "length(worker_id) BETWEEN 1 AND 300",
            name="job_attempt_worker_id_check",
        ),
        UniqueConstraint("job_id", "attempt_no", name="uq_job_attempt_number"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('SUCCESS', 'RETRYABLE_ERROR', 'TERMINAL_ERROR', 'LEASE_EXPIRED', 'CANCELLED')",
            name="ck_job_attempt_outcome",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_job_attempt_finish",
        ),
        {"schema": "jobs"},
    )


class JobDependencyRow(Base):
    """Persistence row for ``jobs.job_dependency``."""

    __tablename__ = "job_dependency"

    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    depends_on_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    dependency_policy: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'REQUIRE_SUCCESS'"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="job_dependency_job_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["depends_on_job_id"],
            ["jobs.job.job_id"],
            name="job_dependency_depends_on_job_id_fkey",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("job_id", "depends_on_job_id", name="job_dependency_pkey"),
        CheckConstraint(
            "job_id <> depends_on_job_id",
            name="ck_job_dependency_not_self",
        ),
        CheckConstraint(
            "dependency_policy IN ('REQUIRE_SUCCESS', 'REQUIRE_TERMINAL')",
            name="ck_job_dependency_policy",
        ),
        {"schema": "jobs"},
    )


Index(
    "uq_job_idempotency",
    JobRow.idempotency_scope,
    JobRow.idempotency_key,
    unique=True,
    postgresql_where=text("idempotency_key IS NOT NULL"),
)

Index(
    "ix_job_claim",
    JobRow.priority,
    JobRow.scheduled_at,
    JobRow.created_at,
    postgresql_where=text("state IN ('QUEUED', 'RETRY_WAIT')"),
)

Index(
    "ix_job_expired_lease",
    JobRow.lease_deadline,
    postgresql_where=text("state = 'RUNNING'"),
)


__all__ = (
    "JobAttemptRow",
    "JobDependencyRow",
    "JobRow",
)
