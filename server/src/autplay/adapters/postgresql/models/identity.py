# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the identity PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
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


class SourceProviderRow(Base):
    """Persistence row for ``identity.source_provider``."""

    __tablename__ = "source_provider"

    provider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    provider_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        Text(),
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
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("ARRAY[]::text[]"),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("true"),
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
        PrimaryKeyConstraint("provider_id", name="source_provider_pkey"),
        UniqueConstraint("provider_key", name="source_provider_provider_key_key"),
        CheckConstraint(
            "provider_key ~ '^[a-z0-9][a-z0-9._-]{1,99}$'",
            name="source_provider_provider_key_check",
        ),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 200",
            name="source_provider_display_name_check",
        ),
        CheckConstraint(
            "length(adapter_id) BETWEEN 1 AND 200",
            name="source_provider_adapter_id_check",
        ),
        CheckConstraint(
            "length(adapter_version) BETWEEN 1 AND 100",
            name="source_provider_adapter_version_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="source_provider_row_version_check",
        ),
        CheckConstraint(
            "capabilities <@ ARRAY['SEARCH', 'METADATA', 'IMPORT', 'DOWNLOAD', 'STREAM', 'RELEASE_WATCH']::text[]",
            name="ck_source_provider_capabilities",
        ),
        {"schema": "identity"},
    )


class RecordingIdentifierRow(Base):
    """Persistence row for ``identity.recording_identifier``."""

    __tablename__ = "recording_identifier"

    recording_identifier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    scheme: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    provider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        server_default=text("0"),
    )
    verified: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("recording_identifier_id", name="recording_identifier_pkey"),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_identifier_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(value) BETWEEN 1 AND 500",
            name="recording_identifier_value_check",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="recording_identifier_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scheme IN ('ISRC', 'MBID', 'OTHER')",
            name="ck_recording_identifier_scheme",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_recording_identifier_confidence",
        ),
        UniqueConstraint(
            "recording_id", "scheme", "value", name="uq_recording_identifier_recording_scheme_value"
        ),
        {"schema": "identity"},
    )


class ExternalReferenceRow(Base):
    """Persistence row for ``identity.external_reference``."""

    __tablename__ = "external_reference"

    external_reference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    provider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    external_entity_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    market_scope: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'GLOBAL'"),
    )
    artist_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    recording_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    release_group_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    release_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
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
        PrimaryKeyConstraint("external_reference_id", name="external_reference_pkey"),
        ForeignKeyConstraint(
            ["provider_id"],
            ["identity.source_provider.provider_id"],
            name="external_reference_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(external_entity_type) BETWEEN 1 AND 100",
            name="external_reference_external_entity_type_check",
        ),
        CheckConstraint(
            "length(external_id) BETWEEN 1 AND 1000",
            name="external_reference_external_id_check",
        ),
        CheckConstraint(
            "length(market_scope) BETWEEN 1 AND 100",
            name="external_reference_market_scope_check",
        ),
        ForeignKeyConstraint(
            ["artist_id"],
            ["catalog.artist.artist_id"],
            name="external_reference_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="external_reference_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_group_id"],
            ["catalog.release_group.release_group_id"],
            name="external_reference_release_group_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["release_id"],
            ["catalog.release.release_id"],
            name="external_reference_release_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="external_reference_row_version_check",
        ),
        CheckConstraint(
            "num_nonnulls(artist_id, recording_id, release_group_id, release_id) <= 1",
            name="ck_external_reference_single_target",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_external_reference_seen_order",
        ),
        UniqueConstraint(
            "provider_id",
            "external_entity_type",
            "external_id",
            "market_scope",
            name="uq_external_reference_namespace",
        ),
        {"schema": "identity"},
    )


