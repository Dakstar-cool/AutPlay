"""Durable source capture, redirect generations and exact candidate evidence."""

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base


def _uuid(
    name: str, target: str | None = None, *, nullable: bool = True, primary_key: bool = False
) -> Column[UUID]:
    args = () if target is None else (ForeignKey(target, ondelete="RESTRICT"),)
    return Column(name, PG_UUID(as_uuid=True), *args, nullable=nullable, primary_key=primary_key)


_authority = SATable(
    "face_redirect_authority",
    Base.metadata,
    _uuid("recording_id", "catalog.recording.recording_id", nullable=False, primary_key=True),
    Column("redirect_generation", BigInteger(), nullable=False, server_default=text("0")),
    CheckConstraint(
        "redirect_generation BETWEEN 0 AND 9007199254740990", name="ck_face_redirect_generation"
    ),
    schema="ml",
)

_mutation = SATable(
    "face_source_mutation",
    Base.metadata,
    Column(
        "face_source_mutation_id",
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    ),
    Column(
        "mutation_generation",
        BigInteger(),
        nullable=False,
        server_default=text("nextval('ml.face_source_mutation_generation_seq')"),
    ),
    _uuid("recording_id", "catalog.recording.recording_id", nullable=False),
    Column("recording_ids", ARRAY(PG_UUID(as_uuid=True)), nullable=False),
    Column("kind", Text(), nullable=False),
    _uuid("expected_audio_variant_id", "vault.audio_variant.audio_variant_id"),
    _uuid("target_recording_id", "catalog.recording.recording_id"),
    _uuid("target_audio_variant_id", "vault.audio_variant.audio_variant_id"),
    Column("target_source_sha256", LargeBinary()),
    _uuid("replacement_id", "ml.face_source_replacement.face_source_replacement_id"),
    _uuid("invalidation_id", "ml.face_source_invalidation.face_source_invalidation_id"),
    _uuid("highwater_reference_id"),
    _uuid("cursor_reference_id"),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("retired_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("successor_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("scanned_count", BigInteger(), nullable=False, server_default=text("0")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("completed_at", TIMESTAMP(timezone=True)),
    UniqueConstraint("mutation_generation", name="uq_face_source_mutation_generation"),
    CheckConstraint(
        "mutation_generation BETWEEN 1 AND 9007199254740990",
        name="ck_face_source_mutation_generation",
    ),
    CheckConstraint(
        "cardinality(recording_ids) BETWEEN 1 AND 35 "
        "AND array_position(recording_ids,NULL) IS NULL AND recording_id=ANY(recording_ids)",
        name="ck_face_source_mutation_scope",
    ),
    CheckConstraint(
        "kind IN ('CANONICAL','VARIANT','OBJECT','RECORDING','REPLICA','REDIRECT')",
        name="ck_face_source_mutation_kind",
    ),
    CheckConstraint(
        "(target_audio_variant_id IS NULL AND target_source_sha256 IS NULL) OR "
        "(target_audio_variant_id IS NOT NULL AND target_recording_id IS NOT NULL "
        "AND target_source_sha256 IS NOT NULL AND octet_length(target_source_sha256)=32)",
        name="ck_face_source_mutation_target",
    ),
    CheckConstraint(
        "retired_count>=0 AND successor_count>=0 AND scanned_count>=0",
        name="ck_face_source_mutation_counts",
    ),
    CheckConstraint(
        "replacement_id IS NULL OR invalidation_id IS NULL", name="ck_face_source_mutation_delegate"
    ),
    CheckConstraint(
        "(state='PENDING' AND completed_at IS NULL) OR (state='COMPLETE' AND completed_at IS NOT "
        "NULL)",
        name="ck_face_source_mutation_completion",
    ),
    Index(
        "ix_face_source_mutation_pending",
        "created_at",
        "face_source_mutation_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)

_candidate = SATable(
    "face_source_mutation_candidate",
    Base.metadata,
    _uuid(
        "face_source_mutation_id",
        "ml.face_source_mutation.face_source_mutation_id",
        nullable=False,
        primary_key=True,
    ),
    _uuid(
        "predecessor_sponsor_id",
        "ml.face_analysis_sponsor.face_sponsor_id",
        nullable=False,
        primary_key=True,
    ),
    Column("candidate_document", LargeBinary(), nullable=False),
    Column("candidate_sha256", LargeBinary(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    _uuid("successor_sponsor_id", "ml.face_analysis_sponsor.face_sponsor_id"),
    Column("reason", Text()),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("completed_at", TIMESTAMP(timezone=True)),
    CheckConstraint(
        "octet_length(candidate_document) BETWEEN 1 AND 131072 "
        "AND candidate_sha256=sha256(candidate_document)",
        name="ck_face_mutation_candidate_bytes",
    ),
    CheckConstraint(
        "(state='PENDING' AND successor_sponsor_id IS NULL "
        "AND reason IS NULL AND completed_at IS NULL) OR "
        "(state='APPLIED' AND successor_sponsor_id IS NOT NULL "
        "AND reason IS NULL AND completed_at IS NOT NULL) OR "
        "(state='CANCELLED' AND successor_sponsor_id IS NULL "
        "AND reason IS NOT NULL AND reason ~ '^[A-Z][A-Z0-9_]{0,63}$' "
        "AND completed_at IS NOT NULL)",
        name="ck_face_mutation_candidate_completion",
    ),
    Index(
        "ix_face_mutation_candidate_pending",
        "face_source_mutation_id",
        "predecessor_sponsor_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)


class FaceRedirectAuthorityRow(Base):
    __table__ = _authority
    recording_id: Mapped[UUID]


class FaceSourceMutationRow(Base):
    __table__ = _mutation
    face_source_mutation_id: Mapped[UUID]


class FaceSourceMutationCandidateRow(Base):
    __table__ = _candidate
    face_source_mutation_id: Mapped[UUID]
