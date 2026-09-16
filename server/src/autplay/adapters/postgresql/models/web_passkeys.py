"""WebAuthn public credentials and bounded one-time ceremony evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WebPasskeyRow(Base):
    __tablename__ = "web_passkey"
    passkey_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    user_handle: Mapped[bytes] = mapped_column(BYTEA)
    credential_id: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    public_key: Mapped[bytes] = mapped_column(BYTEA)
    sign_count: Mapped[int] = mapped_column(BigInteger)
    backup_eligible: Mapped[bool] = mapped_column(Boolean)
    backed_up: Mapped[bool] = mapped_column(Boolean)
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_web_passkey_user", "user_id"),
        CheckConstraint("octet_length(user_handle)=32", name="web_passkey_user_handle_check"),
        CheckConstraint(
            "octet_length(credential_id) BETWEEN 1 AND 1024", name="web_passkey_credential_id_check"
        ),
        CheckConstraint(
            "octet_length(public_key) BETWEEN 1 AND 2048", name="web_passkey_public_key_check"
        ),
        CheckConstraint("sign_count BETWEEN 0 AND 4294967295", name="web_passkey_sign_count_check"),
        CheckConstraint("NOT backed_up OR backup_eligible", name="web_passkey_check"),
        CheckConstraint("length(label) BETWEEN 1 AND 80", name="web_passkey_label_check"),
        {"schema": "account"},
    )


class WebPasskeyCeremonyRow(Base):
    __tablename__ = "web_passkey_ceremony"
    ceremony_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    purpose: Mapped[str] = mapped_column(Text)
    challenge: Mapped[bytes] = mapped_column(BYTEA)
    binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    origin: Mapped[str] = mapped_column(Text)
    rp_id: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    user_handle: Mapped[bytes | None] = mapped_column(BYTEA)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("account.user_account.user_id"))
    web_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    token_generation: Mapped[int | None] = mapped_column(BigInteger)
    completed_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    result_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        Index("ix_web_passkey_ceremony_expiry", "expires_at"),
        Index("ix_web_passkey_ceremony_user", "user_id", "expires_at"),
        CheckConstraint(
            "purpose IN ('REGISTER','LOGIN')", name="web_passkey_ceremony_purpose_check"
        ),
        CheckConstraint("octet_length(challenge)=32", name="web_passkey_ceremony_challenge_check"),
        CheckConstraint(
            "octet_length(binding_sha256)=32", name="web_passkey_ceremony_binding_sha256_check"
        ),
        CheckConstraint(
            "length(origin) BETWEEN 1 AND 2048", name="web_passkey_ceremony_origin_check"
        ),
        CheckConstraint("length(rp_id) BETWEEN 1 AND 253", name="web_passkey_ceremony_rp_id_check"),
        CheckConstraint(
            "octet_length(user_handle)=32", name="web_passkey_ceremony_user_handle_check"
        ),
        CheckConstraint("token_generation>=0", name="web_passkey_ceremony_token_generation_check"),
        CheckConstraint(
            "octet_length(completed_request_sha256)=32",
            name="web_passkey_ceremony_completed_request_sha256_check",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '5 minutes'",
            name="web_passkey_ceremony_check",
        ),
        CheckConstraint(
            "(purpose='REGISTER' AND user_id IS NOT NULL AND user_handle IS NOT NULL "
            "AND web_session_id IS NOT NULL AND token_generation IS NOT NULL) "
            "OR (purpose='LOGIN' AND user_id IS NULL AND user_handle IS NULL "
            "AND web_session_id IS NULL AND token_generation IS NULL)",
            name="web_passkey_ceremony_check1",
        ),
        CheckConstraint(
            "(consumed_at IS NULL AND completed_request_sha256 IS NULL AND result_id IS NULL) "
            "OR (consumed_at IS NOT NULL AND completed_request_sha256 IS NOT NULL "
            "AND result_id IS NOT NULL)",
            name="web_passkey_ceremony_check2",
        ),
        {"schema": "account"},
    )


class WebPasskeyRevocationRow(Base):
    __tablename__ = "web_passkey_revocation"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    passkey_id: Mapped[UUID] = mapped_column(ForeignKey("account.web_passkey.passkey_id"))
    actor_binding: Mapped[bytes] = mapped_column(BYTEA)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(actor_binding)=32", name="web_passkey_revocation_actor_binding_check"
        ),
        {"schema": "account"},
    )
