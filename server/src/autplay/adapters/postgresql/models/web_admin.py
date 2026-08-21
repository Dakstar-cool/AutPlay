"""M6 browser authority rows; opaque values are persisted as SHA-256 only."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WebSessionInvitationRow(Base):
    __tablename__ = "web_session_invitation"
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    issuer_kind: Mapped[str] = mapped_column(Text)
    secret_sha256: Mapped[bytes] = mapped_column(BYTEA)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index(
            "ix_web_invitation_user_active",
            "user_id",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL AND cancelled_at IS NULL"),
        ),
        Index("ix_web_invitation_expiry", "expires_at"),
        {"schema": "account"},
    )


class WebLoginChallengeRow(Base):
    __tablename__ = "web_login_challenge"
    challenge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    login_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    cookie_sha256: Mapped[bytes] = mapped_column(BYTEA)
    nonce_sha256: Mapped[bytes] = mapped_column(BYTEA)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_web_login_challenge_expiry", "expires_at"),
        {"schema": "account"},
    )


class WebSessionRow(Base):
    __tablename__ = "web_session"
    web_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    token_generation: Mapped[int] = mapped_column(BigInteger)
    token_sha256: Mapped[bytes] = mapped_column(BYTEA)
    csrf_sha256: Mapped[bytes] = mapped_column(BYTEA)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    token_issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index(
            "ix_web_session_user_active",
            "user_id",
            "absolute_expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_web_session_expiry", "absolute_expires_at"),
        {"schema": "account"},
    )


class WebSessionRotationEvidenceRow(Base):
    __tablename__ = "web_session_rotation_evidence"
    evidence_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    web_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.web_session.web_session_id", ondelete="RESTRICT")
    )
    predecessor_token_sha256: Mapped[bytes] = mapped_column(BYTEA)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_web_session_rotation_evidence_expiry", "expires_at"),
        {"schema": "account"},
    )


class WebTerminalReceiptRow(Base):
    __tablename__ = "web_terminal_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    web_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.web_session.web_session_id", ondelete="RESTRICT")
    )
    token_generation: Mapped[int] = mapped_column(BigInteger)
    token_sha256: Mapped[bytes] = mapped_column(BYTEA)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    reason_code: Mapped[str | None] = mapped_column(Text)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    login_challenge_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    login_cookie_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    login_invitation_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    outcome: Mapped[str] = mapped_column(Text)
    terminal_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_web_terminal_receipt_expiry", "receipt_expires_at"),
        {"schema": "account"},
    )


class WebLoginRateWindowRow(Base):
    __tablename__ = "web_login_rate_window"
    rate_key_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index("ix_web_login_rate_window_expiry", "expires_at"), {"schema": "account"})


__all__ = (
    "WebLoginChallengeRow",
    "WebLoginRateWindowRow",
    "WebSessionInvitationRow",
    "WebSessionRotationEvidenceRow",
    "WebSessionRow",
    "WebTerminalReceiptRow",
)
