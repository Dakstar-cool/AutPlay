"""Bounded self-service ceremony and exact decision receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class SelfDevicePairingRow(Base):
    __tablename__ = "self_device_pairing"
    ceremony_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    source_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    source_family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    start_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    start_document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    claim_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    claim_document: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    approval_operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    exchange_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    exchange_document: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    result_device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    receipt_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "source_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="self_pairing_source_owner_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id", "result_device_id", "result_session_id"],
            [
                "account.user_session.user_id",
                "account.user_session.device_id",
                "account.user_session.session_id",
            ],
            name="self_pairing_result_owner_fkey",
        ),
        CheckConstraint(
            "authority_generation >= 1 AND revision >= 1", name="self_pairing_versions_check"
        ),
        CheckConstraint(
            "state IN ('OPEN','CLAIMED','APPROVED','EXCHANGED','CANCELLED','REJECTED')",
            name="self_pairing_state_check",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '15 minutes'",
            name="self_pairing_expiry_check",
        ),
        CheckConstraint(
            "jsonb_typeof(start_document)='object' AND octet_length(start_document::text)<=8192",
            name="self_pairing_start_document_check",
        ),
        CheckConstraint(
            "claim_document IS NULL OR (jsonb_typeof(claim_document)='object' "
            "AND octet_length(claim_document::text)<=8192)",
            name="self_pairing_claim_document_check",
        ),
        CheckConstraint(
            "exchange_document IS NULL OR (jsonb_typeof(exchange_document)='object' "
            "AND octet_length(exchange_document::text)<=8192)",
            name="self_pairing_exchange_document_check",
        ),
        CheckConstraint(
            "(claim_id IS NULL) = (claim_document IS NULL)", name="self_pairing_claim_shape_check"
        ),
        CheckConstraint(
            "state NOT IN ('CLAIMED','APPROVED','EXCHANGED','REJECTED') OR claim_id IS NOT NULL",
            name="self_pairing_claim_state_check",
        ),
        CheckConstraint(
            "state NOT IN ('APPROVED','EXCHANGED') OR approval_operation_id IS NOT NULL",
            name="self_pairing_approval_check",
        ),
        CheckConstraint(
            "(state='EXCHANGED' AND exchange_id IS NOT NULL AND exchange_document IS NOT NULL "
            "AND result_device_id IS NOT NULL AND result_session_id IS NOT NULL "
            "AND receipt_expires_at IS NOT NULL) OR (state<>'EXCHANGED' AND exchange_id IS NULL "
            "AND exchange_document IS NULL AND result_device_id IS NULL "
            "AND result_session_id IS NULL AND receipt_expires_at IS NULL)",
            name="self_pairing_exchange_shape_check",
        ),
        Index("ix_self_pairing_user_expiry", "user_id", "expires_at"),
        Index("ix_self_pairing_expiry", "expires_at"),
        {"schema": "account"},
    )


class SelfPairingCommandRow(Base):
    __tablename__ = "self_pairing_command"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    ceremony_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.self_device_pairing.ceremony_id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    request_hash: Mapped[bytes] = mapped_column(BYTEA)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="self_pairing_command_actor_fkey",
        ),
        CheckConstraint("octet_length(request_hash)=32", name="self_pairing_command_hash_check"),
        CheckConstraint(
            "jsonb_typeof(result)='object' AND octet_length(result::text)<=8192",
            name="self_pairing_command_result_check",
        ),
        Index("ix_self_pairing_command_ceremony", "ceremony_id"),
        {"schema": "account"},
    )


class SelfPairingRateRow(Base):
    __tablename__ = "self_pairing_rate"
    key_hash: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    count: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint("octet_length(key_hash)=32 AND count >= 1", name="self_pairing_rate_check"),
        Index("ix_self_pairing_rate_expiry", "expires_at"),
        {"schema": "account"},
    )