class SourceObservationRow(Base):
    """Persistence row for ``identity.source_observation``."""

    __tablename__ = "source_observation"

    source_observation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    external_reference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    adapter_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    raw_metadata_hash: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    raw_metadata: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("source_observation_id", name="source_observation_pkey"),
        ForeignKeyConstraint(
            ["external_reference_id"],
            ["identity.external_reference.external_reference_id"],
            name="source_observation_external_reference_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(adapter_version) BETWEEN 1 AND 100",
            name="source_observation_adapter_version_check",
        ),
        CheckConstraint(
            "octet_length(raw_metadata_hash) = 32",
            name="ck_source_observation_hash_len",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_source_observation_confidence",
        ),
        {"schema": "identity"},
    )


class MatcherReleaseRow(Base):
    """Persistence row for ``identity.matcher_release``."""

    __tablename__ = "matcher_release"

    matcher_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    candidate_generation_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalization_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    feature_extractor_versions: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    feature_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    manifest_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("matcher_version", name="matcher_release_pkey"),
        CheckConstraint(
            "length(matcher_version) BETWEEN 1 AND 200",
            name="matcher_release_matcher_version_check",
        ),
        CheckConstraint(
            "length(candidate_generation_version) BETWEEN 1 AND 200",
            name="matcher_release_candidate_generation_version_check",
        ),
        CheckConstraint(
            "length(normalization_version) BETWEEN 1 AND 200",
            name="matcher_release_normalization_version_check",
        ),
        CheckConstraint(
            "length(feature_schema_version) BETWEEN 1 AND 100",
            name="matcher_release_feature_schema_version_check",
        ),
        UniqueConstraint("manifest_sha256", name="matcher_release_manifest_sha256_key"),
        CheckConstraint(
            "jsonb_typeof(feature_extractor_versions) = 'object' AND octet_length(convert_to(feature_extractor_versions::text, 'UTF8')) <= 131072",
            name="ck_matcher_release_feature_manifest",
        ),
        CheckConstraint(
            "octet_length(manifest_sha256) = 32",
            name="ck_matcher_release_manifest_hash_len",
        ),
        {"schema": "identity"},
    )


class CalibratorReleaseRow(Base):
    """Persistence row for ``identity.calibrator_release``."""

    __tablename__ = "calibrator_release"

    calibrator_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    matcher_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    evidence_mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    artifact_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    input_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("calibrator_version", name="calibrator_release_pkey"),
        CheckConstraint(
            "length(calibrator_version) BETWEEN 1 AND 200",
            name="calibrator_release_calibrator_version_check",
        ),
        ForeignKeyConstraint(
            ["matcher_version"],
            ["identity.matcher_release.matcher_version"],
            name="calibrator_release_matcher_version_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("artifact_sha256", name="calibrator_release_artifact_sha256_key"),
        CheckConstraint(
            "length(input_schema_version) BETWEEN 1 AND 100",
            name="calibrator_release_input_schema_version_check",
        ),
        UniqueConstraint(
            "calibrator_version",
            "matcher_version",
            "evidence_mode",
            name="uq_calibrator_release_matcher_mode",
        ),
        CheckConstraint(
            "evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE')",
            name="ck_calibrator_release_evidence_mode",
        ),
        CheckConstraint(
            "octet_length(artifact_sha256) = 32",
            name="ck_calibrator_release_artifact_hash_len",
        ),
        {"schema": "identity"},
    )


