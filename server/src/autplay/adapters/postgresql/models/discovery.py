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


class SourceAuthorizationRow(Base):
    """Current bounded owner authorization for one canonical artist/provider scope."""

    __tablename__ = "source_authorization"

    authorization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    canonical_artist_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    adapter_id: Mapped[str] = mapped_column(Text(), nullable=False)
    adapter_version: Mapped[str] = mapped_column(Text(), nullable=False)
    market_scope: Mapped[str] = mapped_column(Text(), nullable=False)
    rights_capability: Mapped[str] = mapped_column(Text(), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    policy_reference: Mapped[str] = mapped_column(Text(), nullable=False)
    granted_by_bulk_operation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    purpose: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'MANUAL'"))
    policy_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    policy_revision: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("authorization_id", name="source_authorization_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="source_authorization_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="source_authorization_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["canonical_artist_id"],
            ["catalog.artist.artist_id"],
            name="source_authorization_canonical_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["granted_by_bulk_operation_id"],
            ["discovery.bulk_operation.bulk_operation_id"],
            name="source_authorization_bulk_operation_id_fkey",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["policy_id", "policy_revision"],
            [
                "discovery.artist_policy_revision.policy_id",
                "discovery.artist_policy_revision.revision",
            ],
            name="source_authorization_policy_revision_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("authorization_id", "revision", name="uq_source_authorization_revision"),
        UniqueConstraint(
            "user_id",
            "provider_id",
            "market_scope",
            "canonical_artist_id",
            "purpose",
            "revision",
            name="uq_source_authorization_owner_scope_revision",
        ),
        CheckConstraint("length(adapter_id) BETWEEN 1 AND 200", name="ck_source_auth_adapter"),
        CheckConstraint("length(adapter_version) BETWEEN 1 AND 100", name="ck_source_auth_version"),
        CheckConstraint("length(market_scope) BETWEEN 1 AND 100", name="ck_source_auth_market"),
        CheckConstraint("rights_capability = 'AUTHORIZED_DOWNLOAD'", name="ck_source_auth_rights"),
        CheckConstraint("purpose IN ('MANUAL', 'AUTO_IMPORT')", name="ck_source_auth_purpose"),
        CheckConstraint(
            "(purpose = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR "
            "(purpose = 'AUTO_IMPORT' AND policy_id IS NOT NULL AND policy_revision >= 1)",
            name="ck_source_auth_policy_lineage",
        ),
        CheckConstraint("revision >= 1", name="ck_source_auth_revision"),
        CheckConstraint("length(policy_reference) BETWEEN 1 AND 200", name="ck_source_auth_policy"),
        CheckConstraint("expires_at > granted_at", name="ck_source_auth_expiry"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="ck_source_auth_revocation"
        ),
        CheckConstraint("row_version >= 1", name="ck_source_auth_row_version"),
        {"schema": "discovery"},
    )


class ArtistPolicyRow(Base):
    """Current owner-scoped automation projection bound to one provider artist."""

    __tablename__ = "artist_policy"

    policy_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    canonical_artist_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_artist_id: Mapped[str] = mapped_column(Text(), nullable=False)
    discovery_mode: Mapped[str] = mapped_column(Text(), nullable=False)
    import_mode: Mapped[str] = mapped_column(Text(), nullable=False)
    automation_enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    last_checked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    current_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("policy_id", name="artist_policy_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="artist_policy_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["canonical_artist_id"],
            ["catalog.artist.artist_id"],
            name="artist_policy_canonical_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="artist_policy_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("policy_id", "user_id", name="uq_artist_policy_owner_lookup"),
        UniqueConstraint("user_id", "canonical_artist_id", name="uq_artist_policy_owner_artist"),
        CheckConstraint(
            "provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid",
            name="ck_artist_policy_provider",
        ),
        CheckConstraint(
            "provider_artist_id ~ '^[0-9]{1,20}$'", name="ck_artist_policy_provider_artist"
        ),
        CheckConstraint(
            "discovery_mode IN ('MANUAL_ONLY', 'SCHEDULED', 'DISABLED')",
            name="ck_artist_policy_discovery_mode",
        ),
        CheckConstraint(
            "import_mode IN ('REVIEW_REQUIRED', 'AUTO_IMPORT')", name="ck_artist_policy_import_mode"
        ),
        CheckConstraint(
            "(discovery_mode IN ('MANUAL_ONLY', 'DISABLED') AND next_eligible_at IS NULL) OR "
            "(discovery_mode = 'SCHEDULED' AND next_eligible_at IS NOT NULL)",
            name="ck_artist_policy_next_eligible",
        ),
        CheckConstraint(
            "automation_enabled = (discovery_mode = 'SCHEDULED')",
            name="ck_artist_policy_automation_mode",
        ),
        CheckConstraint("current_revision >= 1", name="ck_artist_policy_current_revision"),
        CheckConstraint("row_version >= 1", name="ck_artist_policy_row_version"),
        {"schema": "discovery"},
    )


class ArtistPolicyRevisionRow(Base):
    """Append-only immutable policy revision and Web confirmation evidence."""

    __tablename__ = "artist_policy_revision"

    policy_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    discovery_mode: Mapped[str] = mapped_column(Text(), nullable=False)
    import_mode: Mapped[str] = mapped_column(Text(), nullable=False)
    automation_enabled: Mapped[bool] = mapped_column(nullable=False)
    change_kind: Mapped[str] = mapped_column(Text(), nullable=False)
    confirmation_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    request_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("policy_id", "revision", name="artist_policy_revision_pkey"),
        ForeignKeyConstraint(
            ["policy_id", "owner_user_id"],
            ["discovery.artist_policy.policy_id", "discovery.artist_policy.user_id"],
            name="artist_policy_revision_policy_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_user_id", "operation_id", name="uq_artist_policy_revision_operation"
        ),
        CheckConstraint("revision >= 1", name="ck_artist_policy_revision"),
        CheckConstraint(
            "discovery_mode IN ('MANUAL_ONLY', 'SCHEDULED', 'DISABLED')",
            name="ck_artist_policy_revision_discovery_mode",
        ),
        CheckConstraint(
            "automation_enabled = (discovery_mode = 'SCHEDULED')",
            name="ck_artist_policy_revision_automation_mode",
        ),
        CheckConstraint(
            "import_mode IN ('REVIEW_REQUIRED', 'AUTO_IMPORT')",
            name="ck_artist_policy_revision_import_mode",
        ),
        CheckConstraint(
            "change_kind IN ('SAFE_DEFAULT', 'OWNER_CONFIRMED', 'DISABLED')",
            name="ck_artist_policy_revision_change_kind",
        ),
        CheckConstraint(
            "(operation_id IS NULL AND request_sha256 IS NULL AND change_kind = 'SAFE_DEFAULT' AND confirmation_code IS NULL) OR "
            "(operation_id IS NOT NULL AND octet_length(request_sha256) = 32 AND "
            "((import_mode = 'AUTO_IMPORT' AND confirmation_code = 'AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1') OR "
            "(import_mode = 'REVIEW_REQUIRED' AND confirmation_code IS NULL)))",
            name="ck_artist_policy_revision_confirmation",
        ),
        {"schema": "discovery"},
    )


