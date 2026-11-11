"""Digest-only D1 evidence; no client Journal or account metadata authority."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class DeviceCapabilityRevisionRow(Base):
    __tablename__ = "device_capability_revision"

    server_instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    identity_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    capability_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_content_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    identity_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "server_instance_id",
            "identity_epoch",
            "user_id",
            "device_id",
            name="device_capability_revision_pkey",
        ),
        ForeignKeyConstraint(
            ["server_instance_id"],
            ["account.server_instance.server_instance_id"],
            name="device_capability_revision_instance_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="device_capability_revision_device_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "identity_epoch BETWEEN 1 AND 9007199254740991",
            name="device_capability_revision_epoch_check",
        ),
        CheckConstraint(
            "capability_revision BETWEEN 1 AND 9007199254740991",
            name="device_capability_revision_value_check",
        ),
        CheckConstraint(
            "octet_length(effective_content_sha256)=32 "
            "AND octet_length(identity_thumbprint_sha256)=32 "
            "AND octet_length(device_key_thumbprint_sha256)=32",
            name="device_capability_revision_digest_check",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="device_capability_revision_time_check",
        ),
        {"schema": "account"},
    )