class ThresholdSetRow(Base):
    """Persistence row for ``identity.threshold_set``."""

    __tablename__ = "threshold_set"

    threshold_set_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    matcher_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    calibrator_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    evidence_mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    minimum_evidence_tier: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    auto_threshold: Mapped[Decimal] = mapped_column(
        Numeric(7, 6),
        nullable=False,
    )
    review_threshold: Mapped[Decimal] = mapped_column(
        Numeric(7, 6),
        nullable=False,
    )
    margin_threshold: Mapped[Decimal] = mapped_column(
        Numeric(7, 6),
        nullable=False,
    )
    benchmark_report_sha256: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    gate_metadata: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    gate_metadata_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("threshold_set_version", name="threshold_set_pkey"),
        CheckConstraint(
            "length(threshold_set_version) BETWEEN 1 AND 200",
            name="threshold_set_threshold_set_version_check",
        ),
        ForeignKeyConstraint(
            ["matcher_version"],
            ["identity.matcher_release.matcher_version"],
            name="threshold_set_matcher_version_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "auto_threshold BETWEEN 0 AND 1",
            name="threshold_set_auto_threshold_check",
        ),
        CheckConstraint(
            "review_threshold BETWEEN 0 AND 1",
            name="threshold_set_review_threshold_check",
        ),
        CheckConstraint(
            "margin_threshold BETWEEN 0 AND 1",
            name="threshold_set_margin_threshold_check",
        ),
        CheckConstraint(
            "length(gate_metadata_schema_version) BETWEEN 1 AND 100",
            name="threshold_set_gate_metadata_schema_version_check",
        ),
        ForeignKeyConstraint(
            ["calibrator_version", "matcher_version", "evidence_mode"],
            [
                "identity.calibrator_release.calibrator_version",
                "identity.calibrator_release.matcher_version",
                "identity.calibrator_release.evidence_mode",
            ],
            name="fk_threshold_set_calibrator_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "threshold_set_version",
            "evidence_mode",
            "minimum_evidence_tier",
            name="uq_threshold_set_scope",
        ),
        CheckConstraint(
            "evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')",
            name="ck_threshold_set_evidence_mode",
        ),
        CheckConstraint(
            "minimum_evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')",
            name="ck_threshold_set_evidence_tier",
        ),
        CheckConstraint(
            "auto_threshold >= review_threshold",
            name="ck_threshold_set_order",
        ),
        CheckConstraint(
            "benchmark_report_sha256 IS NULL OR octet_length(benchmark_report_sha256) = 32",
            name="ck_threshold_set_benchmark_hash_len",
        ),
        CheckConstraint(
            "jsonb_typeof(gate_metadata) = 'object' AND octet_length(convert_to(gate_metadata::text, 'UTF8')) <= 131072",
            name="ck_threshold_set_gate_metadata",
        ),
        {"schema": "identity"},
    )


class MatchPolicyActivationRow(Base):
    """Persistence row for ``identity.match_policy_activation``."""

    __tablename__ = "match_policy_activation"

    activation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    evidence_mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    evidence_tier: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    threshold_set_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    supersedes_activation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'ADMIN'"),
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("activation_id", name="match_policy_activation_pkey"),
        CheckConstraint(
            "sequence_no >= 1",
            name="match_policy_activation_sequence_no_check",
        ),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["account.user_account.user_id"],
            name="match_policy_activation_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 4000",
            name="match_policy_activation_reason_check",
        ),
        ForeignKeyConstraint(
            ["threshold_set_version", "evidence_mode", "evidence_tier"],
            [
                "identity.threshold_set.threshold_set_version",
                "identity.threshold_set.evidence_mode",
                "identity.threshold_set.minimum_evidence_tier",
            ],
            name="fk_match_policy_activation_threshold_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_activation_id"],
            ["identity.match_policy_activation.activation_id"],
            name="fk_match_policy_activation_predecessor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "evidence_mode",
            "evidence_tier",
            "sequence_no",
            name="uq_match_policy_activation_scope_sequence",
        ),
        UniqueConstraint("supersedes_activation_id", name="uq_match_policy_activation_successor"),
        CheckConstraint(
            "evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')",
            name="ck_match_policy_activation_mode",
        ),
        CheckConstraint(
            "evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')",
            name="ck_match_policy_activation_tier",
        ),
        CheckConstraint(
            "action IN ('ACTIVATE', 'DEACTIVATE', 'ROLLBACK')",
            name="ck_match_policy_activation_action",
        ),
        CheckConstraint(
            "actor_type = 'ADMIN'",
            name="ck_match_policy_activation_actor",
        ),
        CheckConstraint(
            "(sequence_no = 1 AND supersedes_activation_id IS NULL) OR (sequence_no > 1 AND supersedes_activation_id IS NOT NULL)",
            name="ck_match_policy_activation_chain",
        ),
        {"schema": "identity"},
    )


