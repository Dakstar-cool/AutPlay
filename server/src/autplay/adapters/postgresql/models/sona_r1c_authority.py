"""Private C0105 metadata. Importing this module extends no live Base registry."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..base import NAMING_CONVENTION
from .account import UserAccountRow
from .native_training_execution import NativeTrainingConsumedInputRow
from .training_work import TrainingRunRow


class R1CAuthorityBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION))


def _hashes(name: str, *columns: str) -> CheckConstraint:
    return CheckConstraint(
        " AND ".join(f"octet_length({column})=32" for column in columns), name=name
    )


def _counters(name: str, *columns: str, minimum: int = 1) -> CheckConstraint:
    return CheckConstraint(
        " AND ".join(f"{column} BETWEEN {minimum} AND 9007199254740991" for column in columns),
        name=name,
    )


class _Scope:
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    serving_ledger_key_id: Mapped[str] = mapped_column(Text)


class R1CTrainingBaselineRow(_Scope, R1CAuthorityBase):
    __tablename__ = "sona_r1c_training_baseline"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(TrainingRunRow.__table__.c.run_id), primary_key=True
    )
    f_operation_id: Mapped[UUID]
    f_execution_id: Mapped[UUID]
    f_input_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    f_input_schema_version: Mapped[int] = mapped_column(Integer)
    registration_sha256: Mapped[bytes] = mapped_column(BYTEA)
    training_input_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    full_owner_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_owner_set_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_lineage_key_id: Mapped[str] = mapped_column(Text)
    run_lineage_key_id: Mapped[str] = mapped_column(Text)
    source_owner_count: Mapped[int] = mapped_column(Integer)
    member_count: Mapped[int] = mapped_column(Integer)
    baseline_sha256: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    admission_xid: Mapped[str] = mapped_column(
        Text, server_default=text("pg_current_xact_id()::text")
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "f_input_receipt_sha256"],
            [
                NativeTrainingConsumedInputRow.__table__.c.run_id,
                NativeTrainingConsumedInputRow.__table__.c.receipt_sha256,
            ],
            ondelete="RESTRICT",
        ),
        _hashes(
            "ck_r1c_baseline_hashes",
            "f_input_receipt_sha256",
            "registration_sha256",
            "training_input_binding_sha256",
            "full_owner_binding_sha256",
            "source_owner_set_sha256",
            "baseline_sha256",
        ),
        _counters("ck_r1c_baseline_epoch", "identity_epoch"),
        CheckConstraint(
            "f_input_schema_version=1 AND member_count BETWEEN 1 AND 4096 "
            "AND source_owner_count=member_count "
            "AND source_lineage_key_id ~ '^[A-Za-z0-9_.-]{1,128}$' "
            "AND run_lineage_key_id ~ '^[A-Za-z0-9_.-]{1,128}$' "
            "AND serving_ledger_key_id ~ '^[A-Za-z0-9_-]{1,64}$'",
            name="ck_r1c_baseline_shape",
        ),
        UniqueConstraint(
            "run_id",
            "server_instance_id",
            "identity_epoch",
            "serving_ledger_key_id",
            name="uq_r1c_baseline_scope",
        ),
        {"schema": "ml"},
    )


class R1CParticipantDependencyRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_participant_dependency"
    server_instance_id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serving_ledger_key_id: Mapped[str] = mapped_column(Text, primary_key=True)
    serving_owner_tag: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey(UserAccountRow.__table__.c.user_id))
    dependency_generation: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        _hashes("ck_r1c_dependency_tag", "serving_owner_tag"),
        _counters("ck_r1c_dependency_epoch", "identity_epoch"),
        _counters("ck_r1c_dependency_generation", "dependency_generation", minimum=0),
        CheckConstraint(
            "serving_ledger_key_id ~ '^[A-Za-z0-9_-]{1,64}$'", name="ck_r1c_dependency_key"
        ),
        UniqueConstraint(
            "server_instance_id",
            "identity_epoch",
            "serving_ledger_key_id",
            "owner_user_id",
            name="uq_r1c_dependency_owner",
        ),
        Index("ix_r1c_dependency_owner", "owner_user_id"),
        {"schema": "ml"},
    )


class R1CNegativeAppliedRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_negative_applied"
    server_instance_id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serving_ledger_key_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey(UserAccountRow.__table__.c.user_id))
    serving_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    revision: Mapped[int] = mapped_column(BigInteger)
    decision: Mapped[str] = mapped_column(Text)
    actor_tag: Mapped[bytes | None] = mapped_column(BYTEA)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    previous_policy_sha256: Mapped[bytes] = mapped_column(BYTEA)
    changed_at_us: Mapped[int] = mapped_column(BigInteger)
    head_sha256: Mapped[bytes] = mapped_column(BYTEA)
    covered_dependency_generation: Mapped[int] = mapped_column(BigInteger)
    affected_run_count: Mapped[int] = mapped_column(BigInteger)
    affected_run_set_sha256: Mapped[bytes] = mapped_column(BYTEA)
    applied_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        _hashes(
            "ck_r1c_negative_hashes",
            "serving_owner_tag",
            "request_sha256",
            "previous_policy_sha256",
            "head_sha256",
            "affected_run_set_sha256",
        ),
        _counters("ck_r1c_negative_positive", "identity_epoch", "revision"),
        _counters(
            "ck_r1c_negative_counters",
            "changed_at_us",
            "covered_dependency_generation",
            "affected_run_count",
            minimum=0,
        ),
        CheckConstraint(
            "decision IN ('DENIED','WITHDRAWN') "
            "AND (actor_tag IS NULL OR octet_length(actor_tag)=32)",
            name="ck_r1c_negative_decision",
        ),
        ForeignKeyConstraint(
            ["server_instance_id", "identity_epoch", "serving_ledger_key_id", "serving_owner_tag"],
            [
                "ml.sona_r1c_participant_dependency." + name
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                    "serving_owner_tag",
                )
            ],
        ),
        Index("ix_r1c_negative_owner", "owner_user_id", "operation_id"),
        {"schema": "ml"},
    )


class R1CTrainingBaselineMemberRow(_Scope, R1CAuthorityBase):
    __tablename__ = "sona_r1c_training_baseline_member"
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(UserAccountRow.__table__.c.user_id), primary_key=True
    )
    training_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    training_consent_revision: Mapped[int] = mapped_column(BigInteger)
    source_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    serving_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    registered_dependency_generation: Mapped[int] = mapped_column(BigInteger)
    head_sha256: Mapped[bytes] = mapped_column(BYTEA)
    head_operation_id: Mapped[UUID | None]
    head_revision: Mapped[int | None] = mapped_column(BigInteger)
    head_decision: Mapped[str | None] = mapped_column(Text)
    head_actor_tag: Mapped[bytes | None] = mapped_column(BYTEA)
    head_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    head_previous_policy_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    head_changed_at_us: Mapped[int | None] = mapped_column(BigInteger)
    last_applied_negative_operation_id: Mapped[UUID | None]
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "server_instance_id", "identity_epoch", "serving_ledger_key_id"],
            [
                "ml.sona_r1c_training_baseline." + name
                for name in (
                    "run_id",
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                )
            ],
        ),
        ForeignKeyConstraint(
            ["server_instance_id", "identity_epoch", "serving_ledger_key_id", "serving_owner_tag"],
            [
                "ml.sona_r1c_participant_dependency." + name
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                    "serving_owner_tag",
                )
            ],
        ),
        ForeignKeyConstraint(
            [
                "server_instance_id",
                "identity_epoch",
                "serving_ledger_key_id",
                "last_applied_negative_operation_id",
            ],
            [
                "ml.sona_r1c_negative_applied." + name
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                    "operation_id",
                )
            ],
        ),
        _hashes(
            "ck_r1c_member_hashes",
            "training_owner_tag",
            "source_owner_tag",
            "serving_owner_tag",
            "head_sha256",
        ),
        _counters(
            "ck_r1c_member_counters",
            "identity_epoch",
            "training_consent_revision",
            "registered_dependency_generation",
        ),
        CheckConstraint(
            "((head_operation_id IS NULL AND head_revision IS NULL AND head_decision IS NULL "
            "AND head_actor_tag IS NULL AND head_request_sha256 IS NULL "
            "AND head_previous_policy_sha256 IS NULL AND head_changed_at_us IS NULL) OR "
            "(head_operation_id IS NOT NULL AND head_revision BETWEEN 1 AND 9007199254740991 "
            "AND head_decision IN ('GRANTED','DENIED','WITHDRAWN') "
            "AND octet_length(head_request_sha256)=32 "
            "AND octet_length(head_previous_policy_sha256)=32 "
            "AND head_changed_at_us BETWEEN 0 AND 9007199254740991 "
            "AND (head_actor_tag IS NULL OR octet_length(head_actor_tag)=32) "
            "AND (head_decision<>'GRANTED' OR (head_actor_tag IS NOT NULL "
            "AND head_revision<9007199254740991)))) IS TRUE",
            name="ck_r1c_member_head",
        ),
        UniqueConstraint("run_id", "training_owner_tag", name="uq_r1c_member_training"),
        UniqueConstraint("run_id", "source_owner_tag", name="uq_r1c_member_source"),
        UniqueConstraint("run_id", "serving_owner_tag", name="uq_r1c_member_serving"),
        UniqueConstraint(
            "server_instance_id",
            "identity_epoch",
            "serving_ledger_key_id",
            "serving_owner_tag",
            "registered_dependency_generation",
            name="uq_r1c_member_position",
        ),
        Index(
            "ix_r1c_member_reverse",
            "server_instance_id",
            "identity_epoch",
            "serving_ledger_key_id",
            "serving_owner_tag",
            "run_id",
        ),
        Index("ix_r1c_member_owner", "owner_user_id", "run_id"),
        {"schema": "ml"},
    )


class R1CRunAuthorityRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_run_authority"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.sona_r1c_training_baseline.run_id"), primary_key=True
    )
    baseline_sha256: Mapped[bytes] = mapped_column(BYTEA)
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    revocation_generation: Mapped[int] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        _hashes("ck_r1c_authority_hash", "baseline_sha256"),
        _counters("ck_r1c_authority_generation", "authority_generation"),
        _counters("ck_r1c_authority_revocation", "revocation_generation", minimum=0),
        CheckConstraint("revoked=(revocation_generation>0)", name="ck_r1c_authority_sticky"),
        {"schema": "ml"},
    )


class R1CRunRevocationRow(_Scope, R1CAuthorityBase):
    __tablename__ = "sona_r1c_run_revocation"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.sona_r1c_run_authority.run_id"), primary_key=True
    )
    negative_operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    serving_ledger_key_id: Mapped[str] = mapped_column(Text, primary_key=True)
    registered_dependency_generation: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "server_instance_id",
                "identity_epoch",
                "serving_ledger_key_id",
                "negative_operation_id",
            ],
            [
                "ml.sona_r1c_negative_applied." + name
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                    "operation_id",
                )
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
        _counters(
            "ck_r1c_revocation_position", "identity_epoch", "registered_dependency_generation"
        ),
        {"schema": "ml"},
    )


class R1CTerminalDenyRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_terminal_deny"
    # No run/account FK: this denial survives removal of the entire personal graph.
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    terminal_generation: Mapped[int] = mapped_column(BigInteger)
    reason_code: Mapped[str] = mapped_column(Text)
    completed_day: Mapped[date] = mapped_column(Date)
    transition_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        _counters("ck_r1c_terminal_counters", "identity_epoch", "terminal_generation"),
        _hashes("ck_r1c_terminal_hash", "transition_sha256"),
        CheckConstraint("reason_code='OWNER_PURGE'", name="ck_r1c_terminal_reason"),
        {"schema": "ml"},
    )


class R1CTerminalNegativeCoverageRow(_Scope, R1CAuthorityBase):
    __tablename__ = "sona_r1c_terminal_negative_coverage"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.sona_r1c_terminal_deny.run_id"), primary_key=True
    )
    negative_operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    serving_ledger_key_id: Mapped[str] = mapped_column(Text, primary_key=True)
    registered_dependency_generation: Mapped[int] = mapped_column(BigInteger)
    terminal_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "server_instance_id",
                "identity_epoch",
                "serving_ledger_key_id",
                "negative_operation_id",
            ],
            [
                "ml.sona_r1c_negative_applied." + name
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "serving_ledger_key_id",
                    "operation_id",
                )
            ],
        ),
        _counters(
            "ck_r1c_terminal_coverage_position",
            "identity_epoch",
            "registered_dependency_generation",
        ),
        _hashes("ck_r1c_terminal_coverage_hash", "terminal_sha256"),
        {"schema": "ml"},
    )


class R1CApprovalRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_approval"
    approval_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.sona_r1c_run_authority.run_id"))
    envelope_bytes: Mapped[bytes] = mapped_column(BYTEA)
    parents: Mapped[dict[str, Any]] = mapped_column(JSONB)
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(envelope_bytes) BETWEEN 2 AND 65536 "
            "AND approval_sha256=sha256(envelope_bytes) "
            "AND jsonb_typeof(parents)='object' AND octet_length(parents::text)<=524288",
            name="ck_r1c_approval_document",
        ),
        _counters("ck_r1c_approval_generation", "authority_generation"),
        Index("ix_r1c_approval_run", "run_id"),
        {"schema": "ml"},
    )


class R1CApprovalArtifactRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_approval_artifact"
    approval_sha256: Mapped[bytes] = mapped_column(
        ForeignKey("ml.sona_r1c_approval.approval_sha256"), primary_key=True
    )
    artifact_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    decision_id: Mapped[UUID]
    effective_generation: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        _hashes("ck_r1c_approval_artifact_hash", "artifact_sha256"),
        _counters("ck_r1c_approval_artifact_generation", "effective_generation"),
        {"schema": "ml"},
    )


class R1CCohortRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_cohort"
    cohort_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.sona_r1c_run_authority.run_id"))
    generation: Mapped[int] = mapped_column(BigInteger)
    killed: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        _counters("ck_r1c_cohort_generation", "generation"),
        Index("ix_r1c_cohort_run", "run_id", "cohort_id"),
        {"schema": "ml"},
    )


class R1CRecipientRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_recipient"
    cohort_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.sona_r1c_cohort.cohort_id"), primary_key=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(UserAccountRow.__table__.c.user_id), primary_key=True
    )
    generation: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        _counters("ck_r1c_recipient_generation", "generation"),
        Index("ix_r1c_recipient_owner", "owner_user_id", "cohort_id"),
        {"schema": "ml"},
    )


class R1CRestoreQuarantineRow(R1CAuthorityBase):
    __tablename__ = "sona_r1c_restore_quarantine"
    singleton: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    released_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint("singleton=1", name="ck_r1c_restore_singleton"),
        _counters("ck_r1c_restore_generation", "generation"),
        {"schema": "ml"},
    )


R1C_AUTHORITY_ROWS = (
    R1CParticipantDependencyRow,
    R1CNegativeAppliedRow,
    R1CTrainingBaselineRow,
    R1CTrainingBaselineMemberRow,
    R1CRunAuthorityRow,
    R1CRunRevocationRow,
    R1CTerminalDenyRow,
    R1CTerminalNegativeCoverageRow,
    R1CApprovalRow,
    R1CApprovalArtifactRow,
    R1CCohortRow,
    R1CRecipientRow,
    R1CRestoreQuarantineRow,
)
R1C_AUTHORITY_TABLES: tuple[Table, ...] = tuple(
    cast(Table, row.__table__) for row in R1C_AUTHORITY_ROWS
)
