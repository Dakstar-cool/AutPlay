"""Typed dormant Face interpreter, qualification and activation authority rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
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


def _digest(name: str, *, nullable: bool = False) -> Column[bytes]:
    return Column(name, BYTEA(), nullable=nullable)


_profile = SATable(
    "face_execution_profile",
    Base.metadata,
    Column("execution_profile_sha256", BYTEA(), primary_key=True),
    Column("profile_document", BYTEA(), nullable=False),
    Column("runtime", Text(), nullable=False),
    Column("provider", Text(), nullable=False),
    Column("precision", Text(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "execution_profile_sha256=sha256(profile_document)", name="ck_face_profile_digest"
    ),
    schema="ml",
)


class FaceExecutionProfileRow(Base):
    __table__ = _profile
    execution_profile_sha256: Mapped[bytes]


_interpreter = SATable(
    "face_semantic_interpreter",
    Base.metadata,
    Column(
        "face_semantic_interpreter_id", _UUID, primary_key=True, server_default=text("uuidv7()")
    ),
    Column("interpreter_key", Text(), nullable=False),
    Column("version", Text(), nullable=False),
    _digest("manifest_sha256"),
    Column(
        "embedding_model_id",
        _UUID,
        ForeignKey("ml.embedding_model.embedding_model_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "interpreter_artifact_release_id",
        _UUID,
        ForeignKey("ml.face_artifact_release.face_artifact_release_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "calibration_artifact_release_id",
        _UUID,
        ForeignKey("ml.face_artifact_release.face_artifact_release_id", ondelete="RESTRICT"),
    ),
    _digest("axis_schema_sha256"),
    _digest("preprocessing_sha256"),
    _digest("calibration_evidence_sha256"),
    Column("runtime_revision", Text(), nullable=False),
    Column("status", Text(), nullable=False),
    Column(
        "reviewer_user_id", _UUID, ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    ),
    Column("reviewed_at", _TIME),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "interpreter_key", "version", "manifest_sha256", name="uq_face_interpreter_version"
    ),
    CheckConstraint(
        "(status='CANDIDATE' AND reviewer_user_id IS NULL AND reviewed_at IS NULL) "
        "OR (status IN ('REVIEWED','BLOCKED') AND reviewer_user_id IS NOT NULL "
        "AND reviewed_at IS NOT NULL)",
        name="ck_face_interpreter_review",
    ),
    Index("ix_face_interpreter_encoder", "embedding_model_id", "status", "preprocessing_sha256"),
    schema="ml",
)


class FaceSemanticInterpreterRow(Base):
    __table__ = _interpreter
    face_semantic_interpreter_id: Mapped[UUID]


_qualification = SATable(
    "face_qualification_set",
    Base.metadata,
    Column("face_qualification_set_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    _digest("manifest_sha256"),
    Column("collection_kind", Text(), nullable=False),
    _digest("fixture_authority_sha256"),
    Column("fixture_authority_generation", BigInteger(), nullable=False),
    _digest("rater_authority_sha256"),
    Column("rater_authority_generation", BigInteger(), nullable=False),
    Column("source_count", Integer(), nullable=False),
    Column("segment_count", Integer(), nullable=False),
    Column("rater_count", Integer(), nullable=False),
    Column("retain_until", _TIME, nullable=False),
    Column("sealed_at", _TIME, nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'SEALED'")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("invalidated_at", _TIME),
    UniqueConstraint("manifest_sha256", name="face_qualification_set_manifest_sha256_key"),
    CheckConstraint(
        "(collection_kind='SMOKE' AND source_count BETWEEN 10 AND 20) "
        "OR (collection_kind='DEVELOPMENT' AND source_count>=30 AND rater_count>=3) "
        "OR (collection_kind='FINAL' AND source_count>=15 AND rater_count>=3 "
        "AND segment_count>=12*source_count)",
        name="ck_face_qualification_counts",
    ),
    CheckConstraint(
        "(state='SEALED' AND invalidated_at IS NULL) "
        "OR (state<>'SEALED' AND invalidated_at IS NOT NULL)",
        name="ck_face_qualification_state",
    ),
    CheckConstraint("retain_until>sealed_at", name="ck_face_qualification_retention"),
    Index(
        "ix_face_qualification_state_expiry",
        "state",
        "retain_until",
        postgresql_where=text("state='SEALED'"),
    ),
    schema="ml",
)


class FaceQualificationSetRow(Base):
    __table__ = _qualification
    face_qualification_set_id: Mapped[UUID]


_approval = SATable(
    "face_qualification_approval",
    Base.metadata,
    Column(
        "face_qualification_approval_id", _UUID, primary_key=True, server_default=text("uuidv7()")
    ),
    Column(
        "face_qualification_set_id",
        _UUID,
        ForeignKey("ml.face_qualification_set.face_qualification_set_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_semantic_interpreter_id",
        _UUID,
        ForeignKey(
            "ml.face_semantic_interpreter.face_semantic_interpreter_id", ondelete="RESTRICT"
        ),
        nullable=False,
    ),
    Column(
        "embedding_model_id",
        _UUID,
        ForeignKey("ml.embedding_model.embedding_model_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "execution_profile_sha256",
        BYTEA(),
        ForeignKey("ml.face_execution_profile.execution_profile_sha256", ondelete="RESTRICT"),
        nullable=False,
    ),
    _digest("qualification_manifest_sha256"),
    _digest("report_sha256"),
    Column("approval_document", BYTEA(), nullable=False),
    _digest("approval_sha256"),
    Column("signature_p1363", BYTEA(), nullable=False),
    Column(
        "signer_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("fixture_authority_generation", BigInteger(), nullable=False),
    Column("rater_authority_generation", BigInteger(), nullable=False),
    Column("signed_at", _TIME, nullable=False),
    Column("expires_at", _TIME, nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'APPROVED'")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("invalidated_at", _TIME),
    UniqueConstraint(
        "face_qualification_set_id",
        "face_semantic_interpreter_id",
        "execution_profile_sha256",
        "report_sha256",
        name="uq_face_approval_report_candidate",
    ),
    CheckConstraint("approval_sha256=sha256(approval_document)", name="ck_face_approval_digest"),
    CheckConstraint(
        "expires_at>signed_at AND expires_at<=signed_at+interval '365 days'",
        name="ck_face_approval_expiry",
    ),
    CheckConstraint(
        "(state='APPROVED' AND invalidated_at IS NULL) "
        "OR (state<>'APPROVED' AND invalidated_at IS NOT NULL)",
        name="ck_face_approval_state",
    ),
    Index(
        "ix_face_approval_state_expiry",
        "state",
        "expires_at",
        postgresql_where=text("state='APPROVED'"),
    ),
    schema="ml",
)


class FaceQualificationApprovalRow(Base):
    __table__ = _approval
    face_qualification_approval_id: Mapped[UUID]


_activation = SATable(
    "face_timeline_activation",
    Base.metadata,
    Column("face_timeline_activation_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column(
        "previous_activation_id",
        _UUID,
        ForeignKey("ml.face_timeline_activation.face_timeline_activation_id", ondelete="RESTRICT"),
    ),
    Column("action", Text(), nullable=False),
    Column(
        "embedding_model_id",
        _UUID,
        ForeignKey("ml.embedding_model.embedding_model_id", ondelete="RESTRICT"),
    ),
    Column(
        "face_semantic_interpreter_id",
        _UUID,
        ForeignKey(
            "ml.face_semantic_interpreter.face_semantic_interpreter_id", ondelete="RESTRICT"
        ),
    ),
    Column(
        "execution_profile_sha256",
        BYTEA(),
        ForeignKey("ml.face_execution_profile.execution_profile_sha256", ondelete="RESTRICT"),
    ),
    Column(
        "face_qualification_approval_id",
        _UUID,
        ForeignKey(
            "ml.face_qualification_approval.face_qualification_approval_id", ondelete="RESTRICT"
        ),
    ),
    _digest("preprocessing_sha256", nullable=True),
    _digest("artifact_policy_list_sha256", nullable=True),
    _digest("evidence_sha256"),
    Column(
        "actor_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("operation_id", _UUID, nullable=False),
    _digest("step_up_receipt_sha256"),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint("activation_epoch", name="face_timeline_activation_activation_epoch_key"),
    UniqueConstraint("actor_user_id", "operation_id", name="uq_face_activation_operation"),
    CheckConstraint(
        "(action='DEACTIVATE' AND embedding_model_id IS NULL "
        "AND face_semantic_interpreter_id IS NULL AND execution_profile_sha256 IS NULL "
        "AND face_qualification_approval_id IS NULL AND preprocessing_sha256 IS NULL "
        "AND artifact_policy_list_sha256 IS NULL) "
        "OR (action IN ('ACTIVATE','ROLLBACK') AND embedding_model_id IS NOT NULL "
        "AND face_semantic_interpreter_id IS NOT NULL "
        "AND execution_profile_sha256 IS NOT NULL "
        "AND face_qualification_approval_id IS NOT NULL "
        "AND preprocessing_sha256 IS NOT NULL "
        "AND artifact_policy_list_sha256 IS NOT NULL)",
        name="ck_face_activation_target",
    ),
    schema="ml",
)


class FaceTimelineActivationRow(Base):
    __table__ = _activation
    face_timeline_activation_id: Mapped[UUID]


_current = SATable(
    "face_activation_current",
    Base.metadata,
    Column("singleton", Integer(), primary_key=True, server_default=text("1")),
    Column("activation_epoch", BigInteger(), nullable=False, server_default=text("0")),
    Column(
        "face_timeline_activation_id",
        _UUID,
        ForeignKey("ml.face_timeline_activation.face_timeline_activation_id", ondelete="RESTRICT"),
    ),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "(activation_epoch=0 AND face_timeline_activation_id IS NULL) "
        "OR (activation_epoch>0 AND face_timeline_activation_id IS NOT NULL)",
        name="ck_face_activation_current_identity",
    ),
    schema="ml",
)


class FaceActivationCurrentRow(Base):
    __table__ = _current
    singleton: Mapped[int]


_history = SATable(
    "face_analysis_policy_history",
    Base.metadata,
    Column(
        "face_analysis_policy_event_id", _UUID, primary_key=True, server_default=text("uuidv7()")
    ),
    Column(
        "owner_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("policy_generation", BigInteger(), nullable=False),
    Column("desired_enabled", Boolean(), nullable=False),
    Column("admin_inhibited", Boolean(), nullable=False),
    Column(
        "effective_enabled",
        Boolean(),
        Computed("desired_enabled AND NOT admin_inhibited", persisted=True),
    ),
    Column("enable_watermark", _TIME),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column("actor_user_id", _UUID, ForeignKey("account.user_account.user_id", ondelete="SET NULL")),
    Column("actor_role", Text(), nullable=False),
    Column("scope", Text(), nullable=False),
    Column("reason", Text(), nullable=False),
    Column("operation_id", _UUID, nullable=False),
    _digest("request_sha256"),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint("owner_user_id", "policy_generation", name="uq_face_policy_owner_generation"),
    CheckConstraint(
        "(desired_enabled AND enable_watermark IS NOT NULL) "
        "OR (NOT desired_enabled AND enable_watermark IS NULL)",
        name="ck_face_policy_watermark",
    ),
    CheckConstraint(
        "(scope='SELF' AND actor_user_id=owner_user_id) "
        "OR (scope='SAFETY' AND actor_role IN ('OWNER','ADMIN'))",
        name="ck_face_policy_actor_scope",
    ),
    Index(
        "uq_face_policy_actor_operation",
        "actor_user_id",
        "operation_id",
        unique=True,
        postgresql_where=text("actor_user_id IS NOT NULL"),
    ),
    Index("ix_face_policy_owner_created", "owner_user_id", text("created_at DESC")),
    schema="ml",
)


class FaceAnalysisPolicyHistoryRow(Base):
    __table__ = _history
    face_analysis_policy_event_id: Mapped[UUID]


_policy_current = SATable(
    "face_analysis_policy_current",
    Base.metadata,
    Column(
        "owner_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("policy_generation", BigInteger(), nullable=False),
    Column("desired_enabled", Boolean(), nullable=False),
    Column("admin_inhibited", Boolean(), nullable=False),
    Column(
        "effective_enabled",
        Boolean(),
        Computed("desired_enabled AND NOT admin_inhibited", persisted=True),
    ),
    Column("enable_watermark", _TIME),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column(
        "face_analysis_policy_event_id",
        _UUID,
        ForeignKey(
            "ml.face_analysis_policy_history.face_analysis_policy_event_id", ondelete="CASCADE"
        ),
        nullable=False,
    ),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["owner_user_id", "policy_generation"],
        [
            "ml.face_analysis_policy_history.owner_user_id",
            "ml.face_analysis_policy_history.policy_generation",
        ],
        name="fk_face_policy_current_history",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "face_analysis_policy_event_id",
        name="face_analysis_policy_current_face_analysis_policy_event_id_key",
    ),
    CheckConstraint(
        "(desired_enabled AND enable_watermark IS NOT NULL) "
        "OR (NOT desired_enabled AND enable_watermark IS NULL)",
        name="ck_face_policy_current_watermark",
    ),
    Index(
        "ix_face_policy_current_effective",
        "owner_user_id",
        "activation_epoch",
        postgresql_where=text("effective_enabled"),
    ),
    schema="ml",
)


class FaceAnalysisPolicyCurrentRow(Base):
    __table__ = _policy_current
    owner_user_id: Mapped[UUID]


# The migration defines these column-level CHECKs inline. Alembic's named
# constraint comparison requires the typed metadata to carry the same names.
_INLINE_CHECKS: tuple[tuple[SATable, dict[str, str]], ...] = (
    (
        _profile,
        {
            "execution_profile_sha256": "octet_length(execution_profile_sha256)=32",
            "profile_document": "octet_length(profile_document) BETWEEN 1 AND 65536",
            "runtime": "length(runtime) BETWEEN 1 AND 200",
            "provider": "length(provider) BETWEEN 1 AND 100",
            "precision": "length(precision) BETWEEN 1 AND 50",
        },
    ),
    (
        _interpreter,
        {
            "interpreter_key": "length(interpreter_key) BETWEEN 1 AND 200",
            "version": "length(version) BETWEEN 1 AND 100",
            "manifest_sha256": "octet_length(manifest_sha256)=32",
            "axis_schema_sha256": "octet_length(axis_schema_sha256)=32",
            "preprocessing_sha256": "octet_length(preprocessing_sha256)=32",
            "calibration_evidence_sha256": "octet_length(calibration_evidence_sha256)=32",
            "runtime_revision": "length(runtime_revision) BETWEEN 1 AND 200",
            "status": "status IN ('CANDIDATE','REVIEWED','BLOCKED')",
        },
    ),
    (
        _qualification,
        {
            "manifest_sha256": "octet_length(manifest_sha256)=32",
            "collection_kind": "collection_kind IN ('SMOKE','DEVELOPMENT','FINAL')",
            "fixture_authority_sha256": "octet_length(fixture_authority_sha256)=32",
            "fixture_authority_generation": "fixture_authority_generation>=1",
            "rater_authority_sha256": "octet_length(rater_authority_sha256)=32",
            "rater_authority_generation": "rater_authority_generation>=1",
            "source_count": "source_count BETWEEN 1 AND 1000",
            "segment_count": "segment_count BETWEEN 0 AND 12000",
            "rater_count": "rater_count BETWEEN 0 AND 20",
            "state": "state IN ('SEALED','INVALIDATED','EXPIRED','DELETED')",
            "state_generation": "state_generation>=1",
        },
    ),
    (
        _approval,
        {
            "qualification_manifest_sha256": "octet_length(qualification_manifest_sha256)=32",
            "report_sha256": "octet_length(report_sha256)=32",
            "approval_document": "octet_length(approval_document) BETWEEN 1 AND 1048576",
            "approval_sha256": "octet_length(approval_sha256)=32",
            "signature_p1363": "octet_length(signature_p1363)=64",
            "fixture_authority_generation": "fixture_authority_generation>=1",
            "rater_authority_generation": "rater_authority_generation>=1",
            "state": "state IN ('APPROVED','INVALIDATED','EXPIRED')",
            "state_generation": "state_generation>=1",
        },
    ),
    (
        _activation,
        {
            "activation_epoch": "activation_epoch>=1",
            "action": "action IN ('ACTIVATE','ROLLBACK','DEACTIVATE')",
            "preprocessing_sha256": (
                "preprocessing_sha256 IS NULL OR octet_length(preprocessing_sha256)=32"
            ),
            "artifact_policy_list_sha256": (
                "artifact_policy_list_sha256 IS NULL "
                "OR octet_length(artifact_policy_list_sha256)=32"
            ),
            "evidence_sha256": "octet_length(evidence_sha256)=32",
            "step_up_receipt_sha256": "octet_length(step_up_receipt_sha256)=32",
        },
    ),
    (
        _current,
        {"singleton": "singleton=1", "activation_epoch": "activation_epoch>=0"},
    ),
    (
        _history,
        {
            "policy_generation": "policy_generation>=1",
            "activation_epoch": "activation_epoch>=0",
            "actor_role": "actor_role IN ('OWNER','ADMIN','USER')",
            "scope": "scope IN ('SELF','SAFETY')",
            "reason": "length(reason) BETWEEN 1 AND 200",
            "request_sha256": "octet_length(request_sha256)=32",
        },
    ),
    (
        _policy_current,
        {"policy_generation": "policy_generation>=1", "activation_epoch": "activation_epoch>=0"},
    ),
)

for _table, _checks in _INLINE_CHECKS:
    for _column, _expression in _checks.items():
        _table.append_constraint(
            CheckConstraint(_expression, name=f"{_table.name}_{_column}_check")
        )
