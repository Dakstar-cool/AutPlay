# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the importing PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .types import JsonValue


class ImportJobRow(Base):
    """Persistence row for ``importing.import_job``."""

    __tablename__ = "import_job"

    import_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    adapter_id: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    adapter_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    input_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    input_schema_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    checkpoint: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    summary: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
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
        PrimaryKeyConstraint("import_job_id", name="import_job_pkey"),
        UniqueConstraint("job_id", name="import_job_job_id_key"),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="import_job_job_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="import_job_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(adapter_id) BETWEEN 1 AND 200",
            name="import_job_adapter_id_check",
        ),
        CheckConstraint(
            "length(adapter_version) BETWEEN 1 AND 100",
            name="import_job_adapter_version_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="import_job_row_version_check",
        ),
        CheckConstraint(
            "octet_length(input_sha256) = 32",
            name="ck_import_job_hash_len",
        ),
        CheckConstraint(
            "mode IN ('LIBRARY_ONLY', 'MATERIALIZE')",
            name="ck_import_job_mode",
        ),
        {"schema": "importing"},
    )


class ImportEntryRow(Base):
    """Persistence row for ``importing.import_entry``."""

    __tablename__ = "import_entry"

    import_entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    import_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    source_row_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    raw_title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    raw_artist: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    raw_album: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_duration_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    raw_external_id: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_payload: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    match_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    current_match_decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    selected_recording_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    user_track_ref_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
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
        PrimaryKeyConstraint("import_entry_id", name="import_entry_pkey"),
        ForeignKeyConstraint(
            ["import_job_id"],
            ["importing.import_job.import_job_id"],
            name="import_entry_import_job_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(source_row_key) BETWEEN 1 AND 1000",
            name="import_entry_source_row_key_check",
        ),
        CheckConstraint(
            "raw_duration_ms IS NULL OR raw_duration_ms > 0",
            name="import_entry_raw_duration_ms_check",
        ),
        ForeignKeyConstraint(
            ["selected_recording_id"],
            ["catalog.recording.recording_id"],
            name="import_entry_selected_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="import_entry_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="import_entry_row_version_check",
        ),
        UniqueConstraint("import_job_id", "source_row_key", name="uq_import_entry_source_row"),
        CheckConstraint(
            "match_status IN ( 'PENDING', 'AUTO_MATCH', 'MANUAL_MATCH', 'MANUAL_UNRESOLVED', 'REVIEW_REQUIRED', 'NO_MATCH', 'INTEGRITY_CONFLICT', 'DEFERRED_EVIDENCE', 'REJECTED' )",
            name="ck_import_entry_match_status",
        ),
        CheckConstraint(
            "(match_status IN ('AUTO_MATCH', 'MANUAL_MATCH') AND selected_recording_id IS NOT NULL) OR (match_status NOT IN ('AUTO_MATCH', 'MANUAL_MATCH') AND selected_recording_id IS NULL)",
            name="ck_import_entry_selected_recording",
        ),
        CheckConstraint(
            "(current_match_decision_id IS NULL AND match_status IN ('PENDING', 'REJECTED') AND selected_recording_id IS NULL) OR current_match_decision_id IS NOT NULL",
            name="ck_import_entry_decision_projection",
        ),
        ForeignKeyConstraint(
            ["current_match_decision_id"],
            ["identity.match_decision.decision_id"],
            name="fk_import_entry_current_match_decision",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "importing"},
    )


Index(
    "ix_import_job_user",
    ImportJobRow.user_id,
    ImportJobRow.created_at.desc(),
)

Index(
    "ix_import_entry_job_status",
    ImportEntryRow.import_job_id,
    ImportEntryRow.match_status,
    ImportEntryRow.source_row_key,
)


__all__ = (
    "ImportEntryRow",
    "ImportJobRow",
)
