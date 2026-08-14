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
        CheckConstraint(
            "context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY')",
            name="ck_recommendation_request_context",
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
            "payload_encoding IN ('JSON_ZSTD', 'PROTOBUF_ZSTD')",
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
        {"schema": "ml"},
    )


Index(
    "uq_embedding_model_single_active_task",
    EmbeddingModelRow.task,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
)

Index(
    "ix_recording_embedding_model_recording",
    RecordingEmbeddingRow.embedding_model_id,
    RecordingEmbeddingRow.recording_id,
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
    "EmbeddingModelRow",
    "OfflineRecommendationPackRow",
    "RecommendationItemRow",
    "RecommendationRequestRow",
    "RecordingEmbeddingRow",
    "TasteClusterMemberRow",
    "TasteClusterRow",
)
