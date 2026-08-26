# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the vault PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
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


class VaultObjectRow(Base):
    """Persistence row for ``vault.vault_object``."""

    __tablename__ = "vault_object"

    vault_object_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    byte_size: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    detected_mime_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    commit_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'STAGING'"),
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    verification_error: Mapped[str | None] = mapped_column(
        Text(),
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
        PrimaryKeyConstraint("vault_object_id", name="vault_object_pkey"),
        UniqueConstraint("sha256", name="vault_object_sha256_key"),
        CheckConstraint(
            "byte_size > 0",
            name="vault_object_byte_size_check",
        ),
        CheckConstraint(
            "length(detected_mime_type) BETWEEN 1 AND 200",
            name="vault_object_detected_mime_type_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="vault_object_row_version_check",
        ),
        CheckConstraint(
            "octet_length(sha256) = 32",
            name="ck_vault_object_sha256_len",
        ),
        CheckConstraint(
            "commit_status IN ('STAGING', 'COMMITTED', 'QUARANTINED', 'DELETED')",
            name="ck_vault_object_commit_status",
        ),
        CheckConstraint(
            "commit_status <> 'COMMITTED' OR committed_at IS NOT NULL",
            name="ck_vault_object_committed_at",
        ),
        {"schema": "vault"},
    )


class VaultReplicaRow(Base):
    """Persistence row for ``vault.vault_replica``."""

    __tablename__ = "vault_replica"

    vault_replica_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    vault_object_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    storage_backend: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    replica_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'COPYING'"),
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
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
        PrimaryKeyConstraint("vault_replica_id", name="vault_replica_pkey"),
        ForeignKeyConstraint(
            ["vault_object_id"],
            ["vault.vault_object.vault_object_id"],
            name="vault_replica_vault_object_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(storage_backend) BETWEEN 1 AND 100",
            name="vault_replica_storage_backend_check",
        ),
        CheckConstraint(
            "length(storage_key) BETWEEN 1 AND 2000",
            name="vault_replica_storage_key_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="vault_replica_row_version_check",
        ),
        UniqueConstraint("storage_backend", "storage_key", name="uq_vault_replica_backend_key"),
        CheckConstraint(
            "replica_status IN ('AVAILABLE', 'MISSING', 'CORRUPT', 'COPYING', 'QUARANTINED')",
            name="ck_vault_replica_status",
        ),
        {"schema": "vault"},
    )


class AudioVariantRow(Base):
    """Persistence row for ``vault.audio_variant``."""

    __tablename__ = "audio_variant"

    audio_variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    vault_object_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    codec: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    container: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    bitrate_bps: Mapped[int | None] = mapped_column(
        Integer(),
        nullable=True,
    )
    bit_depth: Mapped[int | None] = mapped_column(
        Integer(),
        nullable=True,
    )
    sample_rate_hz: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    channels: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    validation_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'VALID'"),
    )
    quality_score: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 4),
        nullable=True,
    )
    quality_policy_version: Mapped[str | None] = mapped_column(
        Text(),
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("audio_variant_id", name="audio_variant_pkey"),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="audio_variant_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("vault_object_id", name="audio_variant_vault_object_id_key"),
        ForeignKeyConstraint(
            ["vault_object_id"],
            ["vault.vault_object.vault_object_id"],
            name="audio_variant_vault_object_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(codec) BETWEEN 1 AND 100",
            name="audio_variant_codec_check",
        ),
        CheckConstraint(
            "length(container) BETWEEN 1 AND 100",
            name="audio_variant_container_check",
        ),
        CheckConstraint(
            "bitrate_bps IS NULL OR bitrate_bps > 0",
            name="audio_variant_bitrate_bps_check",
        ),
        CheckConstraint(
            "bit_depth IS NULL OR bit_depth > 0",
            name="audio_variant_bit_depth_check",
        ),
        CheckConstraint(
            "sample_rate_hz > 0",
            name="audio_variant_sample_rate_hz_check",
        ),
        CheckConstraint(
            "channels BETWEEN 1 AND 64",
            name="audio_variant_channels_check",
        ),
        CheckConstraint(
            "duration_ms > 0",
            name="audio_variant_duration_ms_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="audio_variant_row_version_check",
        ),
        CheckConstraint(
            "validation_status IN ('VALID', 'SUSPECT', 'INVALID', 'QUARANTINED')",
            name="ck_audio_variant_validation_status",
        ),
        CheckConstraint(
            "quality_score IS NULL OR quality_score >= 0",
            name="ck_audio_variant_quality_score",
        ),
        {"schema": "vault"},
    )


