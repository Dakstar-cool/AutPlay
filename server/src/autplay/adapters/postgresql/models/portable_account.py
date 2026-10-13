"""Additive portable identity, one-use challenges and normalized account records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PortableAccountRow(Base):
    __tablename__ = "portable_identity"
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    public_key: Mapped[bytes] = mapped_column(BYTEA())
    key_thumbprint: Mapped[bytes] = mapped_column(BYTEA())
    document: Mapped[dict[str, object]] = mapped_column(JSONB())
    revision: Mapped[int] = mapped_column(BigInteger())
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(public_key)=91 AND octet_length(key_thumbprint)=32",
            name="portable_identity_key_check",
        ),
        CheckConstraint(
            "revision>=1 AND jsonb_typeof(document)='object' "
            "AND octet_length(document::text)<=4096",
            name="portable_identity_document_check",
        ),
        {"schema": "account"},
    )


class PortableAccountChallengeRow(Base):
    __tablename__ = "portable_challenge"
    challenge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    purpose: Mapped[str] = mapped_column(Text())
    source_key: Mapped[bytes] = mapped_column(BYTEA())
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    actor_device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    document: Mapped[dict[str, object]] = mapped_column(JSONB())
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    request_hash: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    receipt: Mapped[dict[str, object] | None] = mapped_column(JSONB(), nullable=True)
    receipt_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('REGISTER','LOGIN','CLAIM','REAUTH','BACKUP','PASSWORD_CHANGE','ROTATE')",
            name="portable_challenge_purpose_check",
        ),
        CheckConstraint(
            "octet_length(source_key)=32 "
            "AND (request_hash IS NULL OR octet_length(request_hash)=32)",
            name="portable_challenge_hash_check",
        ),
        CheckConstraint(
            "expires_at>created_at AND expires_at<=created_at+interval '5 minutes'",
            name="portable_challenge_expiry_check",
        ),
        CheckConstraint(
            "jsonb_typeof(document)='object' AND octet_length(document::text)<=16384",
            name="portable_challenge_document_check",
        ),
        Index("portable_challenge_source_time", "source_key", "created_at"),
        Index("portable_challenge_expiry", "expires_at"),
        {"schema": "account"},
    )


class PortableAccountRecordRow(Base):
    __tablename__ = "portable_record"
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    record_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text())
    document: Mapped[dict[str, object]] = mapped_column(JSONB())
    content_hash: Mapped[bytes] = mapped_column(BYTEA())
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "kind IN ('LISTENING','PREFERENCE','PLAYLIST','PLAYLIST_ENTRY','TASTE')",
            name="portable_record_kind_check",
        ),
        CheckConstraint(
            "octet_length(content_hash)=32 AND jsonb_typeof(document)='object' "
            "AND octet_length(document::text)<=16384",
            name="portable_record_document_check",
        ),
        Index("portable_record_account_cursor", "user_id", "updated_at", "record_id"),
        Index(
            "portable_record_listening_aliases",
            text("(document->'event_ids')"),
            postgresql_using="gin",
        ),
        {"schema": "account"},
    )
