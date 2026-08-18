"""Typed mappings for the durable Wave schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    Integer,
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


class WaveRoomRow(Base):
    __tablename__ = "room"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    room_code_sha256: Mapped[bytes] = mapped_column(BYTEA())
    host_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    host_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text())
    playback_state: Mapped[str] = mapped_column(Text())
    room_epoch: Mapped[int] = mapped_column(BigInteger())
    queue_version: Mapped[int] = mapped_column(BigInteger())
    timeline_position_ms: Mapped[int] = mapped_column(BigInteger())
    timeline_recording_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    command_sequence: Mapped[int] = mapped_column(BigInteger())
    timeline_effective_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    host_lost_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_wave_room_expiry", "expires_at", postgresql_where=text("closed_at IS NULL")),
        {"schema": "wave"},
    )


class WaveMemberRow(Base):
    __tablename__ = "member"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text())
    joined_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_present_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "device_id"),
        Index("ix_wave_member_presence", "room_id", "last_present_at"),
        {"schema": "wave"},
    )


class WaveInvitationRow(Base):
    __tablename__ = "invitation"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "user_id"),
        {"schema": "wave"},
    )


class WaveQueueEntryRow(Base):
    __tablename__ = "queue_entry"
    queue_entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    position: Mapped[int] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint("queue_entry_id", "room_id"),
        {"schema": "wave"},
    )


class WaveCommandRow(Base):
    __tablename__ = "command"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    command_sequence: Mapped[int] = mapped_column(BigInteger())
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    actor_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(Text())
    request_sha256: Mapped[bytes] = mapped_column(BYTEA())
    expected_queue_version: Mapped[int] = mapped_column(BigInteger())
    expected_sequence: Mapped[int] = mapped_column(BigInteger())
    command_kind: Mapped[str] = mapped_column(Text())
    command_document: Mapped[JsonValue] = mapped_column(JSONB())
    effective_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "command_sequence"),
        {"schema": "wave"},
    )


class WavePreflightRow(Base):
    __tablename__ = "preflight"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    queue_entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    queue_version: Mapped[int] = mapped_column(BigInteger())
    availability: Mapped[str] = mapped_column(Text())
    final_ready: Mapped[bool] = mapped_column(Boolean())
    source_checked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "device_id", "queue_entry_id"),
        Index("ix_wave_preflight_room_recording", "room_id", "recording_id", "expires_at"),
        {"schema": "wave"},
    )


class WaveTimingReportRow(Base):
    __tablename__ = "timing_report"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    command_sequence: Mapped[int] = mapped_column(BigInteger())
    rtt_ms: Mapped[int] = mapped_column(Integer())
    offset_ms: Mapped[int] = mapped_column(Integer())
    uncertainty_ms: Mapped[int] = mapped_column(Integer())
    start_skew_ms: Mapped[int | None] = mapped_column(Integer())
    drift_ms: Mapped[int | None] = mapped_column(Integer())
    reported_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "device_id", "command_sequence"),
        {"schema": "wave"},
    )