class DiscoveryRunRow(Base):
    """One bounded due-slot execution of an immutable A1C policy revision."""

    __tablename__ = "run"

    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    policy_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_artist_id: Mapped[str] = mapped_column(Text(), nullable=False)
    adapter_id: Mapped[str] = mapped_column(Text(), nullable=False)
    adapter_version: Mapped[str] = mapped_column(Text(), nullable=False)
    canonical_query_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    due_slot_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    request_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    state: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'QUEUED'"))
    job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    observed_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    auto_selected_count: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("0")
    )
    page_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    checkpoint: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("run_id", name="discovery_run_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="discovery_run_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["policy_id", "policy_revision"],
            [
                "discovery.artist_policy_revision.policy_id",
                "discovery.artist_policy_revision.revision",
            ],
            name="discovery_run_policy_revision_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="discovery_run_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.job.job_id"], name="discovery_run_job_id_fkey", ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "policy_id", "policy_revision", "due_slot_at", name="uq_discovery_run_due_slot"
        ),
        UniqueConstraint("user_id", "operation_id", name="uq_discovery_run_owner_operation"),
        CheckConstraint(
            "provider_artist_id ~ '^[0-9]{1,20}$'", name="ck_discovery_run_provider_artist"
        ),
        CheckConstraint(
            "adapter_id = 'autplay.jamendo.manual'", name="ck_discovery_run_adapter_id"
        ),
        CheckConstraint("adapter_version = '1.0.0'", name="ck_discovery_run_adapter_version"),
        CheckConstraint(
            "octet_length(canonical_query_sha256) = 32", name="ck_discovery_run_query_hash"
        ),
        CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'PARTIAL', 'RETRY_WAIT', 'COMPLETED', 'FAILED_TERMINAL', 'CANCELLED')",
            name="ck_discovery_run_state",
        ),
        CheckConstraint(
            "(operation_id IS NULL AND request_sha256 IS NULL) OR (operation_id IS NOT NULL AND octet_length(request_sha256) = 32)",
            name="ck_discovery_run_operation",
        ),
        CheckConstraint("observed_count BETWEEN 0 AND 50", name="ck_discovery_run_observed_count"),
        CheckConstraint(
            "auto_selected_count BETWEEN 0 AND 10", name="ck_discovery_run_auto_selected_count"
        ),
        CheckConstraint("page_count BETWEEN 0 AND 2", name="ck_discovery_run_page_count"),
        CheckConstraint(
            "checkpoint IS NULL OR octet_length(convert_to(checkpoint, 'UTF8')) <= 2048",
            name="ck_discovery_run_checkpoint",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_discovery_run_error_code",
        ),
        CheckConstraint("row_version >= 1", name="ck_discovery_run_row_version"),
        {"schema": "discovery"},
    )


