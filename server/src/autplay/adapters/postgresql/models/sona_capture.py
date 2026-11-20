"""Typed mappings for non-activating Sona native capture authority."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)


def _hash(name: str) -> Column[bytes]:
    return Column(name, BYTEA(), nullable=False)


_bundle = SATable(
    "sona_capture_bundle",
    Base.metadata,
    Column("recommendation_request_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    _hash("baseline_snapshot_sha256"),
    _hash("temporal_snapshot_sha256"),
    _hash("candidate_membership_sha256"),
    _hash("p11_ranking_sha256"),
    _hash("bundle_sha256"),
    _hash("consent_receipt_sha256"),
    Column("consent_generation", BigInteger(), nullable=False),
    Column("cutoff_at_ms", BigInteger(), nullable=False),
    Column("interaction_watermark", BigInteger(), nullable=False),
    Column("universe_count", Integer(), nullable=False),
    Column("eligible_count", Integer(), nullable=False),
    Column("ineligibility_reason", Text()),
    Column("bundle_document", BYTEA(), nullable=False),
    Column("created_at", _TIME, nullable=False),
    Column("expires_at", _TIME, nullable=False),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        [
            "ml.recommendation_request.user_id",
            "ml.recommendation_request.recommendation_request_id",
        ],
        name="fk_sona_capture_request_owner",
        ondelete="CASCADE",
    ),
    UniqueConstraint("user_id", "recommendation_request_id", name="uq_sona_capture_owner"),
    CheckConstraint("eligible_count<=universe_count", name="ck_sona_capture_counts"),
    CheckConstraint("bundle_sha256=sha256(bundle_document)", name="ck_sona_capture_document_hash"),
    CheckConstraint(
        "(universe_count<=1024 AND ineligibility_reason IS NULL) OR "
        "(universe_count>1024 AND ineligibility_reason='UNIVERSE_OVER_CAP')",
        name="ck_sona_capture_eligibility",
    ),
    CheckConstraint("expires_at=created_at+interval '180 days'", name="ck_sona_capture_expiry"),
    Index("ix_sona_capture_owner_expiry", "user_id", "expires_at"),
    schema="ml",
)


class SonaCaptureBundleRow(Base):
    __table__ = _bundle
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    baseline_snapshot_sha256: Mapped[bytes]
    temporal_snapshot_sha256: Mapped[bytes]
    candidate_membership_sha256: Mapped[bytes]
    p11_ranking_sha256: Mapped[bytes]
    bundle_sha256: Mapped[bytes]
    consent_receipt_sha256: Mapped[bytes]
    consent_generation: Mapped[int]
    cutoff_at_ms: Mapped[int]
    interaction_watermark: Mapped[int]
    universe_count: Mapped[int]
    eligible_count: Mapped[int]
    ineligibility_reason: Mapped[str | None]
    bundle_document: Mapped[bytes]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


_source_event = SATable(
    "sona_capture_source_event",
    Base.metadata,
    Column("recommendation_request_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    Column("source_event_id", _UUID, primary_key=True),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_source_bundle_owner",
        ondelete="CASCADE",
    ),
    Index("ix_sona_source_owner_event", "user_id", "source_event_id", "recommendation_request_id"),
    schema="ml",
)


class SonaCaptureSourceEventRow(Base):
    __table__ = _source_event
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    source_event_id: Mapped[UUID]


_erasure_receipt = SATable(
    "sona_event_erasure_receipt",
    Base.metadata,
    Column(
        "user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("source_event_id", _UUID, primary_key=True),
    Column("erasure_event_id", _UUID, nullable=False, unique=True),
    Column("source_server_sequence", BigInteger(), nullable=False),
    Column("source_watermark", BigInteger(), nullable=False),
    Column("ledger_sequence", BigInteger(), nullable=False),
    _hash("ledger_digest"),
    Column("removed_request_count", Integer(), nullable=False),
    _hash("removed_request_sha256"),
    _hash("receipt_sha256"),
    Column("erased_at", _TIME, nullable=False),
    CheckConstraint(
        "source_server_sequence>0", name="sona_event_erasure_receipt_source_server_sequence_check"
    ),
    CheckConstraint(
        "source_watermark>=source_server_sequence",
        name="sona_event_erasure_receipt_check",
    ),
    CheckConstraint("ledger_sequence>0", name="sona_event_erasure_receipt_ledger_sequence_check"),
    CheckConstraint(
        "removed_request_count>=0", name="sona_event_erasure_receipt_removed_request_count_check"
    ),
    Index("ix_sona_erasure_owner_watermark", "user_id", "source_watermark"),
    schema="ml",
)


class SonaEventErasureReceiptRow(Base):
    __table__ = _erasure_receipt
    user_id: Mapped[UUID]
    source_event_id: Mapped[UUID]
    erasure_event_id: Mapped[UUID]
    source_server_sequence: Mapped[int]
    source_watermark: Mapped[int]
    ledger_sequence: Mapped[int]
    ledger_digest: Mapped[bytes]
    removed_request_count: Mapped[int]
    removed_request_sha256: Mapped[bytes]
    receipt_sha256: Mapped[bytes]
    erased_at: Mapped[datetime]


_replay = SATable(
    "sona_capture_replay_payload",
    Base.metadata,
    Column("recommendation_request_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    _hash("request_sha256"),
    _hash("p11_ranking_sha256"),
    Column("request_document", BYTEA(), nullable=False),
    Column("ranking_document", BYTEA(), nullable=False),
    Column("item_count", Integer(), nullable=False),
    Column("created_at", _TIME, nullable=False),
    Column("expires_at", _TIME, nullable=False),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_replay_bundle_owner",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "octet_length(request_document) BETWEEN 1 AND 1048576",
        name="sona_capture_replay_payload_request_document_check",
    ),
    CheckConstraint(
        "octet_length(ranking_document) BETWEEN 1 AND 16777216",
        name="sona_capture_replay_payload_ranking_document_check",
    ),
    CheckConstraint(
        "item_count BETWEEN 0 AND 5000",
        name="sona_capture_replay_payload_item_count_check",
    ),
    CheckConstraint("request_sha256=sha256(request_document)", name="ck_sona_replay_request_hash"),
    CheckConstraint(
        "p11_ranking_sha256=sha256(ranking_document)", name="ck_sona_replay_ranking_hash"
    ),
    CheckConstraint("expires_at=created_at+interval '180 days'", name="ck_sona_replay_expiry"),
    Index("ix_sona_replay_owner_expiry", "user_id", "expires_at"),
    schema="ml",
)


class SonaCaptureReplayPayloadRow(Base):
    __table__ = _replay
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    request_sha256: Mapped[bytes]
    p11_ranking_sha256: Mapped[bytes]
    request_document: Mapped[bytes]
    ranking_document: Mapped[bytes]
    item_count: Mapped[int]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


_outcome = SATable(
    "sona_capture_outcome",
    Base.metadata,
    Column("recommendation_request_id", _UUID, primary_key=True),
    Column("outcome_event_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    Column("impression_event_id", _UUID, nullable=False),
    Column("recording_id", _UUID, nullable=False),
    Column("source_rank", Integer(), nullable=False),
    Column("impression_at", _TIME, nullable=False),
    Column("outcome_at", _TIME, nullable=False),
    _hash("impression_payload_sha256"),
    _hash("outcome_payload_sha256"),
    Column("expires_at", _TIME, nullable=False),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_outcome_bundle_owner",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["outcome_event_id"],
        ["library.listening_event.listening_event_id"],
        name="fk_sona_outcome_listen",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["impression_event_id"],
        ["library.user_interaction_event.interaction_id"],
        name="fk_sona_outcome_impression",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["outcome_event_id"],
        ["sync.sync_event.event_id"],
        name="fk_sona_outcome_sync_listen",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["impression_event_id"],
        ["sync.sync_event.event_id"],
        name="fk_sona_outcome_sync_impression",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["outcome_event_id"],
        ["sync.device_event_inbox.event_id"],
        name="fk_sona_outcome_inbox_listen",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["impression_event_id"],
        ["sync.device_event_inbox.event_id"],
        name="fk_sona_outcome_inbox_impression",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "source_rank BETWEEN 1 AND 5000", name="sona_capture_outcome_source_rank_check"
    ),
    CheckConstraint("outcome_at>impression_at", name="ck_sona_outcome_order"),
    Index("ix_sona_outcome_owner_expiry", "user_id", "expires_at", "recommendation_request_id"),
    Index("ix_sona_outcome_listen", "outcome_event_id"),
    Index("ix_sona_outcome_impression", "impression_event_id"),
    schema="ml",
)


class SonaCaptureOutcomeRow(Base):
    __table__ = _outcome
    recommendation_request_id: Mapped[UUID]
    outcome_event_id: Mapped[UUID]
    user_id: Mapped[UUID]
    impression_event_id: Mapped[UUID]
    recording_id: Mapped[UUID]
    source_rank: Mapped[int]
    impression_at: Mapped[datetime]
    outcome_at: Mapped[datetime]
    impression_payload_sha256: Mapped[bytes]
    outcome_payload_sha256: Mapped[bytes]
    expires_at: Mapped[datetime]


_registry_target = SATable(
    "sona_capture_registry_target",
    Base.metadata,
    Column("registry_generation", BigInteger(), primary_key=True),
    Column("run_id", _UUID, ForeignKey("ml.training_run.run_id"), nullable=False),
    Column("server_instance_id", _UUID, nullable=False),
    Column("identity_epoch", BigInteger(), nullable=False),
    _hash("input_binding_sha256"),
    _hash("publication_sha256"),
    _hash("model_manifest_sha256"),
    _hash("tokenizer_sha256"),
    _hash("pipeline_manifest_sha256"),
    _hash("execution_profile_sha256"),
    Column("execution_profile_document", BYTEA(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    UniqueConstraint(
        "model_manifest_sha256",
        "tokenizer_sha256",
        "pipeline_manifest_sha256",
        "execution_profile_sha256",
        name="uq_sona_registry_target_exact",
    ),
    CheckConstraint(
        "registry_generation BETWEEN 1 AND 128",
        name="sona_capture_registry_target_registry_generation_check",
    ),
    CheckConstraint("identity_epoch>=1", name="sona_capture_registry_target_identity_epoch_check"),
    CheckConstraint(
        "octet_length(execution_profile_document) BETWEEN 1 AND 65536",
        name="sona_capture_registry_target_execution_profile_document_check",
    ),
    CheckConstraint(
        "execution_profile_sha256=sha256("
        "convert_to('autplay.sona.execution-profile.v1','UTF8') || decode('00','hex') || "
        "execution_profile_document)",
        name="ck_sona_registry_profile_hash",
    ),
    schema="ml",
)


class SonaCaptureRegistryTargetRow(Base):
    __table__ = _registry_target
    registry_generation: Mapped[int]
    run_id: Mapped[UUID]
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int]
    input_binding_sha256: Mapped[bytes]
    publication_sha256: Mapped[bytes]
    model_manifest_sha256: Mapped[bytes]
    tokenizer_sha256: Mapped[bytes]
    pipeline_manifest_sha256: Mapped[bytes]
    execution_profile_sha256: Mapped[bytes]
    execution_profile_document: Mapped[bytes]


_registry_block = SATable(
    "sona_capture_registry_block",
    Base.metadata,
    Column(
        "registry_generation",
        BigInteger(),
        ForeignKey("ml.sona_capture_registry_target.registry_generation"),
        primary_key=True,
    ),
    Column("blocked_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    schema="ml",
)


class SonaCaptureRegistryBlockRow(Base):
    __table__ = _registry_block
    registry_generation: Mapped[int]


_vetting_head = SATable(
    "sona_target_vetting_head",
    Base.metadata,
    Column(
        "registry_generation",
        BigInteger(),
        ForeignKey("ml.sona_capture_registry_target.registry_generation"),
        primary_key=True,
    ),
    Column("generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("vetting_revoked", Boolean(), nullable=False, server_default=text("false")),
    Column("revoked_at", _TIME),
    CheckConstraint(
        "(generation=0 AND NOT vetting_revoked AND revoked_at IS NULL) OR "
        "(generation=1 AND vetting_revoked AND revoked_at IS NOT NULL)",
        name="ck_sona_vetting_head_state",
    ),
    schema="ml",
)


class SonaTargetVettingHeadRow(Base):
    __table__ = _vetting_head
    registry_generation: Mapped[int]
    generation: Mapped[int]
    vetting_revoked: Mapped[bool]
    revoked_at: Mapped[datetime | None]


_cursor = SATable(
    "sona_capture_lineage_cursor",
    Base.metadata,
    Column("recommendation_request_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'ACTIVE'")),
    Column("last_seen_registry_generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("expires_at", _TIME, nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_cursor_bundle_owner",
        ondelete="CASCADE",
    ),
    Index(
        "ix_sona_cursor_active_owner_expiry",
        "user_id",
        "expires_at",
        "recommendation_request_id",
        postgresql_where=text("state='ACTIVE'"),
    ),
    Index(
        "ix_sona_cursor_active_scan",
        "updated_at",
        "recommendation_request_id",
        postgresql_include=("user_id", "last_seen_registry_generation"),
        postgresql_where=text("state='ACTIVE'"),
    ),
    schema="ml",
)


class SonaCaptureLineageCursorRow(Base):
    __table__ = _cursor
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    state: Mapped[str]
    state_generation: Mapped[int]
    last_seen_registry_generation: Mapped[int]
    expires_at: Mapped[datetime]
    updated_at: Mapped[datetime]


_dispatch = SATable(
    "sona_capture_target_dispatch",
    Base.metadata,
    Column("target_dispatch_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("recommendation_request_id", _UUID, nullable=False),
    Column("user_id", _UUID, nullable=False),
    _hash("model_manifest_sha256"),
    _hash("tokenizer_sha256"),
    _hash("pipeline_manifest_sha256"),
    _hash("execution_profile_sha256"),
    Column("registry_generation", BigInteger(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'WAITING'")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("reason_code", Text()),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_dispatch_bundle_owner",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "recommendation_request_id",
        "model_manifest_sha256",
        "tokenizer_sha256",
        "pipeline_manifest_sha256",
        "execution_profile_sha256",
        name="uq_sona_dispatch_exact",
    ),
    Index("ix_sona_dispatch_owner_state", "user_id", "state", "created_at"),
    schema="ml",
)


class SonaCaptureTargetDispatchRow(Base):
    __table__ = _dispatch
    target_dispatch_id: Mapped[UUID]
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    model_manifest_sha256: Mapped[bytes]
    tokenizer_sha256: Mapped[bytes]
    pipeline_manifest_sha256: Mapped[bytes]
    execution_profile_sha256: Mapped[bytes]
    registry_generation: Mapped[int]
    state: Mapped[str]
    state_generation: Mapped[int]


_work = SATable(
    "sona_shadow_work",
    Base.metadata,
    Column("sona_shadow_work_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "target_dispatch_id",
        _UUID,
        ForeignKey("ml.sona_capture_target_dispatch.target_dispatch_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("recommendation_request_id", _UUID, nullable=False),
    Column("user_id", _UUID, nullable=False),
    _hash("model_manifest_sha256"),
    _hash("tokenizer_sha256"),
    _hash("pipeline_manifest_sha256"),
    _hash("execution_profile_sha256"),
    _hash("lineage_sha256"),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("claim_generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("attempt_count", Integer(), nullable=False, server_default=text("0")),
    Column("lease_until", _TIME),
    Column("next_retry_at", _TIME),
    Column("reason_code", Text()),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["user_id", "recommendation_request_id"],
        ["ml.sona_capture_bundle.user_id", "ml.sona_capture_bundle.recommendation_request_id"],
        name="fk_sona_work_bundle_owner",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "recommendation_request_id",
        "model_manifest_sha256",
        "tokenizer_sha256",
        "pipeline_manifest_sha256",
        "execution_profile_sha256",
        name="uq_sona_work_exact",
    ),
    UniqueConstraint(
        "sona_shadow_work_id", "user_id", "recommendation_request_id", name="uq_sona_work_owner"
    ),
    CheckConstraint("(state='CLAIMED')=(lease_until IS NOT NULL)", name="ck_sona_work_lease"),
    CheckConstraint("(state='RETRY_WAIT')=(next_retry_at IS NOT NULL)", name="ck_sona_work_retry"),
    Index(
        "ix_sona_work_claim_owner",
        "user_id",
        "state",
        "next_retry_at",
        "created_at",
        postgresql_where=text("state IN ('PENDING','RETRY_WAIT','CLAIMED')"),
    ),
    schema="ml",
)


class SonaShadowWorkRow(Base):
    __table__ = _work
    sona_shadow_work_id: Mapped[UUID]
    target_dispatch_id: Mapped[UUID]


_attempt = SATable(
    "sona_shadow_attempt",
    Base.metadata,
    Column("sona_shadow_attempt_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("sona_shadow_work_id", _UUID, nullable=False),
    Column("user_id", _UUID, nullable=False),
    Column("recommendation_request_id", _UUID, nullable=False),
    Column("attempt_no", Integer(), nullable=False),
    Column("claim_generation", BigInteger(), nullable=False),
    Column("outcome", Text(), nullable=False),
    Column("reason_code", Text()),
    Column("attempt_document", BYTEA(), nullable=False),
    _hash("attempt_sha256"),
    Column("started_at", _TIME, nullable=False),
    Column("finished_at", _TIME, nullable=False),
    ForeignKeyConstraint(
        ["sona_shadow_work_id", "user_id", "recommendation_request_id"],
        [
            "ml.sona_shadow_work.sona_shadow_work_id",
            "ml.sona_shadow_work.user_id",
            "ml.sona_shadow_work.recommendation_request_id",
        ],
        name="fk_sona_attempt_work_owner",
        ondelete="CASCADE",
    ),
    UniqueConstraint("sona_shadow_work_id", "attempt_no", name="uq_sona_attempt_number"),
    CheckConstraint(
        "attempt_sha256=sha256(attempt_document)", name="ck_sona_attempt_document_hash"
    ),
    Index("ix_sona_attempt_owner_work", "user_id", "sona_shadow_work_id", "attempt_no"),
    schema="ml",
)


class SonaShadowAttemptRow(Base):
    __table__ = _attempt
    sona_shadow_work_id: Mapped[UUID]


_evidence = SATable(
    "sona_shadow_evidence",
    Base.metadata,
    Column("sona_shadow_work_id", _UUID, primary_key=True),
    Column("user_id", _UUID, nullable=False),
    Column("recommendation_request_id", _UUID, nullable=False),
    _hash("evidence_sha256"),
    Column("evidence_document", BYTEA(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["sona_shadow_work_id", "user_id", "recommendation_request_id"],
        [
            "ml.sona_shadow_work.sona_shadow_work_id",
            "ml.sona_shadow_work.user_id",
            "ml.sona_shadow_work.recommendation_request_id",
        ],
        name="fk_sona_evidence_work_owner",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "evidence_sha256=sha256(evidence_document)",
        name="ck_sona_evidence_document_hash",
    ),
    Index("ix_sona_evidence_owner_request", "user_id", "recommendation_request_id"),
    schema="ml",
)


class SonaShadowEvidenceRow(Base):
    __table__ = _evidence
    sona_shadow_work_id: Mapped[UUID]


# PostgreSQL assigned these names to the migration's inline CHECK clauses. Keep
# every name in metadata: Alembic compares named CHECK constraints by name.
_HASH_COLUMNS = (
    (_erasure_receipt, ("ledger_digest", "removed_request_sha256", "receipt_sha256")),
    (_replay, ("request_sha256", "p11_ranking_sha256")),
    (_outcome, ("impression_payload_sha256", "outcome_payload_sha256")),
    (
        _registry_target,
        (
            "input_binding_sha256",
            "publication_sha256",
            "model_manifest_sha256",
            "tokenizer_sha256",
            "pipeline_manifest_sha256",
            "execution_profile_sha256",
        ),
    ),
    (
        _bundle,
        (
            "baseline_snapshot_sha256",
            "temporal_snapshot_sha256",
            "candidate_membership_sha256",
            "p11_ranking_sha256",
            "bundle_sha256",
            "consent_receipt_sha256",
        ),
    ),
    (
        _dispatch,
        (
            "model_manifest_sha256",
            "tokenizer_sha256",
            "pipeline_manifest_sha256",
            "execution_profile_sha256",
        ),
    ),
    (
        _work,
        (
            "model_manifest_sha256",
            "tokenizer_sha256",
            "pipeline_manifest_sha256",
            "execution_profile_sha256",
            "lineage_sha256",
        ),
    ),
    (_attempt, ("attempt_sha256",)),
    (_evidence, ("evidence_sha256",)),
)
for _table, _columns in _HASH_COLUMNS:
    for _name in _columns:
        _table.append_constraint(
            CheckConstraint(
                f"octet_length({_name})=32",
                name=f"{_table.name}_{_name}_check",
            )
        )

_OTHER_CHECKS = (
    (
        _bundle,
        {
            "consent_generation": "consent_generation>=1",
            "cutoff_at_ms": "cutoff_at_ms>=0",
            "interaction_watermark": "interaction_watermark>=0",
            "universe_count": "universe_count BETWEEN 0 AND 5000",
            "eligible_count": "eligible_count BETWEEN 0 AND 5000",
            "ineligibility_reason": "ineligibility_reason='UNIVERSE_OVER_CAP'",
            "bundle_document": "octet_length(bundle_document) BETWEEN 1 AND 16777216",
        },
    ),
    (
        _cursor,
        {
            "state": "state IN ('ACTIVE','EXPIRED','CANCELLED')",
            "last_seen_registry_generation": "last_seen_registry_generation>=0",
            "state_generation": "state_generation>=1",
        },
    ),
    (
        _dispatch,
        {
            "registry_generation": "registry_generation>=1",
            "state": "state IN ('WAITING','READY','CONSUMED','TERMINAL_INELIGIBLE',"
            "'EXPIRED','CANCELLED')",
            "state_generation": "state_generation>=1",
        },
    ),
    (
        _work,
        {
            "state": "state IN ('PENDING','CLAIMED','SUCCEEDED','RETRY_WAIT',"
            "'TERMINAL_INELIGIBLE','TERMINAL_FAILED','RETRY_EXHAUSTED',"
            "'CANCELLED','SUPERSEDED')",
            "state_generation": "state_generation>=1",
            "claim_generation": "claim_generation>=0",
            "attempt_count": "attempt_count BETWEEN 0 AND 16",
        },
    ),
    (
        _attempt,
        {
            "attempt_no": "attempt_no BETWEEN 1 AND 16",
            "claim_generation": "claim_generation>=1",
            "outcome": "outcome IN ('SUCCEEDED','RETRY_WAIT','TERMINAL_INELIGIBLE',"
            "'TERMINAL_FAILED','RETRY_EXHAUSTED','CANCELLED','SUPERSEDED')",
            "attempt_document": "octet_length(attempt_document) BETWEEN 1 AND 1048576",
            "": "finished_at>=started_at",
        },
    ),
    (
        _evidence,
        {"evidence_document": "octet_length(evidence_document) BETWEEN 1 AND 16777216"},
    ),
)
for _table, _constraints in _OTHER_CHECKS:
    for _suffix, _expression in _constraints.items():
        _table.append_constraint(
            CheckConstraint(
                _expression,
                name=f"{_table.name}_{_suffix}_check" if _suffix else f"{_table.name}_check",
            )
        )