class AudioFingerprintRow(Base):
    """Persistence row for ``vault.audio_fingerprint``."""

    __tablename__ = "audio_fingerprint"

    audio_fingerprint_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    audio_variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    algorithm: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    algorithm_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    tool_build_sha256: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    decoder_name: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    decoder_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    duration_ms: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    fingerprint_hash: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    fingerprint_payload: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    quality_flags: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("audio_fingerprint_id", name="audio_fingerprint_pkey"),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="audio_fingerprint_audio_variant_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(algorithm) BETWEEN 1 AND 100",
            name="audio_fingerprint_algorithm_check",
        ),
        CheckConstraint(
            "length(algorithm_version) BETWEEN 1 AND 100",
            name="audio_fingerprint_algorithm_version_check",
        ),
        CheckConstraint(
            "duration_ms > 0",
            name="audio_fingerprint_duration_ms_check",
        ),
        UniqueConstraint(
            "audio_variant_id",
            "algorithm",
            "algorithm_version",
            name="uq_audio_fingerprint_variant_version",
        ),
        CheckConstraint(
            "fingerprint_hash IS NOT NULL OR fingerprint_payload IS NOT NULL",
            name="ck_audio_fingerprint_payload",
        ),
        CheckConstraint(
            "tool_build_sha256 IS NULL OR octet_length(tool_build_sha256) = 32",
            name="ck_audio_fingerprint_tool_hash_len",
        ),
        {"schema": "vault"},
    )


class RecordingCanonicalVariantRow(Base):
    """Persistence row for ``vault.recording_canonical_variant``."""

    __tablename__ = "recording_canonical_variant"

    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    audio_variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    reason: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    selected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("recording_id", name="recording_canonical_variant_pkey"),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_canonical_variant_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "audio_variant_id", name="recording_canonical_variant_audio_variant_id_key"
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="recording_canonical_variant_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(policy_version) BETWEEN 1 AND 100",
            name="recording_canonical_variant_policy_version_check",
        ),
        {"schema": "vault"},
    )


class AcquisitionRecordRow(Base):
    """Persistence row for ``vault.acquisition_record``."""

    __tablename__ = "acquisition_record"

    acquisition_record_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    audio_variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    provider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    external_reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    authorized_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    rights_capability: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    source_uri_encrypted: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    acquired_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    adapter_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("acquisition_record_id", name="acquisition_record_pkey"),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="acquisition_record_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="acquisition_record_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["external_reference_id"],
            ["identity.external_reference.external_reference_id"],
            name="acquisition_record_external_reference_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["authorized_by_user_id"],
            ["account.user_account.user_id"],
            name="acquisition_record_authorized_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "rights_capability IN ('AUTHORIZED_DOWNLOAD', 'USER_UPLOAD', 'LOCAL_IMPORT', 'RESTORE')",
            name="ck_acquisition_rights_capability",
        ),
        {"schema": "vault"},
    )


