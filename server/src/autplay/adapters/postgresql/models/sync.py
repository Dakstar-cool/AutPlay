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
    journal_epoch: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
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
    ingress_validation_version: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("1")
    )
    normalized_payload_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)

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
        UniqueConstraint(
            "device_id", "journal_epoch", "device_sequence", name="uq_device_event_sequence"
        ),
        CheckConstraint(
            "apply_status IN ('RECEIVED', 'APPLIED', 'DUPLICATE', 'CONFLICT', "
            "'REJECTED', 'ERASED')",
            name="ck_device_event_apply_status",
        ),
        CheckConstraint(
            "octet_length(request_hash) = 32",
            name="ck_device_event_request_hash_len",
        ),
        CheckConstraint("ingress_validation_version IN (1,2)", name="ck_inbox_ingress_version"),
        CheckConstraint(
            "(ingress_validation_version=1 AND normalized_payload_sha256 IS NULL) OR "
            "(ingress_validation_version=2 AND normalized_payload_sha256 IS NOT NULL "
            "AND octet_length(normalized_payload_sha256)=32 AND payload='{}'::jsonb "
            "AND schema_version=1 AND device_sequence BETWEEN 1 AND 9007199254740991 "
            "AND ((apply_status='RECEIVED' AND terminal_ack IS NULL AND error_code IS NULL) "
            "OR (apply_status='APPLIED' AND terminal_ack IS NOT NULL AND error_code IS NULL) "
            "OR (apply_status='ERASED' AND terminal_ack IS NOT NULL "
            "AND error_code='EVENT_ERASED')))",
            name="ck_inbox_normalized_ingress",
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
    ingress_validation_version: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("1")
    )
    ingress_device_sequence: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    ingress_journal_epoch: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    normalized_payload_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
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
        CheckConstraint(
            "ingress_validation_version IN (1,2)", name="ck_sync_event_ingress_version"
        ),
        CheckConstraint(
            "(ingress_validation_version=1 AND ingress_device_sequence IS NULL "
            "AND ingress_journal_epoch IS NULL AND normalized_payload_sha256 IS NULL) OR "
            "(ingress_validation_version=2 AND origin_device_id IS NOT NULL "
            "AND ingress_device_sequence IS NOT NULL "
            "AND ingress_device_sequence BETWEEN 1 AND 9007199254740991 "
            "AND ingress_journal_epoch IS NOT NULL AND normalized_payload_sha256 IS NOT NULL "
            "AND octet_length(normalized_payload_sha256)=32 AND schema_version=1 "
            "AND operation='UPSERT' AND server_row_version IS NOT NULL AND server_row_version=1)",
            name="ck_sync_event_normalized_ingress",
        ),
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


class OwnerErasureFloorRow(Base):
    """Highest expired typed erasure sequence retained without event identity."""

    __tablename__ = "owner_erasure_floor"
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    floor_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ledger_sequence: Mapped[int | None] = mapped_column(BigInteger())
    ledger_digest: Mapped[bytes | None] = mapped_column(BYTEA())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="owner_erasure_floor_user_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint("floor_sequence > 0", name="ck_owner_erasure_floor_positive"),
        CheckConstraint(
            "(ledger_sequence IS NULL AND ledger_digest IS NULL) OR "
            "(ledger_sequence > 0 AND octet_length(ledger_digest)=32)",
            name="ck_owner_erasure_floor_receipt",
        ),
        {"schema": "sync"},
    )