class DiscoveryRunPageRow(Base):
    """Bounded provider response evidence for one run page."""

    __tablename__ = "run_page"

    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer(), nullable=False)
    page_offset: Mapped[int] = mapped_column(Integer(), nullable=False)
    response_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    observed_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(Text(), nullable=True)
    next_offset: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("run_id", "ordinal", name="discovery_run_page_pkey"),
        ForeignKeyConstraint(
            ["run_id"],
            ["discovery.run.run_id"],
            name="discovery_run_page_run_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal BETWEEN 0 AND 1", name="ck_discovery_run_page_ordinal"),
        CheckConstraint("page_offset IN (0, 25)", name="ck_discovery_run_page_offset"),
        CheckConstraint("page_offset = ordinal * 25", name="ck_discovery_run_page_offset_ordinal"),
        CheckConstraint(
            "octet_length(response_sha256) = 32", name="ck_discovery_run_page_response_hash"
        ),
        CheckConstraint(
            "observed_count BETWEEN 0 AND 25", name="ck_discovery_run_page_observed_count"
        ),
        CheckConstraint(
            "checkpoint IS NULL OR octet_length(convert_to(checkpoint, 'UTF8')) <= 2048",
            name="ck_discovery_run_page_checkpoint",
        ),
        CheckConstraint(
            "(ordinal = 0 AND next_offset IN (25)) OR (ordinal IN (0, 1) AND next_offset IS NULL)",
            name="ck_discovery_run_page_next_offset",
        ),
        {"schema": "discovery"},
    )


class DiscoveryRunCandidateRow(Base):
    """Durable run-to-candidate observation and auto-selection membership."""

    __tablename__ = "run_candidate"

    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    selected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("run_id", "candidate_id", name="discovery_run_candidate_pkey"),
        ForeignKeyConstraint(
            ["run_id"],
            ["discovery.run.run_id"],
            name="discovery_run_candidate_run_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id"],
            ["discovery.candidate.candidate_id"],
            name="discovery_run_candidate_candidate_id_fkey",
            ondelete="RESTRICT",
        ),
        {"schema": "discovery"},
    )


class AcquisitionAttemptRow(Base):
    """Immutable-lineage acquisition attempt, isolated from mutable candidate state."""

    __tablename__ = "acquisition_attempt"

    acquisition_attempt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin: Mapped[str] = mapped_column(Text(), nullable=False)
    policy_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    policy_revision: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    source_authorization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_authorization_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(Text(), nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("acquisition_attempt_id", name="acquisition_attempt_pkey"),
        ForeignKeyConstraint(
            ["candidate_id"],
            ["discovery.candidate.candidate_id"],
            name="acquisition_attempt_candidate_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["policy_id", "policy_revision"],
            [
                "discovery.artist_policy_revision.policy_id",
                "discovery.artist_policy_revision.revision",
            ],
            name="acquisition_attempt_policy_revision_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_authorization_id", "source_authorization_revision"],
            [
                "discovery.source_authorization.authorization_id",
                "discovery.source_authorization.revision",
            ],
            name="acquisition_attempt_source_auth_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="acquisition_attempt_job_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("origin IN ('MANUAL', 'AUTOMATIC')", name="ck_acquisition_attempt_origin"),
        CheckConstraint(
            "(origin = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR (origin = 'AUTOMATIC' AND policy_id IS NOT NULL AND policy_revision >= 1)",
            name="ck_acquisition_attempt_policy_lineage",
        ),
        CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_acquisition_attempt_state",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_acquisition_attempt_error_code",
        ),
        CheckConstraint("row_version >= 1", name="ck_acquisition_attempt_row_version"),
        {"schema": "discovery"},
    )