class UploadSessionRow(Base):
    """Durable, owner-bound P06 resumable upload state."""

    __tablename__ = "upload_session"

    upload_session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_kind: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'DEVICE'"))
    source_candidate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    source_acquisition_attempt_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    target_recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text(), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    declared_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    computed_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    expected_size: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    received_size: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, server_default=text("0")
    )
    chunk_size: Mapped[int] = mapped_column(Integer(), nullable=False)
    max_chunks: Mapped[int] = mapped_column(Integer(), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    staging_key: Mapped[str] = mapped_column(Text(), nullable=False)
    state: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'OPEN'"))
    job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    vault_object_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    audio_variant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    sealed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))

    __table_args__ = (
        PrimaryKeyConstraint("upload_session_id", name="upload_session_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="upload_session_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_candidate_id"],
            ["discovery.candidate.candidate_id"],
            name="upload_session_source_candidate_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_acquisition_attempt_id"],
            ["discovery.acquisition_attempt.acquisition_attempt_id"],
            name="upload_session_source_acquisition_attempt_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="upload_session_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_recording_id"],
            ["catalog.recording.recording_id"],
            name="upload_session_target_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.job.job_id"],
            name="upload_session_job_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["vault_object_id"],
            ["vault.vault_object.vault_object_id"],
            name="upload_session_vault_object_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="upload_session_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_upload_session_device_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_upload_session_user_idempotency"),
        UniqueConstraint("upload_session_id", "user_id", name="uq_upload_session_owner_lookup"),
        UniqueConstraint("staging_key", name="uq_upload_session_staging_key"),
        UniqueConstraint(
            "source_acquisition_attempt_id",
            name="uq_upload_session_source_acquisition_attempt",
        ),
        CheckConstraint(
            "(actor_kind = 'DEVICE' AND device_id IS NOT NULL AND source_candidate_id IS NULL AND source_acquisition_attempt_id IS NULL) OR "
            "(actor_kind = 'PROVIDER' AND device_id IS NULL AND source_candidate_id IS NOT NULL AND source_acquisition_attempt_id IS NOT NULL)",
            name="ck_upload_session_actor",
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 200", name="upload_session_idempotency_key_check"
        ),
        CheckConstraint(
            "octet_length(request_hash) = 32", name="ck_upload_session_request_hash_len"
        ),
        CheckConstraint(
            "declared_sha256 IS NULL OR octet_length(declared_sha256) = 32",
            name="ck_upload_session_declared_sha256_len",
        ),
        CheckConstraint(
            "computed_sha256 IS NULL OR octet_length(computed_sha256) = 32",
            name="ck_upload_session_computed_sha256_len",
        ),
        CheckConstraint(
            "expected_size BETWEEN 1 AND 4294967296", name="upload_session_expected_size_check"
        ),
        CheckConstraint("chunk_size BETWEEN 1 AND 1048576", name="upload_session_chunk_size_check"),
        CheckConstraint("max_chunks BETWEEN 1 AND 4096", name="upload_session_max_chunks_check"),
        CheckConstraint(
            "expected_size <= chunk_size::bigint * max_chunks::bigint",
            name="ck_upload_session_capacity",
        ),
        CheckConstraint(
            "received_size BETWEEN 0 AND expected_size", name="ck_upload_session_received_size"
        ),
        CheckConstraint(
            "chunk_count BETWEEN 0 AND max_chunks", name="ck_upload_session_chunk_count"
        ),
        CheckConstraint(
            "staging_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'",
            name="ck_upload_session_staging_key",
        ),
        CheckConstraint(
            "state IN ('OPEN', 'SEALED', 'PROCESSING', 'COMMIT_PREPARED', 'COMMITTED', 'REUSED', 'QUARANTINED', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="ck_upload_session_state",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_upload_session_error_code",
        ),
        CheckConstraint("expires_at > created_at", name="ck_upload_session_expiry"),
        CheckConstraint("row_version >= 1", name="upload_session_row_version_check"),
        CheckConstraint(
            "(state = 'OPEN' AND job_id IS NULL AND vault_object_id IS NULL AND audio_variant_id IS NULL AND computed_sha256 IS NULL AND sealed_at IS NULL AND completed_at IS NULL AND error_code IS NULL) OR (state = 'SEALED' AND vault_object_id IS NULL AND audio_variant_id IS NULL AND computed_sha256 IS NULL AND sealed_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL) OR (state = 'PROCESSING' AND job_id IS NOT NULL AND vault_object_id IS NULL AND audio_variant_id IS NULL AND sealed_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL) OR (state = 'COMMIT_PREPARED' AND job_id IS NOT NULL AND vault_object_id IS NOT NULL AND audio_variant_id IS NULL AND computed_sha256 IS NOT NULL AND sealed_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL) OR (state IN ('COMMITTED', 'REUSED') AND job_id IS NOT NULL AND vault_object_id IS NOT NULL AND audio_variant_id IS NOT NULL AND computed_sha256 IS NOT NULL AND sealed_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL) OR (state IN ('QUARANTINED', 'FAILED') AND job_id IS NOT NULL AND vault_object_id IS NULL AND audio_variant_id IS NULL AND sealed_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NOT NULL) OR (state IN ('CANCELLED', 'EXPIRED') AND vault_object_id IS NULL AND audio_variant_id IS NULL AND computed_sha256 IS NULL AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_upload_session_state_links",
        ),
        {"schema": "vault"},
    )