class RecordingRedirectRow(Base):
    """Persistence row for ``identity.recording_redirect``."""

    __tablename__ = "recording_redirect"

    source_recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    target_recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    change_set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("source_recording_id", name="recording_redirect_pkey"),
        ForeignKeyConstraint(
            ["source_recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_redirect_source_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_redirect_target_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["change_set_id"],
            ["audit.catalog_change_set.change_set_id"],
            name="recording_redirect_change_set_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 4000",
            name="recording_redirect_reason_check",
        ),
        CheckConstraint(
            "source_recording_id <> target_recording_id",
            name="ck_recording_redirect_not_self",
        ),
        {"schema": "identity"},
    )


class MatchDecisionRow(Base):
    """Persistence row for ``identity.match_decision``."""

    __tablename__ = "match_decision"

    decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    query_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    device_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    import_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    user_track_ref_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    local_audio_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    external_reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    vault_object_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    audio_variant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    query_snapshot: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    query_snapshot_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    snapshot_canonicalization_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    query_snapshot_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    decision_kind: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    execution_mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    review_action: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    reviewed_candidate_evidence_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    candidate_recording_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    decision_state: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    candidate_evidence_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    candidate_evidence_size_bytes: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    evidence_mode: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    candidate_generation_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalization_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    feature_extractor_versions: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    matcher_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    calibrator_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    threshold_set_version: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_score: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    top2_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    margin: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    evidence_tier: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    feature_scores: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    hard_conflicts: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    candidate_origins: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    explanation_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    idempotency_scope: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    request_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    supersedes_decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    supersession_reason: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("decision_id", name="match_decision_pkey"),
        ForeignKeyConstraint(
            ["owner_user_id"],
            ["account.user_account.user_id"],
            name="match_decision_owner_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="match_decision_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_entry_id"],
            ["importing.import_entry.import_entry_id"],
            name="match_decision_import_entry_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="match_decision_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["external_reference_id"],
            ["identity.external_reference.external_reference_id"],
            name="match_decision_external_reference_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["vault_object_id"],
            ["vault.vault_object.vault_object_id"],
            name="match_decision_vault_object_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="match_decision_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(query_snapshot_schema_version) BETWEEN 1 AND 100",
            name="match_decision_query_snapshot_schema_version_check",
        ),
        CheckConstraint(
            "length(snapshot_canonicalization_version) BETWEEN 1 AND 100",
            name="match_decision_snapshot_canonicalization_version_check",
        ),
        ForeignKeyConstraint(
            ["candidate_recording_id"],
            ["catalog.recording.recording_id"],
            name="match_decision_candidate_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "candidate_count BETWEEN 0 AND 100",
            name="match_decision_candidate_count_check",
        ),
        CheckConstraint(
            "candidate_evidence_size_bytes BETWEEN 0 AND 4194304",
            name="match_decision_candidate_evidence_size_bytes_check",
        ),
        CheckConstraint(
            "length(candidate_generation_version) BETWEEN 1 AND 200",
            name="match_decision_candidate_generation_version_check",
        ),
        CheckConstraint(
            "length(normalization_version) BETWEEN 1 AND 200",
            name="match_decision_normalization_version_check",
        ),
        ForeignKeyConstraint(
            ["matcher_version"],
            ["identity.matcher_release.matcher_version"],
            name="match_decision_matcher_version_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["calibrator_version"],
            ["identity.calibrator_release.calibrator_version"],
            name="match_decision_calibrator_version_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["threshold_set_version"],
            ["identity.threshold_set.threshold_set_version"],
            name="match_decision_threshold_set_version_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(explanation_schema_version) BETWEEN 1 AND 100",
            name="match_decision_explanation_schema_version_check",
        ),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["account.user_account.user_id"],
            name="match_decision_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(idempotency_scope) BETWEEN 1 AND 100",
            name="match_decision_idempotency_scope_check",
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 200",
            name="match_decision_idempotency_key_check",
        ),
        ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["identity.match_decision.decision_id"],
            name="match_decision_supersedes_decision_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="uq_match_decision_idempotency"
        ),
        UniqueConstraint("supersedes_decision_id", name="uq_match_decision_successor"),
        CheckConstraint(
            "(query_type = 'IMPORT_ENTRY' AND import_entry_id IS NOT NULL AND owner_user_id IS NOT NULL AND device_id IS NULL AND num_nonnulls(user_track_ref_id, local_audio_id, external_reference_id, vault_object_id, audio_variant_id) = 0) OR (query_type = 'USER_TRACK_REF' AND user_track_ref_id IS NOT NULL AND owner_user_id IS NOT NULL AND device_id IS NULL AND num_nonnulls(import_entry_id, local_audio_id, external_reference_id, vault_object_id, audio_variant_id) = 0) OR (query_type = 'LOCAL_AUDIO' AND local_audio_id IS NOT NULL AND owner_user_id IS NOT NULL AND device_id IS NOT NULL AND num_nonnulls(import_entry_id, user_track_ref_id, external_reference_id, vault_object_id, audio_variant_id) = 0) OR (query_type = 'EXTERNAL_REFERENCE' AND external_reference_id IS NOT NULL AND device_id IS NULL AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id, vault_object_id, audio_variant_id) = 0) OR (query_type = 'VAULT_OBJECT' AND vault_object_id IS NOT NULL AND device_id IS NULL AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id, external_reference_id, audio_variant_id) = 0) OR (query_type = 'AUDIO_VARIANT' AND audio_variant_id IS NOT NULL AND device_id IS NULL AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id, external_reference_id, vault_object_id) = 0)",
            name="ck_match_decision_query_type",
        ),
        CheckConstraint(
            "jsonb_typeof(query_snapshot) = 'object' AND octet_length(convert_to(query_snapshot::text, 'UTF8')) <= 131072 AND octet_length(query_snapshot_sha256) = 32",
            name="ck_match_decision_snapshot",
        ),
        CheckConstraint(
            "decision_kind IN ('EVALUATION', 'REVIEW_ACTION') AND execution_mode IN ('SHADOW', 'APPLIED') AND ( (decision_kind = 'EVALUATION' AND review_action IS NULL AND reviewed_candidate_evidence_id IS NULL) OR (decision_kind = 'REVIEW_ACTION' AND execution_mode = 'APPLIED' AND review_action IN ('ACCEPT', 'REJECT', 'KEEP_UNRESOLVED', 'CREATE_RECORDING') AND supersedes_decision_id IS NOT NULL) )",
            name="ck_match_decision_kind_mode",
        ),
        CheckConstraint(
            "decision_state IN ( 'AUTO_MATCH', 'REVIEW_REQUIRED', 'NO_MATCH', 'INTEGRITY_CONFLICT', 'DEFERRED_EVIDENCE' )",
            name="ck_match_decision_state",
        ),
        CheckConstraint(
            "evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')",
            name="ck_match_decision_evidence_mode",
        ),
        CheckConstraint(
            "evidence_tier IS NULL OR evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')",
            name="ck_match_decision_evidence_tier",
        ),
        CheckConstraint(
            "(raw_score IS NULL OR raw_score BETWEEN 0 AND 1) AND (confidence IS NULL OR confidence BETWEEN 0 AND 1) AND (top2_confidence IS NULL OR top2_confidence BETWEEN 0 AND 1) AND (margin IS NULL OR margin BETWEEN 0 AND 1) AND ( top2_confidence IS NULL AND margin IS NULL OR confidence IS NOT NULL AND top2_confidence IS NOT NULL AND confidence >= top2_confidence AND margin = confidence - top2_confidence )",
            name="ck_match_decision_scores",
        ),
        CheckConstraint(
            "jsonb_typeof(feature_extractor_versions) = 'object' AND jsonb_typeof(feature_scores) = 'array' AND jsonb_typeof(hard_conflicts) = 'array' AND jsonb_typeof(candidate_origins) = 'array' AND octet_length(convert_to(feature_extractor_versions::text, 'UTF8')) <= 131072 AND octet_length(convert_to(feature_scores::text, 'UTF8')) <= 131072 AND octet_length(convert_to(hard_conflicts::text, 'UTF8')) <= 131072 AND octet_length(convert_to(candidate_origins::text, 'UTF8')) <= 131072 AND jsonb_array_length(feature_scores) <= 256 AND jsonb_array_length(hard_conflicts) <= 64 AND jsonb_array_length(candidate_origins) <= 256",
            name="ck_match_decision_json",
        ),
        CheckConstraint(
            "actor_type IN ('SYSTEM', 'USER', 'ADMIN') AND ((actor_type = 'SYSTEM' AND actor_user_id IS NULL) OR (actor_type IN ('USER', 'ADMIN') AND actor_user_id IS NOT NULL))",
            name="ck_match_decision_actor",
        ),
        CheckConstraint(
            "octet_length(query_snapshot_sha256) = 32 AND octet_length(candidate_evidence_sha256) = 32 AND octet_length(request_sha256) = 32",
            name="ck_match_decision_hashes",
        ),
        CheckConstraint(
            "decision_state <> 'AUTO_MATCH' OR (decision_kind = 'EVALUATION' AND execution_mode = 'APPLIED' AND actor_type = 'SYSTEM' AND candidate_recording_id IS NOT NULL AND calibrator_version IS NOT NULL AND threshold_set_version IS NOT NULL AND confidence IS NOT NULL AND evidence_tier IS NOT NULL AND jsonb_array_length(hard_conflicts) = 0)",
            name="ck_match_decision_auto_match",
        ),
        CheckConstraint(
            "(supersedes_decision_id IS NULL AND supersession_reason IS NULL) OR (supersedes_decision_id IS NOT NULL AND length(supersession_reason) BETWEEN 1 AND 4000)",
            name="ck_match_decision_supersession_reason",
        ),
        ForeignKeyConstraint(
            ["reviewed_candidate_evidence_id", "supersedes_decision_id", "candidate_recording_id"],
            [
                "identity.match_candidate_evidence.match_candidate_evidence_id",
                "identity.match_candidate_evidence.decision_id",
                "identity.match_candidate_evidence.recording_id",
            ],
            name="fk_match_decision_reviewed_evidence",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "identity"},
    )


