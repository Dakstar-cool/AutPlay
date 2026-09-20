"""Reversible deletion intent; final evidence lives outside the restored database."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AccountDeletionHoldRow(Base):
    __tablename__ = "account_deletion_hold"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id"), primary_key=True
    )
    reason_code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="account_deletion_hold_reason_code_check"
        ),
        {"schema": "account"},
    )


class AccountPurgeReceiptRow(Base):
    __tablename__ = "account_purge_receipt"
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    removed_rows: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint("removed_rows>=1", name="account_purge_receipt_removed_rows_check"),
        {"schema": "account"},
    )


class AccountDeletionRequestRow(Base):
    __tablename__ = "account_deletion_request"
    deletion_request_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    identity_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    actor_device_id: Mapped[UUID] = mapped_column(ForeignKey("account.device.device_id"))
    actor_public_key_spki: Mapped[bytes] = mapped_column(BYTEA)
    code_generation: Mapped[int] = mapped_column(BigInteger)
    code_verifier_sha256: Mapped[bytes] = mapped_column(BYTEA)
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    cancel_before: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    state: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # The transition trigger verifies the receipt; its normal expiry must remain possible.
    cancel_operation_id: Mapped[UUID | None] = mapped_column(unique=True)
    purge_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "actor_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="deletion_request_actor_fkey",
        ),
        CheckConstraint(
            "identity_epoch>=1 AND code_generation>=1 AND authority_generation>=2",
            name="deletion_request_versions_check",
        ),
        CheckConstraint(
            "octet_length(identity_thumbprint_sha256)=32 AND octet_length(request_sha256)=32 "
            "AND octet_length(code_verifier_sha256)=32 "
            "AND octet_length(actor_public_key_spki) BETWEEN 32 AND 256",
            name="deletion_request_hashes_check",
        ),
        CheckConstraint(
            "cancel_before=requested_at+interval '720 hours'",
            name="deletion_request_deadline_check",
        ),
        CheckConstraint(
            "(state='PENDING' AND revision=1 AND cancelled_at IS NULL "
            "AND cancel_operation_id IS NULL AND purge_started_at IS NULL) OR "
            "(state='CANCELLED' AND revision=2 AND cancelled_at IS NOT NULL AND "
            "cancelled_at>=requested_at "
            "AND cancelled_at<cancel_before AND cancel_operation_id IS NOT NULL "
            "AND purge_started_at IS NULL) OR (state='PURGING' AND revision=2 "
            "AND cancelled_at IS NULL AND cancel_operation_id IS NULL "
            "AND purge_started_at IS NOT NULL AND purge_started_at>=cancel_before)",
            name="deletion_request_state_check",
        ),
        Index(
            "ix_deletion_request_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING','PURGING')"),
        ),
        Index(
            "ix_deletion_request_deadline",
            "cancel_before",
            postgresql_where=text("state='PENDING'"),
        ),
        {"schema": "account"},
    )
