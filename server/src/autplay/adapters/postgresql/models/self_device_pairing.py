"""Bounded self-service ceremony and exact decision receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def self_ceremony_v2_check() -> str:
    """Same SQL as 0072; no v1 hash backfill or reinterpretation."""
    return (
        "\n"
        " (contract_version='v1' AND source_platform IS NULL AND recipient_platform "
        "IS NULL\n"
        "  AND source_public_key_spki IS NULL AND source_key_thumbprint_sha256 IS "
        "NULL\n"
        "  AND start_request_sha256 IS NULL AND start_original_document_sha256 IS "
        "NULL\n"
        "  AND claim_request_sha256 IS NULL AND claim_original_document_sha256 IS "
        "NULL\n"
        "  AND exchange_request_sha256 IS NULL AND exchange_original_document_sha256 "
        "IS NULL AND binding_commit_id IS NULL)\n"
        " OR COALESCE((contract_version='v2' AND source_platform IN "
        "('ANDROID','MACOS')\n"
        "  AND octet_length(source_public_key_spki)=91 AND "
        "octet_length(source_key_thumbprint_sha256)=32\n"
        "  AND octet_length(start_request_sha256)=32 AND "
        "octet_length(start_original_document_sha256)=32\n"
        "  AND app_private.valid_self_document_v2(start_document,'start')\n"
        "  AND start_document->>'platform'=source_platform AND "
        "start_document->>'ceremony_id'=ceremony_id::text\n"
        "  AND start_document->>'operation_id'=start_operation_id::text\n"
        "  AND start_document->>'expected_server_instance_id'=server_instance_id::tex"
        "t\n"
        "  AND start_document->>'request_sha256'=encode(start_request_sha256,'hex')\n"
        "  AND expires_at=created_at+interval '900 seconds'\n"
        "  AND (state<>'OPEN' OR claim_document IS NULL)\n"
        "  AND ((claim_document IS NULL AND recipient_platform IS NULL AND "
        "claim_request_sha256 IS NULL AND claim_original_document_sha256 IS NULL)\n"
        "   OR (claim_document IS NOT NULL AND recipient_platform IN "
        "('ANDROID','MACOS')\n"
        "    AND octet_length(claim_request_sha256)=32 AND "
        "octet_length(claim_original_document_sha256)=32\n"
        "    AND app_private.valid_self_document_v2(claim_document,'claim')\n"
        "    AND claim_document->>'platform'=recipient_platform AND "
        "claim_document->>'claim_id'=claim_id::text\n"
        "    AND claim_document->>'request_sha256'=encode(claim_request_sha256,'hex')"
        "\n"
        "    AND claim_document->>'ceremony_id'=ceremony_id::text\n"
        "    AND (claim_document - ARRAY['platform','requested_at','request_sha256','"
        "device_signature_b64url','claim_id','poll_secret_sha256','device_public_key_"
        "spki_b64','device_key_thumbprint_sha256','device_name','app_version'])\n"
        "      = (start_document - ARRAY['platform','requested_at','request_sha256','"
        "operation_id','rendezvous_secret_sha256'])))\n"
        "  AND ((exchange_document IS NULL AND exchange_request_sha256 IS NULL AND "
        "exchange_original_document_sha256 IS NULL AND binding_commit_id IS NULL)\n"
        "   OR (exchange_document IS NOT NULL AND "
        "octet_length(exchange_request_sha256)=32 AND "
        "octet_length(exchange_original_document_sha256)=32\n"
        "    AND app_private.valid_self_document_v2(exchange_document,'exchange')\n"
        "    AND exchange_document->>'platform'=recipient_platform AND "
        "exchange_document->>'exchange_id'=exchange_id::text\n"
        "    AND exchange_document->>'binding_commit_id'=binding_commit_id::text\n"
        "    AND exchange_document->>'request_sha256'=encode(exchange_request_sha256,"
        "'hex')\n"
        "    AND exchange_document->>'claim_id'=claim_id::text\n"
        "    AND exchange_document->>'claim_request_sha256'=encode(claim_request_sha2"
        "56,'hex')\n"
        "    AND exchange_document->>'approval_operation_id'=approval_operation_id::t"
        "ext\n"
        "    AND exchange_document->>'confirmed_account_id'=user_id::text\n"
        "    AND (exchange_document - ARRAY['platform','requested_at','request_sha256"
        "','device_signature_b64url','exchange_id','binding_commit_id','claim_id','cl"
        "aim_request_sha256','approval_operation_id','confirmed_account_id','next_ref"
        "resh_token_sha256'])\n"
        "      = (start_document - ARRAY['platform','requested_at','request_sha256','"
        "operation_id','rendezvous_secret_sha256'])))),false)\n"
    )


def self_command_v2_check() -> str:
    return (
        "\n"
        " (contract_version='v1' AND source_platform IS NULL AND original_document "
        "IS NULL AND original_document_sha256 IS NULL)\n"
        " OR COALESCE((contract_version='v2' AND source_platform IN "
        "('ANDROID','MACOS')\n"
        "  AND octet_length(original_document_sha256)=32 AND "
        "app_private.valid_self_document_v2(original_document,'decision')\n"
        "  AND original_document->>'operation_id'=operation_id::text AND "
        "original_document->>'ceremony_id'=ceremony_id::text\n"
        "  AND original_document->>'platform'=source_platform AND "
        "original_document->>'request_sha256'=encode(request_hash,'hex')\n"
        "  AND app_private.valid_self_document_v2(result,'status') AND "
        "result->>'ceremony_id'=ceremony_id::text\n"
        "  AND result->>'source_platform'=source_platform),false)\n"
    )


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
    contract_version: Mapped[str] = mapped_column(Text, server_default=text("'v1'"))
    source_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_public_key_spki: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    source_key_thumbprint_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    start_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    start_original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    claim_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    claim_original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    exchange_request_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    exchange_original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    binding_commit_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
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
        CheckConstraint(self_ceremony_v2_check(), name="self_pairing_v2_evidence_check"),
        Index(
            "self_pairing_v2_binding_commit",
            "binding_commit_id",
            unique=True,
            postgresql_where=text("contract_version='v2'"),
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
    contract_version: Mapped[str] = mapped_column(Text, server_default=text("'v1'"))
    source_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_document: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
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
        CheckConstraint(self_command_v2_check(), name="self_pairing_command_v2_evidence_check"),
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