class MatchCandidateEvidenceRow(Base):
    """Persistence row for ``identity.match_candidate_evidence``."""

    __tablename__ = "match_candidate_evidence"

    match_candidate_evidence_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    raw_score: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    evidence_tier: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    feature_scores: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    hard_conflicts: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    candidate_origins: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    extractor_versions: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    evidence_schema_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    evidence_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    evidence_document_size_bytes: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("match_candidate_evidence_id", name="match_candidate_evidence_pkey"),
        ForeignKeyConstraint(
            ["decision_id"],
            ["identity.match_decision.decision_id"],
            name="match_candidate_evidence_decision_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="match_candidate_evidence_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "rank BETWEEN 1 AND 100",
            name="match_candidate_evidence_rank_check",
        ),
        CheckConstraint(
            "length(evidence_schema_version) BETWEEN 1 AND 100",
            name="match_candidate_evidence_evidence_schema_version_check",
        ),
        CheckConstraint(
            "evidence_document_size_bytes BETWEEN 2 AND 131072",
            name="match_candidate_evidence_evidence_document_size_bytes_check",
        ),
        UniqueConstraint("decision_id", "rank", name="uq_match_candidate_evidence_rank"),
        UniqueConstraint(
            "decision_id", "recording_id", name="uq_match_candidate_evidence_recording"
        ),
        UniqueConstraint(
            "match_candidate_evidence_id",
            "decision_id",
            "recording_id",
            name="uq_match_candidate_evidence_review_ref",
        ),
        CheckConstraint(
            "(raw_score IS NULL OR raw_score BETWEEN 0 AND 1) AND (confidence IS NULL OR confidence BETWEEN 0 AND 1)",
            name="ck_match_candidate_evidence_scores",
        ),
        CheckConstraint(
            "evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')",
            name="ck_match_candidate_evidence_tier",
        ),
        CheckConstraint(
            "jsonb_typeof(feature_scores) = 'array' AND jsonb_typeof(hard_conflicts) = 'array' AND jsonb_typeof(candidate_origins) = 'array' AND jsonb_typeof(extractor_versions) = 'object' AND octet_length(convert_to(feature_scores::text, 'UTF8')) + octet_length(convert_to(hard_conflicts::text, 'UTF8')) + octet_length(convert_to(candidate_origins::text, 'UTF8')) + octet_length(convert_to(extractor_versions::text, 'UTF8')) <= 131072 AND jsonb_array_length(feature_scores) <= 256 AND jsonb_array_length(hard_conflicts) <= 64 AND jsonb_array_length(candidate_origins) <= 256",
            name="ck_match_candidate_evidence_json",
        ),
        CheckConstraint(
            "octet_length(evidence_sha256) = 32",
            name="ck_match_candidate_evidence_hash_len",
        ),
        {"schema": "identity"},
    )