class UploadChunkRow(Base):
    """One durable receipt; primary-key conflicts make retries explicit."""

    __tablename__ = "upload_chunk"

    upload_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    start_offset: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer(), nullable=False)
    sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("upload_session_id", "chunk_index", name="upload_chunk_pkey"),
        ForeignKeyConstraint(
            ["upload_session_id"],
            ["vault.upload_session.upload_session_id"],
            name="upload_chunk_upload_session_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("upload_session_id", "start_offset", name="uq_upload_chunk_start_offset"),
        CheckConstraint("chunk_index >= 0", name="ck_upload_chunk_index"),
        CheckConstraint("start_offset >= 0", name="ck_upload_chunk_start_offset"),
        CheckConstraint("byte_size BETWEEN 1 AND 1048576", name="upload_chunk_byte_size_check"),
        CheckConstraint("octet_length(sha256) = 32", name="ck_upload_chunk_sha256_len"),
        {"schema": "vault"},
    )


Index(
    "ix_vault_object_status",
    VaultObjectRow.commit_status,
    VaultObjectRow.created_at,
)

Index(
    "ix_vault_replica_object_status",
    VaultReplicaRow.vault_object_id,
    VaultReplicaRow.replica_status,
)

Index(
    "ix_audio_variant_recording_valid",
    AudioVariantRow.recording_id,
    AudioVariantRow.quality_score.desc().nulls_last(),
    postgresql_where=text("validation_status = 'VALID' AND deleted_at IS NULL"),
)

Index(
    "ix_audio_fingerprint_candidate",
    AudioFingerprintRow.algorithm,
    AudioFingerprintRow.algorithm_version,
    AudioFingerprintRow.fingerprint_hash,
    postgresql_where=text("fingerprint_hash IS NOT NULL"),
)

Index(
    "ix_acquisition_record_variant",
    AcquisitionRecordRow.audio_variant_id,
    AcquisitionRecordRow.acquired_at.desc(),
)

Index(
    "ix_upload_session_owner_state_time",
    UploadSessionRow.user_id,
    UploadSessionRow.device_id,
    UploadSessionRow.state,
    UploadSessionRow.updated_at.desc(),
)

Index(
    "ix_upload_session_state_expiry",
    UploadSessionRow.state,
    UploadSessionRow.expires_at,
)

Index(
    "ix_upload_session_computed_sha256",
    UploadSessionRow.computed_sha256,
    postgresql_where=text("computed_sha256 IS NOT NULL"),
)

Index(
    "ix_upload_session_job",
    UploadSessionRow.job_id,
    postgresql_where=text("job_id IS NOT NULL"),
)


__all__ = (
    "AcquisitionRecordRow",
    "AudioFingerprintRow",
    "AudioVariantRow",
    "RecordingCanonicalVariantRow",
    "UploadChunkRow",
    "UploadSessionRow",
    "VaultObjectRow",
    "VaultReplicaRow",
)
