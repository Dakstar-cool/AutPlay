"""Typed rows for additive M5B profile pairing persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
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


class DeviceAdmissionRow(Base):
    """One targetless, exact-key admission request; secrets are hash-only."""

    __tablename__ = "device_admission"
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    server_instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    identity_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    identity_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_public_key_spki: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    device_model_hint: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)
    api_major: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    review_locator_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    review_binding_hash: Mapped[bytes | None] = mapped_column(BYTEA)
    review_web_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account.web_session.web_session_id", ondelete="RESTRICT")
    )
    poll_bearer_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    last_poll_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    secret_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    recovery_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_recovery_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    approved_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    enrolled_device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account.device.device_id", ondelete="RESTRICT")
    )
    enrolled_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account.user_session.session_id", ondelete="RESTRICT")
    )
    decision_action: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(request_sha256) = 32", name="device_admission_request_sha256_check"
        ),
        CheckConstraint("identity_epoch >= 1", name="device_admission_identity_epoch_check"),
        CheckConstraint(
            "octet_length(identity_thumbprint_sha256) = 32",
            name="device_admission_identity_thumbprint_sha256_check",
        ),
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256) = 32",
            name="device_admission_device_key_thumbprint_sha256_check",
        ),
        CheckConstraint(
            "length(nickname) BETWEEN 1 AND 120", name="device_admission_nickname_check"
        ),
        CheckConstraint(
            "device_model_hint IS NULL OR length(device_model_hint) <= 96",
            name="device_admission_device_model_hint_check",
        ),
        CheckConstraint("platform = 'ANDROID'", name="device_admission_platform_check"),
        CheckConstraint(
            "length(app_version) BETWEEN 1 AND 32", name="device_admission_app_version_check"
        ),
        CheckConstraint("api_major = 1", name="device_admission_api_major_check"),
        CheckConstraint(
            "state IN ('PENDING', 'APPROVED', 'REJECTED', 'BLOCKED', 'EXPIRED', "
            "'CANCELLED', 'EXCHANGED')",
            name="device_admission_state_check",
        ),
        CheckConstraint(
            "octet_length(review_locator_hash) = 32",
            name="device_admission_review_locator_hash_check",
        ),
        CheckConstraint(
            "review_binding_hash IS NULL OR octet_length(review_binding_hash) = 32",
            name="device_admission_review_binding_hash_check",
        ),
        CheckConstraint(
            "octet_length(poll_bearer_hash) = 32", name="device_admission_poll_bearer_hash_check"
        ),
        CheckConstraint(
            "secret_generation BETWEEN 1 AND 4", name="device_admission_secret_generation_check"
        ),
        CheckConstraint(
            "recovery_count BETWEEN 0 AND 3", name="device_admission_recovery_count_check"
        ),
        CheckConstraint(
            "decision_action IS NULL OR decision_action IN ('APPROVE_ONCE', 'TRUST_DEVICE', "
            "'REJECT', 'BLOCK_DEVICE')",
            name="device_admission_decision_action_check",
        ),
        CheckConstraint("expires_at > created_at", name="ck_device_admission_expiry"),
        CheckConstraint(
            "(state IN ('PENDING', 'EXPIRED', 'CANCELLED') AND approved_user_id IS NULL) "
            "OR state NOT IN ('PENDING', 'EXPIRED', 'CANCELLED')",
            name="ck_device_admission_decision",
        ),
        Index(
            "uq_device_admission_pending_key",
            "device_key_thumbprint_sha256",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
        Index(
            "uq_device_admission_locator",
            "review_locator_hash",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
        Index("ix_device_admission_poll_expiry", "poll_bearer_hash", "expires_at"),
        Index(
            "ix_device_admission_cleanup",
            "expires_at",
            postgresql_where=text("state IN ('PENDING', 'APPROVED')"),
        ),
        {"schema": "account"},
    )


class DeviceAdmissionNonceRow(Base):
    """Hash-only replay evidence bounded by the parent admission lifetime."""

    __tablename__ = "device_admission_nonce"
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.device_admission.request_id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    nonce_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("scope IN ('POLL', 'RECOVERY')", name="device_admission_nonce_scope_check"),
        CheckConstraint(
            "octet_length(nonce_sha256) = 32",
            name="device_admission_nonce_nonce_sha256_check",
        ),
        {"schema": "account"},
    )


class TrustedDeviceKeyRow(Base):
    __tablename__ = "trusted_device_key"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"), primary_key=True
    )
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    device_public_key_spki: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    approved_request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    key_reference: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256) = 32",
            name="trusted_device_key_device_key_thumbprint_sha256_check",
        ),
        CheckConstraint("revision >= 1", name="trusted_device_key_revision_check"),
        UniqueConstraint("key_reference", name="trusted_device_key_key_reference_key"),
        {"schema": "account"},
    )


class DeviceKeyBlockRow(Base):
    __tablename__ = "device_key_block"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"), primary_key=True
    )
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    blocked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    unblocked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256) = 32",
            name="device_key_block_device_key_thumbprint_sha256_check",
        ),
        Index(
            "ix_device_key_block_active",
            "device_key_thumbprint_sha256",
            postgresql_where=text("unblocked_at IS NULL"),
        ),
        {"schema": "account"},
    )


class TrustedDeviceReenrollmentChallengeRow(Base):
    __tablename__ = "trusted_device_reenrollment_challenge"
    challenge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"), nullable=False
    )
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    client_nonce_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    challenge_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256) = 32",
            name="trusted_device_reenrollment__device_key_thumbprint_sha256_check",
        ),
        CheckConstraint(
            "octet_length(request_sha256) = 32",
            name="trusted_device_reenrollment_challenge_request_sha256_check",
        ),
        CheckConstraint(
            "octet_length(client_nonce_sha256) = 32",
            name="trusted_device_reenrollment_challenge_client_nonce_sha256_check",
        ),
        CheckConstraint(
            "octet_length(challenge_hash) = 32",
            name="trusted_device_reenrollment_challenge_challenge_hash_check",
        ),
        Index("ix_trusted_reenrollment_challenge_expiry", "expires_at"),
        {"schema": "account"},
    )


class DeviceAdmissionExchangeReceiptRow(Base):
    __tablename__ = "device_admission_exchange_receipt"
    exchange_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    request_or_challenge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.device.device_id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_session.session_id", ondelete="RESTRICT"), nullable=False
    )
    binding_commit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "octet_length(request_sha256) = 32",
            name="device_admission_exchange_receipt_request_sha256_check",
        ),
        CheckConstraint(
            "octet_length(device_key_thumbprint_sha256) = 32",
            name="device_admission_exchange_re_device_key_thumbprint_sha256_check",
        ),
        Index("ix_device_admission_receipt_expiry", "receipt_expires_at"),
        {"schema": "account"},
    )


class DeviceAdmissionWebOperationReceiptRow(Base):
    """One exact M6 operation result; locator values are retained only as hashes."""

    __tablename__ = "device_admission_web_operation_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"), nullable=False
    )
    web_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.web_session.web_session_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    target_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    terminal_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "length(action) BETWEEN 1 AND 80",
            name="device_admission_web_operation_receipt_action_check",
        ),
        CheckConstraint(
            "octet_length(target_sha256) = 32",
            name="device_admission_web_operation_receipt_target_sha256_check",
        ),
        CheckConstraint(
            "octet_length(request_sha256) = 32",
            name="device_admission_web_operation_receipt_request_sha256_check",
        ),
        Index("ix_device_admission_web_operation_receipt_expiry", "receipt_expires_at"),
        {"schema": "account"},
    )


class DeviceAdmissionRateWindowRow(Base):
    """Bounded opaque submit throttle state; source addresses are never persisted."""

    __tablename__ = "device_admission_rate_window"
    rate_key_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "octet_length(rate_key_sha256) = 32",
            name="device_admission_rate_window_rate_key_sha256_check",
        ),
        CheckConstraint(
            "scope IN ('KEY_DAY', 'SOURCE_15M', 'TRUSTED_KEY_15M', 'TRUSTED_ACCOUNT_15M')",
            name="device_admission_rate_window_scope_check",
        ),
        CheckConstraint(
            "attempt_count >= 1", name="device_admission_rate_window_attempt_count_check"
        ),
        CheckConstraint(
            "expires_at > window_started_at", name="device_admission_rate_window_check"
        ),
        Index("ix_device_admission_rate_window_expiry", "expires_at"),
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
    "DeviceAdmissionExchangeReceiptRow",
    "DeviceAdmissionNonceRow",
    "DeviceAdmissionRateWindowRow",
    "DeviceAdmissionRow",
    "DeviceAdmissionWebOperationReceiptRow",
    "DeviceKeyBlockRow",
    "EnrollmentExchangeReceiptRow",
    "EnrollmentInvitationRow",
    "ProfileLifecycleCommandRow",
    "ServerInstanceRow",
    "SessionRotationReceiptRow",
    "TrustedDeviceKeyRow",
    "TrustedDeviceReenrollmentChallengeRow",
)
