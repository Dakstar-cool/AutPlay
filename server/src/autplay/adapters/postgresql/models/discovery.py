# ruff: noqa: E501
"""Typed A1B persistence for manual provider expansion and acquisition state."""

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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class BulkOperationRow(Base):
    """One explicit owner-confirmed TXT collection expansion operation."""

    __tablename__ = "bulk_operation"

    bulk_operation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    import_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    start_operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    start_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    state: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'PREVIEW'"))
    selected_artist_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    planned_candidate_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    ready_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("bulk_operation_id", name="bulk_operation_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="bulk_operation_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_job_id"],
            ["importing.import_job.import_job_id"],
            name="bulk_operation_import_job_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "operation_id", name="uq_bulk_operation_owner_operation"),
        UniqueConstraint(
            "user_id", "start_operation_id", name="uq_bulk_operation_owner_start_operation"
        ),
        CheckConstraint("octet_length(request_sha256) = 32", name="ck_bulk_operation_hash"),
        CheckConstraint(
            "start_request_sha256 IS NULL OR octet_length(start_request_sha256) = 32",
            name="ck_bulk_operation_start_hash",
        ),
        CheckConstraint(
            "(start_operation_id IS NULL) = (start_request_sha256 IS NULL)",
            name="ck_bulk_operation_start_pair",
        ),
        CheckConstraint(
            "state IN ('PREVIEW', 'QUEUED', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED_TERMINAL', 'CANCELLED')",
            name="ck_bulk_operation_state",
        ),
        CheckConstraint(
            "selected_artist_count BETWEEN 1 AND 20",
            name="ck_bulk_operation_artist_count",
        ),
        CheckConstraint(
            "planned_candidate_count BETWEEN 1 AND 200",
            name="ck_bulk_operation_candidate_count",
        ),
        CheckConstraint(
            "queued_count BETWEEN 0 AND planned_candidate_count AND ready_count BETWEEN 0 AND planned_candidate_count AND failed_count BETWEEN 0 AND planned_candidate_count",
            name="ck_bulk_operation_counts",
        ),
        CheckConstraint("row_version >= 1", name="ck_bulk_operation_row_version"),
        {"schema": "discovery"},
    )


class DiscoveryCandidateRow(Base):
    """Owner-scoped provider evidence and orthogonal acquisition/readiness state."""

    __tablename__ = "candidate"

    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    market_scope: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'GLOBAL'")
    )
    provider_track_id: Mapped[str] = mapped_column(Text(), nullable=False)
    provider_artist_id: Mapped[str] = mapped_column(Text(), nullable=False)
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    artist: Mapped[str] = mapped_column(Text(), nullable=False)
    album: Mapped[str | None] = mapped_column(Text(), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer(), nullable=False)
    license_url: Mapped[str] = mapped_column(Text(), nullable=False)
    share_url: Mapped[str] = mapped_column(Text(), nullable=False)
    disposition: Mapped[str] = mapped_column(Text(), nullable=False)
    acquisition_state: Mapped[str] = mapped_column(Text(), nullable=False)
    analysis_state: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_authorization_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    staging_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_reference_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    recording_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_track_ref_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    library_entry_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    audio_variant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("candidate_id", name="discovery_candidate_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="discovery_candidate_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="discovery_candidate_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="discovery_candidate_job_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["external_reference_id"],
            ["identity.external_reference.external_reference_id"],
            name="discovery_candidate_external_reference_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="discovery_candidate_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="discovery_candidate_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["library_entry_id"],
            ["library.library_entry.library_entry_id"],
            name="discovery_candidate_library_entry_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="discovery_candidate_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "provider_id",
            "market_scope",
            "provider_track_id",
            name="uq_discovery_candidate_owner_provider_track",
        ),
        CheckConstraint("length(market_scope) BETWEEN 1 AND 100", name="ck_candidate_market"),
        CheckConstraint("provider_track_id ~ '^[0-9]{1,20}$'", name="ck_candidate_track_id"),
        CheckConstraint("provider_artist_id ~ '^[0-9]{1,20}$'", name="ck_candidate_artist_id"),
        CheckConstraint("length(title) BETWEEN 1 AND 500", name="ck_candidate_title"),
        CheckConstraint("length(artist) BETWEEN 1 AND 500", name="ck_candidate_artist"),
        CheckConstraint(
            "album IS NULL OR length(album) BETWEEN 1 AND 500", name="ck_candidate_album"
        ),
        CheckConstraint("duration_seconds BETWEEN 1 AND 86400", name="ck_candidate_duration"),
        CheckConstraint("length(license_url) BETWEEN 1 AND 1000", name="ck_candidate_license_url"),
        CheckConstraint("length(share_url) BETWEEN 1 AND 1000", name="ck_candidate_share_url"),
        CheckConstraint(
            "disposition IN ('SELECTABLE', 'SELECTED', 'UNAVAILABLE', 'ALREADY_IN_LIBRARY', 'IDENTITY_REVIEW_REQUIRED', 'IGNORED')",
            name="ck_candidate_disposition",
        ),
        CheckConstraint(
            "acquisition_state IN ('NOT_REQUESTED', 'QUEUED', 'ACQUIRING', 'INGESTING', 'MATERIALIZING', 'READY', 'RETRY_WAIT', 'FAILED_TERMINAL', 'CANCELLED')",
            name="ck_candidate_acquisition_state",
        ),
        CheckConstraint(
            "analysis_state IS NULL OR analysis_state IN ('QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED_RETRYABLE', 'FAILED_TERMINAL')",
            name="ck_candidate_analysis_state",
        ),
        CheckConstraint("source_authorization_revision >= 1", name="ck_candidate_auth_revision"),
        CheckConstraint(
            "staging_key IS NULL OR staging_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'",
            name="ck_candidate_staging_key",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_candidate_error_code",
        ),
        CheckConstraint("row_version >= 1", name="ck_candidate_row_version"),
        {"schema": "discovery"},
    )


class BulkOperationItemRow(Base):
    """Stable ordered membership of a candidate in one manual bulk operation."""

    __tablename__ = "bulk_operation_item"

    bulk_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("bulk_operation_id", "candidate_id", name="bulk_operation_item_pkey"),
        ForeignKeyConstraint(
            ["bulk_operation_id"],
            ["discovery.bulk_operation.bulk_operation_id"],
            name="bulk_operation_item_operation_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["candidate_id"],
            ["discovery.candidate.candidate_id"],
            name="bulk_operation_item_candidate_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bulk_operation_id", "ordinal", name="uq_bulk_operation_item_ordinal"),
        CheckConstraint("ordinal BETWEEN 0 AND 199", name="ck_bulk_operation_item_ordinal"),
        {"schema": "discovery"},
    )


Index("ix_bulk_operation_owner_time", BulkOperationRow.user_id, BulkOperationRow.created_at.desc())
Index(
    "ix_discovery_candidate_owner_state",
    DiscoveryCandidateRow.user_id,
    DiscoveryCandidateRow.acquisition_state,
    DiscoveryCandidateRow.updated_at,
)
Index("ix_bulk_operation_item_candidate", BulkOperationItemRow.candidate_id)


__all__ = ("BulkOperationItemRow", "BulkOperationRow", "DiscoveryCandidateRow")
