"""Durable, dormant canonical Face source invalidation intent."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, Text, text
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_intent = SATable(
    "face_source_invalidation",
    Base.metadata,
    Column(
        "face_source_invalidation_id",
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    ),
    Column(
        "recording_id",
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.recording.recording_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "audio_variant_id",
        PG_UUID(as_uuid=True),
        ForeignKey("vault.audio_variant.audio_variant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("validation_status", Text(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("retired_count", BigInteger(), nullable=False, server_default=text("0")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("completed_at", TIMESTAMP(timezone=True)),
    CheckConstraint(
        "validation_status IN ('SUSPECT','INVALID','QUARANTINED')",
        name="ck_face_source_invalidation_status",
    ),
    CheckConstraint("state IN ('PENDING','COMPLETE')", name="ck_face_source_invalidation_state"),
    CheckConstraint("retired_count>=0", name="ck_face_source_invalidation_retired"),
    CheckConstraint(
        "(state='PENDING' AND completed_at IS NULL) "
        "OR (state='COMPLETE' AND completed_at IS NOT NULL)",
        name="ck_face_source_invalidation_completion",
    ),
    Index(
        "uq_face_source_invalidation_pending",
        "recording_id",
        unique=True,
        postgresql_where=text("state='PENDING'"),
    ),
    Index(
        "ix_face_source_invalidation_pending",
        "created_at",
        "face_source_invalidation_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)


class FaceSourceInvalidationRow(Base):
    __table__ = _intent

    face_source_invalidation_id: Mapped[UUID]
