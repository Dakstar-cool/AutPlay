"""Metadata for the private dormant v2 P11 authority graph."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP, UUID

from ..base import Base


def _hash(
    table: str, name: str, *, constraint_name: str | None = None
) -> tuple[Column[bytes], CheckConstraint]:
    return (
        Column(name, BYTEA(), nullable=False),
        CheckConstraint(
            f"octet_length({name})=32", name=constraint_name or f"{table}_{name}_check"
        ),
    )


_replay_name = "recommendation_request_replay_payload"
_replay = Table(
    _replay_name,
    Base.metadata,
    Column(
        "recommendation_request_id",
        UUID(as_uuid=True),
        ForeignKey("ml.recommendation_request.recommendation_request_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("owner_user_id", UUID(as_uuid=True), nullable=False),
    Column("device_id", UUID(as_uuid=True), nullable=False),
    Column("journal_epoch", UUID(as_uuid=True), nullable=False),
    Column(
        "server_instance_id",
        UUID(as_uuid=True),
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("identity_epoch", BigInteger(), nullable=False),
    Column("effective_limit", Integer(), nullable=False),
    Column("endpoint_max", Integer(), nullable=False),
    Column("cutoff_at_ms", BigInteger(), nullable=False),
    Column("document", JSONB(), nullable=False),
    *_hash(_replay_name, "document_sha256"),
    *_hash(_replay_name, "request_sha256"),
    *_hash(
        _replay_name,
        "input_snapshot_sha256",
        constraint_name="recommendation_request_replay_paylo_input_snapshot_sha256_check",
    ),
    *_hash(
        _replay_name,
        "policy_snapshot_sha256",
        constraint_name="recommendation_request_replay_payl_policy_snapshot_sha256_check",
    ),
    Column("replay_expires_at", TIMESTAMP(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["owner_user_id", "recommendation_request_id"],
        [
            "ml.recommendation_request.user_id",
            "ml.recommendation_request.recommendation_request_id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["owner_user_id", "device_id"],
        ["account.device.user_id", "account.device.device_id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("identity_epoch>=1", name=f"{_replay_name}_identity_epoch_check"),
    CheckConstraint(
        "effective_limit BETWEEN 1 AND 100", name=f"{_replay_name}_effective_limit_check"
    ),
    CheckConstraint("endpoint_max=100", name=f"{_replay_name}_endpoint_max_check"),
    CheckConstraint("cutoff_at_ms>=0", name=f"{_replay_name}_cutoff_at_ms_check"),
    schema="ml",
)

_decision_name = "recommendation_serving_decision"
_decision = Table(
    _decision_name,
    Base.metadata,
    Column(
        "recommendation_request_id",
        UUID(as_uuid=True),
        ForeignKey("ml.recommendation_request.recommendation_request_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("state", Text(), nullable=False),
    Column("serving_provenance", Text(), nullable=False),
    *_hash(_decision_name, "p11_ranking_sha256"),
    Column("inference_evidence_sha256", BYTEA(), nullable=True),
    Column("fallback_reason", Text(), nullable=True),
    Column("p11_item_count", Integer(), nullable=False),
    *_hash(_decision_name, "p11_items_sha256"),
    Column("served_item_count", Integer(), nullable=False),
    *_hash(_decision_name, "served_items_sha256"),
    *_hash(_decision_name, "decision_sha256"),
    Column("served_truth_expires_at", TIMESTAMP(timezone=True), nullable=False),
    CheckConstraint("state='COMMITTED_P11'", name=f"{_decision_name}_state_check"),
    CheckConstraint("serving_provenance='P11'", name=f"{_decision_name}_serving_provenance_check"),
    CheckConstraint(
        "inference_evidence_sha256 IS NULL",
        name=f"{_decision_name}_inference_evidence_sha256_check",
    ),
    CheckConstraint("fallback_reason IS NULL", name=f"{_decision_name}_fallback_reason_check"),
    CheckConstraint(
        "p11_item_count BETWEEN 0 AND 100", name=f"{_decision_name}_p11_item_count_check"
    ),
    CheckConstraint(
        "served_item_count BETWEEN 0 AND 100", name=f"{_decision_name}_served_item_count_check"
    ),
    CheckConstraint(
        "p11_ranking_sha256=p11_items_sha256 AND p11_item_count=served_item_count",
        name=f"{_decision_name}_check",
    ),
    schema="ml",
)

_served_name = "recommendation_served_item"
_served = Table(
    _served_name,
    Base.metadata,
    Column(
        "recommendation_request_id",
        UUID(as_uuid=True),
        ForeignKey("ml.recommendation_request.recommendation_request_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("source_rank", Integer(), primary_key=True),
    Column(
        "recording_id",
        UUID(as_uuid=True),
        ForeignKey("catalog.recording.recording_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("score_units_e8", BigInteger(), nullable=False),
    Column("score_kind", Text(), nullable=False),
    Column("attribution_source", Text(), nullable=False),
    Column("reasons", JSONB(), nullable=False),
    *_hash(_served_name, "reasons_sha256"),
    Column("contributions", JSONB(), nullable=False),
    *_hash(_served_name, "contributions_sha256"),
    Column("original_p11_rank", Integer(), nullable=False),
    Column("origin", Text(), nullable=False),
    *_hash(_served_name, "served_item_sha256"),
    UniqueConstraint(
        "recommendation_request_id",
        "recording_id",
        name="recommendation_served_item_recommendation_request_id_record_key",
    ),
    UniqueConstraint(
        "recommendation_request_id",
        "source_rank",
        "recording_id",
        name="recommendation_served_item_recommendation_request_id_source_key",
    ),
    CheckConstraint("source_rank BETWEEN 1 AND 100", name=f"{_served_name}_source_rank_check"),
    CheckConstraint("score_kind='heuristic'", name=f"{_served_name}_score_kind_check"),
    CheckConstraint("attribution_source='P11'", name=f"{_served_name}_attribution_source_check"),
    CheckConstraint("origin='P11'", name=f"{_served_name}_origin_check"),
    CheckConstraint("original_p11_rank=source_rank", name=f"{_served_name}_check"),
    schema="ml",
)

_tombstone_name = "recommendation_attribution_expiry_tombstone"
_tombstone = Table(
    _tombstone_name,
    Base.metadata,
    Column(
        "owner_user_id",
        UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("recommendation_request_id", UUID(as_uuid=True), primary_key=True),
    Column("serving_contract_version", Integer(), nullable=False),
    *_hash(
        _tombstone_name,
        "request_sha256",
        constraint_name="recommendation_attribution_expiry_tombston_request_sha256_check",
    ),
    *_hash(
        _tombstone_name,
        "decision_sha256",
        constraint_name="recommendation_attribution_expiry_tombsto_decision_sha256_check",
    ),
    *_hash(
        _tombstone_name,
        "served_items_sha256",
        constraint_name="recommendation_attribution_expiry_tom_served_items_sha256_check",
    ),
    Column("expired_at", TIMESTAMP(timezone=True), nullable=False),
    Column("tombstone_expires_at", TIMESTAMP(timezone=True), nullable=False),
    Column("reason", Text(), nullable=False),
    CheckConstraint(
        "serving_contract_version=2",
        name="recommendation_attribution_expir_serving_contract_version_check",
    ),
    CheckConstraint("reason='EXPIRED'", name=f"{_tombstone_name}_reason_check"),
    schema="ml",
)


class RecommendationRequestReplayPayloadRow(Base):
    __table__ = _replay


class RecommendationServingDecisionRow(Base):
    __table__ = _decision


class RecommendationServedItemRow(Base):
    __table__ = _served


class RecommendationAttributionExpiryTombstoneRow(Base):
    __table__ = _tombstone


__all__ = (
    "RecommendationAttributionExpiryTombstoneRow",
    "RecommendationRequestReplayPayloadRow",
    "RecommendationServedItemRow",
    "RecommendationServingDecisionRow",
)
