"""Typed mappings for the non-activating GPU admission authority."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)


_device = SATable(
    "gpu_device_authority",
    Base.metadata,
    Column("device_uuid", _UUID, primary_key=True),
    Column("reviewed_total_vram_bytes", BigInteger(), nullable=False),
    Column("safety_margin_bytes", BigInteger(), nullable=False),
    Column("reserved_sona_vram_bytes", BigInteger(), nullable=False),
    Column("generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("face_cancellation_generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "reviewed_total_vram_bytes BETWEEN 1 AND 9007199254740991",
        name="gpu_device_authority_reviewed_total_vram_bytes_check",
    ),
    CheckConstraint(
        "safety_margin_bytes>=1073741824", name="gpu_device_authority_safety_margin_bytes_check"
    ),
    CheckConstraint(
        "reserved_sona_vram_bytes>0", name="gpu_device_authority_reserved_sona_vram_bytes_check"
    ),
    CheckConstraint("generation>=0", name="gpu_device_authority_generation_check"),
    CheckConstraint(
        "face_cancellation_generation>=0",
        name="gpu_device_authority_face_cancellation_generation_check",
    ),
    CheckConstraint(
        "safety_margin_bytes*10>=reviewed_total_vram_bytes",
        name="gpu_device_authority_check",
    ),
    CheckConstraint(
        "safety_margin_bytes+reserved_sona_vram_bytes<reviewed_total_vram_bytes",
        name="gpu_device_authority_check1",
    ),
    schema="ml",
)


class GpuDeviceAuthorityRow(Base):
    __table__ = _device


_current = SATable(
    "gpu_reservation_current",
    Base.metadata,
    Column(
        "device_uuid",
        _UUID,
        ForeignKey("ml.gpu_device_authority.device_uuid", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("reservation_kind", Text(), primary_key=True),
    Column("reservation_generation", BigInteger(), nullable=False),
    Column("authority_generation", BigInteger(), nullable=False),
    Column("holder_id", _UUID, nullable=False),
    Column("model_identity_sha256", BYTEA(), nullable=False),
    Column("priority", Integer(), nullable=False),
    Column("requested_vram_bytes", BigInteger(), nullable=False),
    Column("measured_vram_bytes", BigInteger(), nullable=False),
    Column("cancellation_generation", BigInteger(), nullable=False),
    Column("state", Text(), nullable=False),
    Column("acquired_at", _TIME, nullable=False),
    Column("heartbeat_at", _TIME, nullable=False),
    Column("lease_until", _TIME, nullable=False),
    Column("released_at", _TIME),
    CheckConstraint(
        "reservation_kind IN ('FACE','SONA')", name="gpu_reservation_current_reservation_kind_check"
    ),
    CheckConstraint(
        "reservation_generation>=1", name="gpu_reservation_current_reservation_generation_check"
    ),
    CheckConstraint(
        "authority_generation>=1", name="gpu_reservation_current_authority_generation_check"
    ),
    CheckConstraint(
        "octet_length(model_identity_sha256)=32",
        name="gpu_reservation_current_model_identity_sha256_check",
    ),
    CheckConstraint("priority IN (10,100)", name="gpu_reservation_current_priority_check"),
    CheckConstraint(
        "requested_vram_bytes>0", name="gpu_reservation_current_requested_vram_bytes_check"
    ),
    CheckConstraint(
        "measured_vram_bytes>=0", name="gpu_reservation_current_measured_vram_bytes_check"
    ),
    CheckConstraint(
        "cancellation_generation>=0", name="gpu_reservation_current_cancellation_generation_check"
    ),
    CheckConstraint(
        "state IN ('ACTIVE','CANCELLED','RELEASED','EXPIRED')",
        name="gpu_reservation_current_state_check",
    ),
    CheckConstraint(
        "(state IN ('ACTIVE','CANCELLED') AND released_at IS NULL) OR "
        "(state IN ('RELEASED','EXPIRED') AND released_at IS NOT NULL)",
        name="gpu_reservation_current_check",
    ),
    CheckConstraint(
        "(reservation_kind='FACE' AND priority=10) OR (reservation_kind='SONA' AND priority=100)",
        name="gpu_reservation_current_check1",
    ),
    Index(
        "ix_gpu_current_lease",
        "lease_until",
        postgresql_where=text("state IN ('ACTIVE','CANCELLED')"),
    ),
    schema="ml",
)


class GpuReservationCurrentRow(Base):
    __table__ = _current


_receipt = SATable(
    "gpu_admission_receipt",
    Base.metadata,
    Column("receipt_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "device_uuid",
        _UUID,
        ForeignKey("ml.gpu_device_authority.device_uuid", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("reservation_kind", Text(), nullable=False),
    Column("event_kind", Text(), nullable=False),
    Column("authority_generation", BigInteger(), nullable=False),
    Column("reservation_generation", BigInteger(), nullable=False),
    Column("holder_id", _UUID, nullable=False),
    Column("model_identity_sha256", BYTEA(), nullable=False),
    Column("priority", Integer(), nullable=False),
    Column("requested_vram_bytes", BigInteger(), nullable=False),
    Column("measured_vram_bytes", BigInteger(), nullable=False),
    Column("cancellation_generation", BigInteger(), nullable=False),
    Column("lease_until", _TIME, nullable=False),
    Column("nvml_release_confirmed", Boolean(), nullable=False, server_default=text("false")),
    Column("process_exit_confirmed", Boolean(), nullable=False, server_default=text("false")),
    Column("session_unloaded", Boolean(), nullable=False, server_default=text("false")),
    Column("release_process_id", BigInteger()),
    Column("release_boot_id", _UUID),
    Column("release_start_ticks", BigInteger()),
    Column("release_pid_namespace_inode", BigInteger()),
    Column("recorded_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    UniqueConstraint("device_uuid", "authority_generation"),
    CheckConstraint(
        "reservation_kind IN ('FACE','SONA')", name="gpu_admission_receipt_reservation_kind_check"
    ),
    CheckConstraint(
        "event_kind IN ('ACQUIRED','HEARTBEAT','CANCEL_FACE','RELEASED','EXPIRED')",
        name="gpu_admission_receipt_event_kind_check",
    ),
    CheckConstraint(
        "authority_generation>=1", name="gpu_admission_receipt_authority_generation_check"
    ),
    CheckConstraint(
        "reservation_generation>=1", name="gpu_admission_receipt_reservation_generation_check"
    ),
    CheckConstraint(
        "octet_length(model_identity_sha256)=32",
        name="gpu_admission_receipt_model_identity_sha256_check",
    ),
    CheckConstraint("priority IN (10,100)", name="gpu_admission_receipt_priority_check"),
    CheckConstraint(
        "requested_vram_bytes>0", name="gpu_admission_receipt_requested_vram_bytes_check"
    ),
    CheckConstraint(
        "measured_vram_bytes>=0", name="gpu_admission_receipt_measured_vram_bytes_check"
    ),
    CheckConstraint(
        "cancellation_generation>=0", name="gpu_admission_receipt_cancellation_generation_check"
    ),
    CheckConstraint(
        "(reservation_kind='FACE' AND priority=10) OR (reservation_kind='SONA' AND priority=100)",
        name="gpu_admission_receipt_check",
    ),
    CheckConstraint(
        "measured_vram_bytes<=requested_vram_bytes", name="gpu_admission_receipt_check1"
    ),
    CheckConstraint(
        "(release_process_id IS NULL AND release_boot_id IS NULL AND "
        "release_start_ticks IS NULL AND release_pid_namespace_inode IS NULL) OR "
        "(release_process_id IS NOT NULL AND release_process_id BETWEEN 1 AND 4294967295 "
        "AND release_boot_id IS NOT NULL AND release_start_ticks IS NOT NULL "
        "AND release_start_ticks>0 AND release_pid_namespace_inode IS NOT NULL "
        "AND release_pid_namespace_inode>0)",
        name="ck_gpu_receipt_process_identity",
    ),
    Index("ix_gpu_receipt_device_time", "device_uuid", text("recorded_at DESC")),
    schema="ml",
)


class GpuAdmissionReceiptRow(Base):
    __table__ = _receipt


_sona_binding = SATable(
    "gpu_sona_process_binding",
    Base.metadata,
    Column(
        "device_uuid",
        _UUID,
        ForeignKey("ml.gpu_device_authority.device_uuid", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("reservation_generation", BigInteger(), primary_key=True),
    Column("holder_id", _UUID, nullable=False),
    Column("model_identity_sha256", BYTEA(), nullable=False),
    Column("process_id", BigInteger(), nullable=False),
    Column("boot_id", _UUID, nullable=False),
    Column("start_ticks", BigInteger(), nullable=False),
    Column("pid_namespace_inode", BigInteger(), nullable=False),
    Column("bound_at", _TIME, nullable=False, server_default=text("clock_timestamp()")),
    UniqueConstraint(
        "device_uuid", "boot_id", "process_id", "start_ticks", name="uq_gpu_sona_process_identity"
    ),
    CheckConstraint("reservation_generation>=1", name="ck_gpu_sona_binding_generation"),
    CheckConstraint(
        "octet_length(model_identity_sha256)=32", name="ck_gpu_sona_binding_model_hash"
    ),
    CheckConstraint("process_id BETWEEN 1 AND 4294967295", name="ck_gpu_sona_binding_pid"),
    CheckConstraint("start_ticks>0", name="ck_gpu_sona_binding_start"),
    CheckConstraint("pid_namespace_inode>0", name="ck_gpu_sona_binding_namespace"),
    schema="ml",
)


class GpuSonaProcessBindingRow(Base):
    __table__ = _sona_binding