class DeviceErasureFloorRow(Base):
    """Old epoch fence and explicit wipe/bootstrap state for a lagging device."""

    __tablename__ = "device_erasure_floor"
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    floor_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ledger_sequence: Mapped[int | None] = mapped_column(BigInteger())
    ledger_digest: Mapped[bytes | None] = mapped_column(BYTEA())
    invalidated_epoch: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    replacement_epoch: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    bootstrap_completed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_device_erasure_floor_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("floor_sequence > 0", name="ck_device_erasure_floor_positive"),
        CheckConstraint(
            "(ledger_sequence IS NULL AND ledger_digest IS NULL) OR "
            "(ledger_sequence > 0 AND octet_length(ledger_digest)=32)",
            name="ck_device_erasure_floor_receipt",
        ),
        CheckConstraint(
            "replacement_epoch IS NULL OR replacement_epoch IS DISTINCT FROM invalidated_epoch",
            name="ck_device_erasure_floor_new_epoch",
        ),
        CheckConstraint(
            "NOT bootstrap_completed OR replacement_epoch IS NOT NULL",
            name="ck_device_erasure_floor_bootstrap_epoch",
        ),
        {"schema": "sync"},
    )


class DeviceErasureInvalidatedEpochRow(Base):
    """Permanent per-device journal epochs forbidden by signed floor receipts."""

    __tablename__ = "device_erasure_invalidated_epoch"
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    journal_epoch: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    floor_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ledger_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ledger_digest: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    invalidated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_device_erasure_invalidated_epoch_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "floor_sequence > 0", name="device_erasure_invalidated_epoch_floor_sequence_check"
        ),
        CheckConstraint(
            "ledger_sequence > 0", name="device_erasure_invalidated_epoch_ledger_sequence_check"
        ),
        CheckConstraint(
            "octet_length(ledger_digest)=32",
            name="device_erasure_invalidated_epoch_ledger_digest_check",
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
    projection_event_type: Mapped[str | None] = mapped_column(Text(), nullable=True)
    projection_schema_version: Mapped[int | None] = mapped_column(Integer(), nullable=True)

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
        CheckConstraint(
            "(projection_event_type IS NULL AND projection_schema_version IS NULL) OR "
            "(projection_event_type IN ('RECOMMENDATION_INTERACTION_ERASED',"
            "'LISTENING_EVENT_ERASED') AND projection_schema_version=2)",
            name="ck_sona_erasure_tombstone_projection",
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
    attribution_contract_version: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("1")
    )
    attribution_closed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    __table_args__ = (
        CheckConstraint(
            "attribution_contract_version IN (1,2)", name="ck_interaction_attribution_version"
        ),
        CheckConstraint(
            "attribution_contract_version=2 OR NOT attribution_closed",
            name="ck_interaction_attribution_closed",
        ),
        CheckConstraint(
            "attribution_contract_version=1 OR "
            "(recording_id IS NOT NULL "
            "AND presentation_id IS NOT NULL AND recommendation_request_id IS NULL "
            "AND source_rank IS NULL AND impression_interaction_id IS NULL "
            "AND ((event_type='RECOMMENDATION_IMPRESSION_RECORDED' "
            'AND payload=\'{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}\'::jsonb) '
            "OR (event_type='RECOMMENDATION_FEEDBACK_RECORDED' AND payload IN ("
            '\'{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED",'
            '"feedback_type":"SELECTED"}\'::jsonb,'
            '\'{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED",'
            '"feedback_type":"DISMISSED"}\'::jsonb))))',
            name="ck_interaction_attribution_v2",
        ),
        UniqueConstraint(
            "user_id", "interaction_id", "recording_id", name="uq_interaction_owner_recording"
        ),
        {"schema": "library"},
    )


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
    "ix_device_erasure_floor_owner",
    DeviceErasureFloorRow.user_id,
    DeviceErasureFloorRow.device_id,
)

Index(
    "ix_device_erasure_invalidated_epoch_owner",
    DeviceErasureInvalidatedEpochRow.user_id,
    DeviceErasureInvalidatedEpochRow.device_id,
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
    "DeviceErasureFloorRow",
    "DeviceErasureInvalidatedEpochRow",
    "DeviceEventInboxRow",
    "DeviceSyncCursorRow",
    "IdempotencyRecordRow",
    "OwnerErasureFloorRow",
    "SyncEventRow",
    "TombstoneRow",
    "UserInteractionEventRow",
)
