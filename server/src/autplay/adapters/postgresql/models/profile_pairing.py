"""Typed rows for additive M5B profile pairing persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ServerInstanceRow(Base):
    __tablename__ = "server_instance"
    server_instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    identity_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    identity_public_key_spki: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    identity_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    label_hint: Mapped[str] = mapped_column(Text, nullable=False)
    api_origin: Mapped[str] = mapped_column(Text, nullable=False)
    stream_origin: Mapped[str] = mapped_column(Text, nullable=False)
    capability_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "account"},)


class EnrollmentInvitationRow(Base):
    __tablename__ = "enrollment_invitation"
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    issued_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    invitation_secret_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = ({"schema": "account"},)


class EnrollmentExchangeReceiptRow(Base):
    __tablename__ = "enrollment_exchange_receipt"
    exchange_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    binding_commit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "account"},)


class SessionRotationReceiptRow(Base):
    __tablename__ = "session_rotation_receipt"
    rotation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    parent_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    successor_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "account"},)


class ProfileLifecycleCommandRow(Base):
    """Durable exact result for one authenticated M5B lifecycle command."""

    __tablename__ = "profile_lifecycle_command"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "account.user_account.user_id",
            name="profile_lifecycle_command_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    actor_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_access_token_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "length(action) BETWEEN 1 AND 200", name="profile_lifecycle_command_action_check"
        ),
        CheckConstraint(
            "length(target_type) BETWEEN 1 AND 100",
            name="profile_lifecycle_command_target_type_check",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="profile_lifecycle_command_reason_code_check",
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'APPLIED', 'ALREADY_TERMINAL')",
            name="profile_lifecycle_command_outcome_check",
        ),
        {"schema": "account"},
    )


Index(
    "ix_enrollment_invitation_user_active",
    EnrollmentInvitationRow.user_id,
    EnrollmentInvitationRow.expires_at,
    postgresql_where=text("cancelled_at IS NULL AND consumed_at IS NULL"),
)

Index(
    "ix_enrollment_exchange_receipt_expiry",
    EnrollmentExchangeReceiptRow.receipt_expires_at,
)
Index(
    "ix_session_rotation_receipt_expiry",
    SessionRotationReceiptRow.receipt_expires_at,
)


__all__ = (
    "EnrollmentExchangeReceiptRow",
    "EnrollmentInvitationRow",
    "ProfileLifecycleCommandRow",
    "ServerInstanceRow",
    "SessionRotationReceiptRow",
)
