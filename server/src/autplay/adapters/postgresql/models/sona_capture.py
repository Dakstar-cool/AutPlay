"""Typed mappings for non-activating Sona native capture authority."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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
    schema="ml",
)


class SonaCaptureLineageCursorRow(Base):
    __table__ = _cursor
    recommendation_request_id: Mapped[UUID]
    user_id: Mapped[UUID]
    state: Mapped[str]
    expires_at: Mapped[datetime]


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