class CandidateActionReceiptRow(Base):
    """Owner-scoped idempotency receipt for an explicit A1C candidate action."""

    __tablename__ = "candidate_action_receipt"

    action_receipt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    result_disposition: Mapped[str] = mapped_column(Text(), nullable=False)
    result_acquisition_state: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("action_receipt_id", name="candidate_action_receipt_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="candidate_action_receipt_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id"],
            ["discovery.candidate.candidate_id"],
            name="candidate_action_receipt_candidate_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "user_id", "operation_id", name="uq_candidate_action_receipt_owner_operation"
        ),
        CheckConstraint(
            "action IN ('SELECT', 'RETRY', 'IGNORE')",
            name="ck_candidate_action_receipt_action",
        ),
        CheckConstraint(
            "octet_length(request_sha256) = 32", name="ck_candidate_action_receipt_hash"
        ),
        CheckConstraint(
            "result_disposition IN ('SELECTABLE', 'SELECTED', 'UNAVAILABLE', 'ALREADY_IN_LIBRARY', 'IDENTITY_REVIEW_REQUIRED', 'IGNORED')",
            name="ck_candidate_action_receipt_disposition",
        ),
        CheckConstraint(
            "result_acquisition_state IN ('NOT_REQUESTED', 'QUEUED', 'ACQUIRING', 'INGESTING', 'MATERIALIZING', 'READY', 'RETRY_WAIT', 'FAILED_TERMINAL', 'CANCELLED')",
            name="ck_candidate_action_receipt_acquisition_state",
        ),
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
    canonical_artist_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    source_authorization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
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
    released_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    selection_origin: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'MANUAL'")
    )
    policy_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    policy_revision: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    source_authorization_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    staging_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_reference_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    recording_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_track_ref_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    library_entry_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    audio_variant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    current_acquisition_attempt_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
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
            ["canonical_artist_id"],
            ["catalog.artist.artist_id"],
            name="discovery_candidate_canonical_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_authorization_id", "source_authorization_revision"],
            [
                "discovery.source_authorization.authorization_id",
                "discovery.source_authorization.revision",
            ],
            name="discovery_candidate_source_authorization_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["policy_id", "policy_revision"],
            [
                "discovery.artist_policy_revision.policy_id",
                "discovery.artist_policy_revision.revision",
            ],
            name="discovery_candidate_policy_revision_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_acquisition_attempt_id"],
            ["discovery.acquisition_attempt.acquisition_attempt_id"],
            name="discovery_candidate_current_attempt_fkey",
            ondelete="RESTRICT",
            use_alter=True,
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
        CheckConstraint(
            "selection_origin IN ('MANUAL', 'AUTOMATIC')", name="ck_candidate_selection_origin"
        ),
        CheckConstraint(
            "(selection_origin = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR "
            "(selection_origin = 'AUTOMATIC' AND policy_id IS NOT NULL AND policy_revision >= 1)",
            name="ck_candidate_policy_lineage",
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
Index(
    "uq_source_authorization_current_scope",
    SourceAuthorizationRow.user_id,
    SourceAuthorizationRow.provider_id,
    SourceAuthorizationRow.market_scope,
    SourceAuthorizationRow.canonical_artist_id,
    SourceAuthorizationRow.purpose,
    unique=True,
    postgresql_where=text("revoked_at IS NULL"),
)
Index("ix_bulk_operation_item_candidate", BulkOperationItemRow.candidate_id)
Index(
    "ix_source_authorization_owner_expiry",
    SourceAuthorizationRow.user_id,
    SourceAuthorizationRow.expires_at,
)
Index("ix_artist_policy_due", ArtistPolicyRow.automation_enabled, ArtistPolicyRow.next_eligible_at)
Index("ix_discovery_run_policy_slot", DiscoveryRunRow.policy_id, DiscoveryRunRow.due_slot_at)
Index(
    "ix_acquisition_attempt_candidate",
    AcquisitionAttemptRow.candidate_id,
    AcquisitionAttemptRow.created_at,
)
Index(
    "ix_candidate_action_receipt_candidate",
    CandidateActionReceiptRow.candidate_id,
    CandidateActionReceiptRow.created_at,
)
Index(
    "uq_acquisition_attempt_active_candidate",
    AcquisitionAttemptRow.candidate_id,
    unique=True,
    postgresql_where=text("state IN ('QUEUED', 'RUNNING')"),
)


__all__ = (
    "AcquisitionAttemptRow",
    "ArtistPolicyRevisionRow",
    "ArtistPolicyRow",
    "BulkOperationItemRow",
    "BulkOperationRow",
    "CandidateActionReceiptRow",
    "DiscoveryCandidateRow",
    "DiscoveryRunCandidateRow",
    "DiscoveryRunPageRow",
    "DiscoveryRunRow",
    "SourceAuthorizationRow",
)
