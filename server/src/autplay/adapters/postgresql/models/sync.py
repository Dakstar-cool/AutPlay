"""Typed SQLAlchemy mappings for the sync PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Identity,
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


class DeviceEventInboxRow(Base):
    """Persistence row for ``sync.device_event_inbox``."""

    __tablename__ = "device_event_inbox"

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_sequence: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    aggregate_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    aggregate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    aggregate_local_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    base_server_row_version: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    payload: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    apply_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'RECEIVED'"),
    )
    error_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    request_hash: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    terminal_ack: Mapped[JsonValue | None] = mapped_column(JSONB(), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("event_id", name="device_event_inbox_pkey"),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="device_event_inbox_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="device_event_inbox_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "device_sequence >= 1",
            name="device_event_inbox_device_sequence_check",
        ),
        CheckConstraint(
            "length(event_type) BETWEEN 1 AND 200",
            name="device_event_inbox_event_type_check",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="device_event_inbox_schema_version_check",
        ),
        CheckConstraint(
            "length(aggregate_type) BETWEEN 1 AND 100",
            name="device_event_inbox_aggregate_type_check",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_device_event_inbox_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("device_id", "device_sequence", name="uq_device_event_sequence"),
        CheckConstraint(
            "apply_status IN ('RECEIVED', 'APPLIED', 'DUPLICATE', 'CONFLICT', 'REJECTED')",
            name="ck_device_event_apply_status",
        ),
        CheckConstraint(
            "octet_length(request_hash) = 32",
            name="ck_device_event_request_hash_len",
        ),
        {"schema": "sync"},
    )


class SyncEventRow(Base):
    """Persistence row for ``sync.sync_event``."""

    __tablename__ = "sync_event"

    server_sequence: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    origin_device_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    aggregate_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    aggregate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    payload: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    operation: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'UPSERT'"))
    server_row_version: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("server_sequence", name="sync_event_pkey"),
        UniqueConstraint("event_id", name="sync_event_event_id_key"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="sync_event_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_device_id"],
            ["account.device.device_id"],
            name="sync_event_origin_device_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(event_type) BETWEEN 1 AND 200",
            name="sync_event_event_type_check",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="sync_event_schema_version_check",
        ),
        CheckConstraint(
            "length(aggregate_type) BETWEEN 1 AND 100",
            name="sync_event_aggregate_type_check",
        ),
        UniqueConstraint("user_id", "event_id", name="uq_sync_event_user_event"),
        ForeignKeyConstraint(
            ["user_id", "origin_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_sync_event_origin_owner",
            ondelete="RESTRICT",
        ),
        {"schema": "sync"},
    )


class DeviceSyncCursorRow(Base):
    """Persistence row for ``sync.device_sync_cursor``."""

    __tablename__ = "device_sync_cursor"

    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    last_pulled_server_sequence: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("0"),
    )
    last_acked_device_sequence: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    journal_epoch: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("device_id", name="device_sync_cursor_pkey"),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="device_sync_cursor_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="device_sync_cursor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "last_pulled_server_sequence >= 0",
            name="device_sync_cursor_last_pulled_server_sequence_check",
        ),
        CheckConstraint(
            "last_acked_device_sequence >= 0",
            name="device_sync_cursor_last_acked_device_sequence_check",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_device_sync_cursor_owner",
            ondelete="RESTRICT",
        ),
        {"schema": "sync"},
    )


class TombstoneRow(Base):
    """Persistence row for ``sync.tombstone``."""

    __tablename__ = "tombstone"

    tombstone_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    aggregate_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    aggregate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    deleted_by_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    deleted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    retain_until: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("tombstone_id", name="tombstone_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="tombstone_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(aggregate_type) BETWEEN 1 AND 100",
            name="tombstone_aggregate_type_check",
        ),
        ForeignKeyConstraint(
            ["deleted_by_event_id"],
            ["sync.sync_event.event_id"],
            name="tombstone_deleted_by_event_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "deleted_by_event_id"],
            ["sync.sync_event.user_id", "sync.sync_event.event_id"],
            name="fk_tombstone_event_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "user_id", "aggregate_type", "aggregate_id", name="uq_tombstone_aggregate"
        ),
        CheckConstraint(
            "retain_until > deleted_at",
            name="ck_tombstone_retention",
        ),
        {"schema": "sync"},
    )


class IdempotencyRecordRow(Base):
    """Persistence row for ``sync.idempotency_record``."""

    __tablename__ = "idempotency_record"

    scope: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    request_hash: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    response_code: Mapped[int | None] = mapped_column(
        Integer(),
        nullable=True,
    )
    response_reference: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'IN_PROGRESS'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "length(scope) BETWEEN 1 AND 300",
            name="idempotency_record_scope_check",
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 300",
            name="idempotency_record_idempotency_key_check",
        ),
        PrimaryKeyConstraint("scope", "idempotency_key", name="idempotency_record_pkey"),
        CheckConstraint(
            "octet_length(request_hash) = 32",
            name="ck_idempotency_request_hash_len",
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="ck_idempotency_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_idempotency_expiry",
        ),
        {"schema": "sync"},
    )


Index(
    "ix_device_event_inbox_pending",
    DeviceEventInboxRow.received_at,
    DeviceEventInboxRow.event_id,
    postgresql_where=text("apply_status = 'RECEIVED'"),
)


class BootstrapSessionRow(Base):
    """A materialized, bounded bootstrap snapshot cursor."""

    __tablename__ = "bootstrap_session"

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    journal_epoch: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("ARRAY[]::text[]")
    )
    high_water_server_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "sync"},)


class BootstrapSnapshotItemRow(Base):
    """Immutable materialized item in one bootstrap session."""

    __tablename__ = "bootstrap_snapshot_item"
    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(BigInteger(), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(Text(), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    server_row_version: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    payload: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    __table_args__ = ({"schema": "sync"},)


class UserInteractionEventRow(Base):
    """Idempotent projection of P04 specialized interaction envelopes."""

    __tablename__ = "user_interaction_event"

    interaction_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text(), nullable=False)
    recommendation_request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    recording_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_rank: Mapped[int | None] = mapped_column(Integer())
    presentation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    impression_interaction_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    payload: Mapped[JsonValue] = mapped_column(JSONB(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "library"},)


Index("ix_bootstrap_session_expiry", BootstrapSessionRow.expires_at)


Index(
    "ix_sync_event_user_sequence",
    SyncEventRow.user_id,
    SyncEventRow.server_sequence,
)

Index(
    "ix_device_sync_cursor_user",
    DeviceSyncCursorRow.user_id,
    DeviceSyncCursorRow.updated_at,
)

Index(
    "ix_tombstone_retention",
    TombstoneRow.retain_until,
)

Index(
    "ix_idempotency_record_expiry",
    IdempotencyRecordRow.expires_at,
)


__all__ = (
    "BootstrapSessionRow",
    "BootstrapSnapshotItemRow",
    "DeviceEventInboxRow",
    "DeviceSyncCursorRow",
    "IdempotencyRecordRow",
    "SyncEventRow",
    "TombstoneRow",
    "UserInteractionEventRow",
)
