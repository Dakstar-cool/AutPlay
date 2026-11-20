"""Typed metadata for the dormant Face projection tombstone outbox."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)

_outbox = SATable(
    "face_projection_tombstone_outbox",
    Base.metadata,
    Column("outbox_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "owner_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "server_profile_id",
        _UUID,
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT"),
    ),
    Column(
        "face_projection_id",
        _UUID,
        ForeignKey("ml.face_projection.face_projection_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("projection_generation", BigInteger(), nullable=False),
    Column("reason_code", Text(), nullable=False),
    Column("tombstoned_at", _TIME, nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("attempt_count", Integer(), nullable=False, server_default=text("0")),
    Column("claim_token", _UUID),
    Column("lease_expires_at", _TIME),
    Column("next_retry_at", _TIME),
    Column("delivered_at", _TIME),
    Column("created_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    UniqueConstraint(
        "owner_user_id",
        "face_projection_id",
        "projection_generation",
        name="uq_face_tombstone_transition",
    ),
    CheckConstraint(
        "projection_generation BETWEEN 1 AND 9007199254740991",
        name="face_projection_tombstone_outbox_projection_generation_check",
    ),
    CheckConstraint(
        "reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
        name="face_projection_tombstone_outbox_reason_code_check",
    ),
    CheckConstraint(
        "state IN ('PENDING','CLAIMED','DELIVERED')",
        name="face_projection_tombstone_outbox_state_check",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 5",
        name="face_projection_tombstone_outbox_attempt_count_check",
    ),
    CheckConstraint(
        "(state='PENDING' AND claim_token IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NULL) OR "
        "(state='CLAIMED' AND server_profile_id IS NOT NULL AND claim_token IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL) OR "
        "(state='DELIVERED' AND server_profile_id IS NOT NULL AND claim_token IS NULL "
        "AND lease_expires_at IS NULL AND delivered_at IS NOT NULL)",
        name="ck_face_tombstone_delivery_shape",
    ),
    Index(
        "ix_face_tombstone_claim",
        "created_at",
        "outbox_id",
        postgresql_where=text("state<>'DELIVERED' AND attempt_count<5"),
    ),
    Index(
        "ix_face_tombstone_owner",
        "owner_user_id",
        "face_projection_id",
        "projection_generation",
    ),
    schema="ml",
)


class FaceProjectionTombstoneOutboxRow(Base):
    __table__ = _outbox

    outbox_id: Mapped[UUID]


_audit = SATable(
    "face_projection_tombstone_audit",
    Base.metadata,
    Column("audit_id", BigInteger(), Identity(always=True), primary_key=True),
    Column(
        "outbox_id",
        _UUID,
        ForeignKey("ml.face_projection_tombstone_outbox.outbox_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("action", Text(), nullable=False),
    Column("attempt_count", Integer(), nullable=False),
    Column("claim_token", _UUID),
    Column("occurred_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    CheckConstraint(
        "action IN ('ENQUEUED','CLAIMED','RETRIED','DELIVERED')",
        name="face_projection_tombstone_audit_action_check",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 5",
        name="face_projection_tombstone_audit_attempt_count_check",
    ),
    Index("ix_face_tombstone_audit_outbox", "outbox_id", "audit_id"),
    schema="ml",
)


class FaceProjectionTombstoneAuditRow(Base):
    __table__ = _audit

    audit_id: Mapped[int]
