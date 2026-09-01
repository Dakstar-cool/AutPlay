"""PA2 invite-only account provisioning persistence (all bearer evidence is hash-only)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AccountInvitationRow(Base):
    """A one-time, owner-issued bearer for first-account registration."""

    __tablename__ = "account_invitation"
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    secret_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(secret_sha256)=32", name="account_invitation_secret_hash_check"
        ),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 120", name="account_invitation_name_check"
        ),
        CheckConstraint("expires_at > issued_at", name="account_invitation_expiry_check"),
        Index("ix_account_invitation_expiry", "expires_at"),
        {"schema": "account"},
    )


class AccountRegistrationReceiptRow(Base):
    """Exact registration retry identity; no invitation secret or token is persisted."""

    __tablename__ = "account_registration_receipt"
    registration_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    invitation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.account_invitation.invitation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    invitation_secret_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.device.device_id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_session.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    binding_commit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "octet_length(invitation_secret_sha256)=32",
            name="account_registration_secret_hash_check",
        ),
        CheckConstraint(
            "octet_length(request_sha256)=32", name="account_registration_request_hash_check"
        ),
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256)=32",
            name="account_registration_key_hash_check",
        ),
        CheckConstraint(
            "receipt_expires_at > created_at", name="account_registration_receipt_expiry_check"
        ),
        Index("ix_account_registration_receipt_expiry", "receipt_expires_at"),
        {"schema": "account"},
    )


class AccountProvisioningLinkRow(Base):
    """Immutable, narrow relation permitting owner lifecycle actions only."""

    __tablename__ = "account_provisioning_link"
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.account_invitation.invitation_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = ({"schema": "account"},)


class AccountProvisioningOperationReceiptRow(Base):
    """Exact owner command receipt, deliberately separate from M5 lifecycle receipts."""

    __tablename__ = "account_provisioning_operation_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    command_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "action IN ('CREATE','CANCEL','DISABLE')",
            name="account_provisioning_operation_action_check",
        ),
        CheckConstraint(
            "octet_length(command_sha256)=32", name="account_provisioning_operation_hash_check"
        ),
        CheckConstraint(
            "length(result_json) <= 4096", name="account_provisioning_operation_result_check"
        ),
        {"schema": "account"},
    )


class AccountProvisioningRateWindowRow(Base):
    """Persistent failed-attempt counter; source values are already HMAC-reduced."""

    __tablename__ = "account_provisioning_rate_window"
    rate_key_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "octet_length(rate_key_sha256)=32", name="account_provisioning_rate_key_check"
        ),
        CheckConstraint(
            "scope IN ('ISSUE_OWNER','REDEEM_INVITATION','REDEEM_SOURCE','REDEEM_SERVER')",
            name="account_provisioning_rate_scope_check",
        ),
        CheckConstraint("attempt_count >= 1", name="account_provisioning_rate_attempt_check"),
        CheckConstraint(
            "expires_at > window_started_at", name="account_provisioning_rate_expiry_check"
        ),
        Index("ix_account_provisioning_rate_expiry", "expires_at"),
        {"schema": "account"},
    )


__all__ = (
    "AccountInvitationRow",
    "AccountProvisioningLinkRow",
    "AccountProvisioningOperationReceiptRow",
    "AccountProvisioningRateWindowRow",
    "AccountRegistrationReceiptRow",
)
