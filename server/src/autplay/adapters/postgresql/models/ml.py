"""Typed SQLAlchemy mappings for the ml PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
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


class EmbeddingModelRow(Base):
    """Persistence row for ``ml.embedding_model``."""

    __tablename__ = "embedding_model"

    embedding_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    model_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    task: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text(), nullable=False)
    source_revision: Mapped[str] = mapped_column(Text(), nullable=False)
    artifact_filename: Mapped[str] = mapped_column(Text(), nullable=False)
    artifact_format: Mapped[str] = mapped_column(Text(), nullable=False)
    artifact_byte_size: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    artifact_manifest: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    manifest_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    weights_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    license_id: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    runtime: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    runtime_revision: Mapped[str] = mapped_column(Text(), nullable=False)
    inference_precision: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    input_sample_rate_hz: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    segment_duration_ms: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    preprocessing_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    preprocessing_manifest: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    preprocessing_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    license_review_reference: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pooling_strategy: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    dimension: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'BENCHMARK'"),
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
        PrimaryKeyConstraint("embedding_model_id", name="embedding_model_pkey"),
        CheckConstraint(
            "length(model_key) BETWEEN 1 AND 300",
            name="embedding_model_model_key_check",
        ),
        CheckConstraint(
            "length(version) BETWEEN 1 AND 200",
            name="embedding_model_version_check",
        ),
        CheckConstraint(
            "length(task) BETWEEN 1 AND 100",
            name="embedding_model_task_check",
        ),
        CheckConstraint("length(source) BETWEEN 1 AND 500", name="ck_embedding_model_source"),
        CheckConstraint(
            "length(source_revision) BETWEEN 1 AND 300",
            name="ck_embedding_model_source_revision",
        ),
        CheckConstraint(
            "length(artifact_filename) BETWEEN 1 AND 300",
            name="ck_embedding_model_artifact_filename",
        ),
        CheckConstraint(
            "length(artifact_format) BETWEEN 1 AND 100",
            name="ck_embedding_model_artifact_format",
        ),
        CheckConstraint("artifact_byte_size > 0", name="ck_embedding_model_artifact_byte_size"),
        CheckConstraint(
            "length(license_id) BETWEEN 1 AND 200",
            name="embedding_model_license_id_check",
        ),
        CheckConstraint(
            "length(runtime) BETWEEN 1 AND 200",
            name="embedding_model_runtime_check",
        ),
        CheckConstraint(
            "length(inference_precision) BETWEEN 1 AND 50",
            name="embedding_model_inference_precision_check",
        ),
        CheckConstraint(
            "input_sample_rate_hz > 0",
            name="embedding_model_input_sample_rate_hz_check",
        ),
        CheckConstraint(
            "segment_duration_ms > 0",
            name="embedding_model_segment_duration_ms_check",
        ),
        CheckConstraint(
            "length(preprocessing_version) BETWEEN 1 AND 200",
            name="embedding_model_preprocessing_version_check",
        ),
        CheckConstraint(
            "length(pooling_strategy) BETWEEN 1 AND 200",
            name="embedding_model_pooling_strategy_check",
        ),
        CheckConstraint(
            "dimension BETWEEN 1 AND 16000",
            name="embedding_model_dimension_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="embedding_model_row_version_check",
        ),
        CheckConstraint(
            "octet_length(weights_sha256) = 32",
            name="ck_embedding_model_weights_hash_len",
        ),
        CheckConstraint(
            "octet_length(manifest_sha256) = 32",
            name="ck_embedding_model_manifest_hash_len",
        ),
        CheckConstraint(
            "octet_length(preprocessing_sha256) = 32",
            name="ck_embedding_model_preprocessing_hash_len",
        ),
        CheckConstraint(
            "length(runtime_revision) BETWEEN 1 AND 200",
            name="ck_embedding_model_runtime_revision",
        ),
        CheckConstraint(
            "license_review_reference IS NULL OR "
            "length(license_review_reference) BETWEEN 1 AND 500",
            name="ck_embedding_model_license_review_reference",
        ),
        CheckConstraint(
            "status = 'BLOCKED' OR license_review_reference IS NOT NULL",
            name="ck_embedding_model_review_required",
        ),
        CheckConstraint(
            "status IN ('BENCHMARK', 'ACTIVE', 'RETIRED', 'BLOCKED')",
            name="ck_embedding_model_status",
        ),
        UniqueConstraint(
            "model_key",
            "version",
            "preprocessing_version",
            "pooling_strategy",
            name="uq_embedding_model_version",
        ),
        {"schema": "ml"},
    )


class RecordingEmbeddingRow(Base):
    """Persistence row for ``ml.recording_embedding``."""

    __tablename__ = "recording_embedding"

    recording_embedding_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    embedding_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    audio_variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(),
        nullable=False,
    )
    normalized: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("true"),
    )
    quality_flags: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    preprocessing_input_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    vector_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    producing_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    producing_attempt_no: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("recording_embedding_id", name="recording_embedding_pkey"),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_embedding_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["producing_job_id"],
            ["jobs.job.job_id"],
            name="recording_embedding_producing_job_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "preprocessing_input_sha256 IS NULL OR octet_length(preprocessing_input_sha256) = 32",
            name="ck_recording_embedding_input_hash",
        ),
        CheckConstraint(
            "vector_sha256 IS NULL OR octet_length(vector_sha256) = 32",
            name="ck_recording_embedding_vector_hash",
        ),
        CheckConstraint(
            "producing_attempt_no IS NULL OR producing_attempt_no >= 1",
            name="ck_recording_embedding_attempt",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="recording_embedding_embedding_model_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="recording_embedding_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "recording_id",
            "embedding_model_id",
            "audio_variant_id",
            name="uq_recording_embedding_source",
        ),
        {"schema": "ml"},
    )


class EmbeddingModelActivationRow(Base):
    """Append-only activation and rollback evidence for one embedding task."""

    __tablename__ = "embedding_model_activation"

    embedding_model_activation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    task: Mapped[str] = mapped_column(Text(), nullable=False)
    activation_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    target_embedding_model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    previous_embedding_model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    benchmark_report_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    rollback_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "embedding_model_activation_id", name="embedding_model_activation_pkey"
        ),
        ForeignKeyConstraint(
            ["target_embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="embedding_model_activation_target_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["previous_embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="embedding_model_activation_previous_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["benchmark_report_sha256"],
            ["ml.embedding_benchmark_report.report_sha256"],
            name="embedding_model_activation_benchmark_report_sha256_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["account.user_account.user_id"],
            name="embedding_model_activation_actor_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task", "activation_sequence", name="uq_embedding_activation_sequence"),
        CheckConstraint(
            "length(task) BETWEEN 1 AND 100",
            name="embedding_model_activation_task_check",
        ),
        CheckConstraint(
            "activation_sequence >= 1",
            name="embedding_model_activation_activation_sequence_check",
        ),
        CheckConstraint(
            "action IN ('ACTIVATE', 'ROLLBACK', 'DEACTIVATE')",
            name="embedding_model_activation_action_check",
        ),
        CheckConstraint(
            "octet_length(benchmark_report_sha256) = 32",
            name="ck_embedding_activation_benchmark_hash",
        ),
        {"schema": "ml"},
    )


class EmbeddingBenchmarkReportRow(Base):
    """Immutable model/shadow benchmark evidence and activation gate."""

    __tablename__ = "embedding_benchmark_report"

    report_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    embedding_model_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[str] = mapped_column(Text(), nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text(), nullable=False)
    dataset_snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    interaction_schema_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    interaction_watermark: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    decision: Mapped[str] = mapped_column(Text(), nullable=False)
    report_document: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("report_sha256", name="embedding_benchmark_report_pkey"),
        ForeignKeyConstraint(
            ["embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="embedding_benchmark_report_model_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(report_sha256) = 32", name="ck_embedding_benchmark_report_hash"
        ),
        CheckConstraint(
            "octet_length(dataset_snapshot_sha256) = 32",
            name="ck_embedding_benchmark_dataset_hash",
        ),
        CheckConstraint(
            "interaction_schema_version >= 1 AND interaction_watermark >= 0",
            name="ck_embedding_benchmark_interaction_identity",
        ),
        CheckConstraint(
            "decision IN ('EXPERIMENTAL', 'APPROVED', 'REJECTED', 'UNAVAILABLE')",
            name="ck_embedding_benchmark_decision",
        ),
        {"schema": "ml"},
    )


class EnrichmentJobRow(Base):
    """Typed enrichment target whose generic job payload carries only this ID."""

    __tablename__ = "enrichment_job"

    enrichment_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    job_kind: Mapped[str] = mapped_column(Text(), nullable=False)
    recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    audio_variant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    embedding_model_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    expected_weights_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    expected_preprocessing_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("enrichment_job_id", name="enrichment_job_pkey"),
        UniqueConstraint("job_id", name="enrichment_job_job_id_key"),
        ForeignKeyConstraint(
            ["job_id"], ["jobs.job.job_id"], name="enrichment_job_job_id_fkey", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="enrichment_job_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="enrichment_job_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="enrichment_job_model_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "job_kind = 'AUDIO_EMBEDDING'",
            name="enrichment_job_job_kind_check",
        ),
        CheckConstraint(
            "octet_length(expected_weights_sha256) = 32",
            name="ck_enrichment_job_weights_hash",
        ),
        CheckConstraint(
            "octet_length(expected_preprocessing_sha256) = 32",
            name="ck_enrichment_job_preprocessing_hash",
        ),
        {"schema": "ml"},
    )


class RecordingTagSetRow(Base):
    """Immutable bounded tag result kept separate from embedding quality flags."""

    __tablename__ = "recording_tag_set"

    recording_tag_set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    embedding_model_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    audio_variant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    output_schema_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    tag_document: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    result_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    preprocessing_input_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    producing_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    producing_attempt_no: Mapped[int] = mapped_column(Integer(), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("recording_tag_set_id", name="recording_tag_set_pkey"),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="recording_tag_set_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id"],
            ["ml.embedding_model.embedding_model_id"],
            name="recording_tag_set_model_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["audio_variant_id"],
            ["vault.audio_variant.audio_variant_id"],
            name="recording_tag_set_audio_variant_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["producing_job_id"],
            ["jobs.job.job_id"],
            name="recording_tag_set_producing_job_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "recording_id",
            "embedding_model_id",
            "audio_variant_id",
            "output_schema_version",
            name="uq_recording_tag_set_source",
        ),
        CheckConstraint(
            "output_schema_version >= 1",
            name="recording_tag_set_output_schema_version_check",
        ),
        CheckConstraint(
            "octet_length(result_sha256) = 32 AND octet_length(preprocessing_input_sha256) = 32",
            name="ck_recording_tag_set_hashes",
        ),
        CheckConstraint(
            "producing_attempt_no >= 1",
            name="recording_tag_set_producing_attempt_no_check",
        ),
        {"schema": "ml"},
    )


class RecommendationPipelineVersionRow(Base):
    """Immutable manifest identity for one recommendation pipeline version."""

    __tablename__ = "recommendation_pipeline_version"

    pipeline_key: Mapped[str] = mapped_column(Text(), nullable=False)
    version: Mapped[str] = mapped_column(Text(), nullable=False)
    implementation_revision: Mapped[str] = mapped_column(Text(), nullable=False)
    request_schema_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    canonicalization_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    manifest: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    manifest_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "pipeline_key", "version", name="recommendation_pipeline_version_pkey"
        ),
        CheckConstraint(
            "request_schema_version >= 1",
            name="recommendation_pipeline_version_request_schema_version_check",
        ),
        CheckConstraint(
            "canonicalization_version >= 1",
            name="recommendation_pipeline_version_canonicalization_version_check",
        ),
        CheckConstraint(
            "octet_length(manifest_sha256) = 32",
            name="recommendation_pipeline_version_manifest_sha256_check",
        ),
        CheckConstraint(
            "length(pipeline_key) BETWEEN 1 AND 100", name="ck_recommendation_pipeline_key"
        ),
        CheckConstraint(
            "length(version) BETWEEN 1 AND 100", name="ck_recommendation_pipeline_version"
        ),
        CheckConstraint(
            "length(implementation_revision) BETWEEN 1 AND 200",
            name="ck_recommendation_pipeline_revision",
        ),
        CheckConstraint(
            "lifecycle_status IN ('ACTIVE', 'SHADOW', 'RETIRED')",
            name="ck_recommendation_pipeline_status",
        ),
        {"schema": "ml"},
    )


class RecommendationInputSnapshotRow(Base):
    """Retained immutable replay input for one owner."""

    __tablename__ = "recommendation_input_snapshot"

    recommendation_input_snapshot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    input_snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    interaction_watermark: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    catalog_snapshot: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    availability_snapshot: Mapped[str] = mapped_column(Text(), nullable=False)
    policy_snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    snapshot_document: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    retained_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "recommendation_input_snapshot_id", name="recommendation_input_snapshot_pkey"
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="recommendation_input_snapshot_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(input_snapshot_sha256) = 32",
            name="recommendation_input_snapshot_input_snapshot_sha256_check",
        ),
        CheckConstraint(
            "interaction_watermark >= 0",
            name="recommendation_input_snapshot_interaction_watermark_check",
        ),
        CheckConstraint(
            "catalog_snapshot >= 0",
            name="recommendation_input_snapshot_catalog_snapshot_check",
        ),
        CheckConstraint(
            "octet_length(policy_snapshot_sha256) = 32",
            name="recommendation_input_snapshot_policy_snapshot_sha256_check",
        ),
        UniqueConstraint(
            "user_id",
            "recommendation_input_snapshot_id",
            name="uq_recommendation_input_snapshot_owner",
        ),
        CheckConstraint(
            "retained_until > created_at", name="ck_recommendation_input_snapshot_retention"
        ),
        {"schema": "ml"},
    )


class RecommendationRequestRow(Base):
    """Persistence row for ``ml.recommendation_request``."""

    __tablename__ = "recommendation_request"

    recommendation_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    context: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'GENERAL'"),
    )
    surface: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pipeline_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pipeline_manifest_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    request_schema_version: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    request_canonicalization_version: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    request_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    recommendation_input_snapshot_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    input_snapshot_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    interaction_watermark: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    catalog_snapshot: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    availability_snapshot_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    policy_snapshot_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    request_document: Mapped[JsonValue | None] = mapped_column(JSONB(), nullable=True)
    shadow: Mapped[bool] = mapped_column(Boolean(), nullable=False, server_default=text("false"))
    model_bundle_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    candidate_policy_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    filter_policy_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    reranker_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    seed: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    request_features: Mapped[JsonValue] = mapped_column(
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
        PrimaryKeyConstraint("recommendation_request_id", name="recommendation_request_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="recommendation_request_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["pipeline_key", "pipeline_version"],
            [
                "ml.recommendation_pipeline_version.pipeline_key",
                "ml.recommendation_pipeline_version.version",
            ],
            name="fk_recommendation_request_pipeline",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "recommendation_input_snapshot_id"],
            [
                "ml.recommendation_input_snapshot.user_id",
                "ml.recommendation_input_snapshot.recommendation_input_snapshot_id",
            ],
            name="fk_recommendation_request_input_owner",
            ondelete="SET NULL (recommendation_input_snapshot_id)",
        ),
        UniqueConstraint(
            "user_id",
            "recommendation_request_id",
            name="uq_recommendation_request_owner",
        ),
        CheckConstraint(
            "context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY')",
            name="ck_recommendation_request_context",
        ),
        CheckConstraint(
            "surface IS NULL OR surface IN ('recommendations', 'home', 'offline_pack')",
            name="ck_recommendation_request_surface",
        ),
        CheckConstraint(
            "(pipeline_manifest_sha256 IS NULL OR octet_length(pipeline_manifest_sha256) = 32) "
            "AND (request_sha256 IS NULL OR octet_length(request_sha256) = 32) "
            "AND (input_snapshot_sha256 IS NULL OR octet_length(input_snapshot_sha256) = 32) "
            "AND (policy_snapshot_sha256 IS NULL OR octet_length(policy_snapshot_sha256) = 32)",
            name="ck_recommendation_request_replay_hashes",
        ),
        CheckConstraint(
            "(request_schema_version IS NULL OR request_schema_version >= 1) "
            "AND (request_canonicalization_version IS NULL "
            "OR request_canonicalization_version >= 1) "
            "AND (interaction_watermark IS NULL OR interaction_watermark >= 0) "
            "AND (catalog_snapshot IS NULL OR catalog_snapshot >= 0)",
            name="ck_recommendation_request_replay_versions",
        ),
        {"schema": "ml"},
    )


class RecommendationItemRow(Base):
    """Persistence row for ``ml.recommendation_item``."""

    __tablename__ = "recommendation_item"

    recommendation_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )
    candidate_sources: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("ARRAY[]::text[]"),
    )
    explanation_code: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    availability_snapshot: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    contributions: Mapped[JsonValue] = mapped_column(
        JSONB(), nullable=False, server_default=text("'[]'::jsonb")
    )
    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("ARRAY[]::text[]")
    )
    item_provenance: Mapped[JsonValue] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["recommendation_request_id"],
            ["ml.recommendation_request.recommendation_request_id"],
            name="recommendation_item_recommendation_request_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "rank >= 1",
            name="recommendation_item_rank_check",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="recommendation_item_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("recommendation_request_id", "rank", name="recommendation_item_pkey"),
        UniqueConstraint(
            "recommendation_request_id", "recording_id", name="uq_recommendation_item_recording"
        ),
        {"schema": "ml"},
    )


class TasteClusterRow(Base):
    """Persistence row for ``ml.taste_cluster``."""

    __tablename__ = "taste_cluster"

    taste_cluster_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    context: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'GENERAL'"),
    )
    model_bundle_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    centroid: Mapped[list[float]] = mapped_column(
        VECTOR(),
        nullable=False,
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("taste_cluster_id", name="taste_cluster_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="taste_cluster_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "weight >= 0",
            name="taste_cluster_weight_check",
        ),
        CheckConstraint(
            "context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY')",
            name="ck_taste_cluster_context",
        ),
        {"schema": "ml"},
    )


class TasteClusterMemberRow(Base):
    """Persistence row for ``ml.taste_cluster_member``."""

    __tablename__ = "taste_cluster_member"

    taste_cluster_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    membership_score: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )
    explicit_weight: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
        server_default=text("1"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["taste_cluster_id"],
            ["ml.taste_cluster.taste_cluster_id"],
            name="taste_cluster_member_taste_cluster_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="taste_cluster_member_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "membership_score BETWEEN 0 AND 1",
            name="taste_cluster_member_membership_score_check",
        ),
        CheckConstraint(
            "explicit_weight >= 0",
            name="taste_cluster_member_explicit_weight_check",
        ),
        PrimaryKeyConstraint(
            "taste_cluster_id", "user_track_ref_id", name="taste_cluster_member_pkey"
        ),
        {"schema": "ml"},
    )


class OfflineRecommendationPackRow(Base):
    """Persistence row for ``ml.offline_recommendation_pack``."""

    __tablename__ = "offline_recommendation_pack"

    offline_pack_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    recommendation_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    pipeline_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(Text(), nullable=True)
    input_snapshot_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    catalog_snapshot: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    model_bundle_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    payload_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    payload_encoding: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    payload: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    payload_sha256: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("offline_pack_id", name="offline_recommendation_pack_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="offline_recommendation_pack_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "recommendation_request_id"],
            [
                "ml.recommendation_request.user_id",
                "ml.recommendation_request.recommendation_request_id",
            ],
            name="fk_offline_pack_request_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["pipeline_key", "pipeline_version"],
            [
                "ml.recommendation_pipeline_version.pipeline_key",
                "ml.recommendation_pipeline_version.version",
            ],
            name="fk_offline_pack_pipeline",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="offline_recommendation_pack_device_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "catalog_snapshot >= 0",
            name="offline_recommendation_pack_catalog_snapshot_check",
        ),
        CheckConstraint(
            "payload_version >= 1",
            name="offline_recommendation_pack_payload_version_check",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_offline_pack_device_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "payload_encoding IN ('RAW_JSON', 'JSON_ZSTD', 'PROTOBUF_ZSTD')",
            name="ck_offline_pack_encoding",
        ),
        CheckConstraint(
            "octet_length(payload_sha256) = 32",
            name="ck_offline_pack_hash_len",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_offline_pack_expiry",
        ),
        CheckConstraint(
            "input_snapshot_sha256 IS NULL OR octet_length(input_snapshot_sha256) = 32",
            name="ck_offline_pack_snapshot_hash",
        ),
        {"schema": "ml"},
    )


Index(
    "uq_embedding_model_single_active_task",
    EmbeddingModelRow.task,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
)

Index(
    "ix_recommendation_snapshot_user_retention",
    RecommendationInputSnapshotRow.user_id,
    RecommendationInputSnapshotRow.retained_until.desc(),
)

Index(
    "ix_recording_embedding_model_recording",
    RecordingEmbeddingRow.embedding_model_id,
    RecordingEmbeddingRow.recording_id,
)

Index(
    "ix_embedding_activation_task_time",
    EmbeddingModelActivationRow.task,
    EmbeddingModelActivationRow.activation_sequence.desc(),
)

Index(
    "ix_embedding_benchmark_model_time",
    EmbeddingBenchmarkReportRow.embedding_model_id,
    EmbeddingBenchmarkReportRow.created_at.desc(),
)

Index(
    "ix_enrichment_job_model_recording",
    EnrichmentJobRow.embedding_model_id,
    EnrichmentJobRow.recording_id,
)

Index(
    "ix_recording_tag_set_model_recording",
    RecordingTagSetRow.embedding_model_id,
    RecordingTagSetRow.recording_id,
)

Index(
    "ix_recommendation_request_user_time",
    RecommendationRequestRow.user_id,
    RecommendationRequestRow.created_at.desc(),
)

Index(
    "ix_recommendation_item_recording",
    RecommendationItemRow.recording_id,
    RecommendationItemRow.recommendation_request_id,
)

Index(
    "ix_taste_cluster_user_active",
    TasteClusterRow.user_id,
    TasteClusterRow.context,
    TasteClusterRow.model_bundle_version,
    postgresql_where=text("retired_at IS NULL"),
)

Index(
    "ix_offline_pack_user_device",
    OfflineRecommendationPackRow.user_id,
    OfflineRecommendationPackRow.device_id,
    OfflineRecommendationPackRow.expires_at.desc(),
)


__all__ = (
    "EmbeddingBenchmarkReportRow",
    "EmbeddingModelActivationRow",
    "EmbeddingModelRow",
    "EnrichmentJobRow",
    "OfflineRecommendationPackRow",
    "RecommendationInputSnapshotRow",
    "RecommendationItemRow",
    "RecommendationPipelineVersionRow",
    "RecommendationRequestRow",
    "RecordingEmbeddingRow",
    "RecordingTagSetRow",
    "TasteClusterMemberRow",
    "TasteClusterRow",
)
