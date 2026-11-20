"""Immutable durable source replacement identity and explicit child receipts."""

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_root = SATable(
    "face_source_replacement",
    Base.metadata,
    Column("face_source_replacement_id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "recording_id",
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.recording.recording_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "expected_audio_variant_id",
        PG_UUID(as_uuid=True),
        ForeignKey("vault.audio_variant.audio_variant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "audio_variant_id",
        PG_UUID(as_uuid=True),
        ForeignKey("vault.audio_variant.audio_variant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("expected_source_sha256", LargeBinary(), nullable=False),
    Column("source_sha256", LargeBinary(), nullable=False),
    Column("replacement_generation", BigInteger(), nullable=False),
    Column("request_sha256", LargeBinary(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("retired_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("successor_count", BigInteger(), nullable=False, server_default=text("0")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("completed_at", TIMESTAMP(timezone=True)),
    UniqueConstraint(
        "recording_id", "replacement_generation", name="uq_face_source_replacement_generation"
    ),
    CheckConstraint(
        "expected_audio_variant_id<>audio_variant_id AND "
        "octet_length(expected_source_sha256)=32 "
        "AND octet_length(source_sha256)=32 AND octet_length(request_sha256)=32",
        name="ck_face_source_replacement_source",
    ),
    CheckConstraint(
        "replacement_generation BETWEEN 1 AND 9007199254740990",
        name="ck_face_source_replacement_generation",
    ),
    CheckConstraint(
        "retired_count>=0 AND successor_count BETWEEN 0 AND retired_count",
        name="ck_face_source_replacement_counts",
    ),
    CheckConstraint(
        "(state='PENDING' AND completed_at IS NULL) OR "
        "(state='COMPLETE' AND completed_at IS NOT NULL)",
        name="ck_face_source_replacement_completion",
    ),
    Index(
        "uq_face_source_replacement_pending",
        "recording_id",
        unique=True,
        postgresql_where=text("state='PENDING'"),
    ),
    Index(
        "ix_face_source_replacement_pending",
        "created_at",
        "face_source_replacement_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)

_candidate = SATable(
    "face_source_replacement_candidate",
    Base.metadata,
    Column(
        "face_source_replacement_id",
        PG_UUID(as_uuid=True),
        ForeignKey("ml.face_source_replacement.face_source_replacement_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "predecessor_sponsor_id",
        PG_UUID(as_uuid=True),
        ForeignKey("ml.face_analysis_sponsor.face_sponsor_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("candidate_document", LargeBinary(), nullable=False),
    Column("candidate_sha256", LargeBinary(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column(
        "successor_sponsor_id",
        PG_UUID(as_uuid=True),
        ForeignKey("ml.face_analysis_sponsor.face_sponsor_id", ondelete="RESTRICT"),
    ),
    Column("reason", Text()),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("completed_at", TIMESTAMP(timezone=True)),
    CheckConstraint(
        "octet_length(candidate_document) BETWEEN 1 AND 131072 AND "
        "candidate_sha256=sha256(candidate_document)",
        name="ck_face_replacement_candidate_bytes",
    ),
    CheckConstraint(
        "(state='PENDING' AND successor_sponsor_id IS NULL "
        "AND reason IS NULL AND completed_at IS NULL) "
        "OR (state='APPLIED' AND successor_sponsor_id IS NOT NULL "
        "AND reason IS NULL AND completed_at IS NOT NULL) "
        "OR (state='CANCELLED' AND successor_sponsor_id IS NULL "
        "AND reason IS NOT NULL AND reason ~ '^[A-Z][A-Z0-9_]{0,63}$' "
        "AND completed_at IS NOT NULL)",
        name="ck_face_replacement_candidate_completion",
    ),
    Index(
        "ix_face_replacement_candidate_pending",
        "face_source_replacement_id",
        "predecessor_sponsor_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)


class FaceSourceReplacementRow(Base):
    __table__ = _root
    face_source_replacement_id: Mapped[UUID]


class FaceSourceReplacementCandidateRow(Base):
    __table__ = _candidate
    face_source_replacement_id: Mapped[UUID]
    predecessor_sponsor_id: Mapped[UUID]


_remediation = SATable(
    "face_source_replacement_remediation",
    Base.metadata,
    Column("remediation_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("face_source_replacement_id", PG_UUID(as_uuid=True), nullable=False),
    Column("replacement_generation", BigInteger(), nullable=False),
    Column("predecessor_sponsor_id", PG_UUID(as_uuid=True), nullable=False),
    Column("candidate_sha256", LargeBinary(), nullable=False),
    Column(
        "operator_user_id",
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("reason", Text(), nullable=False),
    Column("request_sha256", LargeBinary(), nullable=False),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["face_source_replacement_id", "predecessor_sponsor_id"],
        [
            "ml.face_source_replacement_candidate.face_source_replacement_id",
            "ml.face_source_replacement_candidate.predecessor_sponsor_id",
        ],
        ondelete="RESTRICT",
        name="fk_face_remediation_candidate",
    ),
    UniqueConstraint(
        "face_source_replacement_id", "predecessor_sponsor_id", name="uq_face_remediation_candidate"
    ),
    CheckConstraint(
        "replacement_generation BETWEEN 1 AND 9007199254740990",
        name="ck_face_remediation_generation",
    ),
    CheckConstraint(
        "octet_length(candidate_sha256)=32 AND octet_length(request_sha256)=32",
        name="ck_face_remediation_hashes",
    ),
    CheckConstraint(
        "reason ~ '^[A-Z][A-Z0-9_]{0,63}$' "
        "AND reason NOT IN ('PREDECESSOR_RETIRED','SUCCESSOR_INELIGIBLE')",
        name="ck_face_remediation_reason",
    ),
    schema="ml",
)


class FaceSourceReplacementRemediationRow(Base):
    __table__ = _remediation
    remediation_id: Mapped[UUID]