Index(
    "ix_recording_identifier_lookup",
    RecordingIdentifierRow.scheme,
    RecordingIdentifierRow.value,
)

Index(
    "ix_external_reference_recording",
    ExternalReferenceRow.recording_id,
    postgresql_where=text("recording_id IS NOT NULL"),
)

Index(
    "ix_source_observation_reference_time",
    SourceObservationRow.external_reference_id,
    SourceObservationRow.observed_at.desc(),
)

Index(
    "ix_threshold_set_scope",
    ThresholdSetRow.evidence_mode,
    ThresholdSetRow.minimum_evidence_tier,
    ThresholdSetRow.created_at.desc(),
    ThresholdSetRow.threshold_set_version,
)

Index(
    "ix_match_policy_activation_threshold_time",
    MatchPolicyActivationRow.threshold_set_version,
    MatchPolicyActivationRow.created_at.desc(),
    MatchPolicyActivationRow.activation_id,
)

Index(
    "ix_match_decision_query_time",
    MatchDecisionRow.query_type,
    MatchDecisionRow.import_entry_id,
    MatchDecisionRow.user_track_ref_id,
    MatchDecisionRow.local_audio_id,
    MatchDecisionRow.external_reference_id,
    MatchDecisionRow.vault_object_id,
    MatchDecisionRow.audio_variant_id,
    MatchDecisionRow.owner_user_id,
    MatchDecisionRow.device_id,
    MatchDecisionRow.decided_at.desc(),
    MatchDecisionRow.decision_id,
)

Index(
    "ix_match_decision_candidate_time",
    MatchDecisionRow.candidate_recording_id,
    MatchDecisionRow.decided_at.desc(),
    MatchDecisionRow.decision_id,
    postgresql_where=text("candidate_recording_id IS NOT NULL"),
)

Index(
    "ix_match_decision_matcher_time",
    MatchDecisionRow.matcher_version,
    MatchDecisionRow.decided_at.desc(),
    MatchDecisionRow.decision_id,
)

Index(
    "ix_match_candidate_evidence_recording",
    MatchCandidateEvidenceRow.recording_id,
    MatchCandidateEvidenceRow.decision_id,
)


__all__ = (
    "CalibratorReleaseRow",
    "ExternalReferenceRow",
    "MatchCandidateEvidenceRow",
    "MatchDecisionRow",
    "MatchPolicyActivationRow",
    "MatcherReleaseRow",
    "RecordingIdentifierRow",
    "RecordingRedirectRow",
    "SourceObservationRow",
    "SourceProviderRow",
    "ThresholdSetRow",
)
