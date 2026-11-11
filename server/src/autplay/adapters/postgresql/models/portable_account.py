"""Additive portable identity, one-use challenges and normalized account records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def portable_v2_evidence_check() -> str:
    """Closed hash-only ROOT projection; migration keeps an independent frozen copy."""
    fields = (
        "'purpose','contract_version','schema_version','platform','domain',"
        "'signature_algorithm','signature_encoding','request_sha256',"
        "'original_document_sha256','root_proof_sha256','account_id','public_id',"
        "'root_spki_hex','root_thumbprint_sha256','device_spki_hex',"
        "'device_thumbprint_sha256','binding_commit_id','next_refresh_token_sha256',"
        "'device_name','app_version','account_role'"
    )
    receipt_fields = (
        "'contract_version','schema_version','platform','account_id','device_id',"
        "'session_id','session_family_id','generation','binding_commit_id','token_id',"
        "'issued_at','access_expires_at','refresh_expires_at'"
    )
    challenge_fields = (
        "'contract_version','schema_version','platform','challenge_id','purpose','account_id',"
        "'document_sha256','server_instance_id','identity_epoch','identity_thumbprint_sha256',"
        "'api_origin','stream_origin','nonce','expires_at','actor_device_id','actor_session_id',"
        "'action','operation_id','target_device_id'"
    )
    return (
        "(contract_version='v1' AND platform IS NULL AND original_document_sha256 IS NULL "
        "AND root_proof_sha256 IS NULL AND original_request_evidence IS NULL "
        "AND binding_commit_id IS NULL AND authority_generation IS NULL AND root_revision IS NULL) "
        "OR COALESCE((contract_version='v2' AND platform IN ('ANDROID','MACOS') "
        "AND purpose IN ('REGISTER','LOGIN') AND actor_device_id IS "
        "NULL AND actor_session_id IS NULL "
        "AND document->>'contract_version'='v2' AND document->'schema_version'='2'::jsonb "
        "AND document->>'platform'=platform AND document->>'purpose'=purpose "
        "AND document->>'challenge_id'=challenge_id::text AND "
        "document->>'account_id'=account_id::text "
        f"AND document ?& ARRAY[{challenge_fields}] "
        f"AND document - ARRAY[{challenge_fields}]='{{}}'::jsonb "
        "AND document->'actor_device_id'='null'::jsonb AND "
        "document->'actor_session_id'='null'::jsonb "
        "AND document->'action'='null'::jsonb AND document->'operation_id'='null'::jsonb "
        "AND document->'target_device_id'='null'::jsonb "
        "AND document->>'document_sha256' ~ '^[0-9a-f]{64}$' "
        "AND document->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND document->>'nonce' ~ '^[A-Za-z0-9_-]{43}$' "
        "AND jsonb_typeof(document->'api_origin')='string' "
        "AND jsonb_typeof(document->'stream_origin')='string' "
        "AND jsonb_typeof(document->'server_instance_id')='string' "
        "AND jsonb_typeof(document->'identity_epoch')='number' "
        "AND document->>'identity_epoch' ~ '^[1-9][0-9]{0,15}$' "
        "AND jsonb_typeof(document->'expires_at')='string' "
        "AND expires_at=created_at+interval '120 seconds' AND ("
        "(receipt IS NULL AND request_hash IS NULL AND receipt_expires_at IS NULL "
        "AND original_document_sha256 IS NULL AND root_proof_sha256 IS NULL "
        "AND original_request_evidence IS NULL AND binding_commit_id IS NULL "
        "AND authority_generation IS NULL AND root_revision IS NULL) OR "
        "(receipt IS NOT NULL AND request_hash IS NOT NULL AND receipt_expires_at IS NOT NULL "
        "AND octet_length(original_document_sha256)=32 AND octet_length(root_proof_sha256)=32 "
        "AND binding_commit_id IS NOT NULL AND authority_generation>=1 AND root_revision>=1 "
        "AND jsonb_typeof(original_request_evidence)='object' "
        "AND octet_length(original_request_evidence::text)<=8192 "
        f"AND original_request_evidence ?& ARRAY[{fields}] "
        f"AND original_request_evidence - ARRAY[{fields}]='{{}}'::jsonb "
        "AND original_request_evidence->>'purpose'=purpose "
        "AND original_request_evidence->>'contract_version'='v2' "
        "AND original_request_evidence->'schema_version'='2'::jsonb "
        "AND original_request_evidence->>'platform'=platform "
        "AND original_request_evidence->>'account_id'=account_id::text "
        "AND original_request_evidence->>'binding_commit_id'=binding_commit_id::text "
        "AND original_request_evidence->>'request_sha256'=encode(request_hash,'hex') "
        "AND original_request_evidence->>'original_document_sha256'=enco"
        "de(original_document_sha256,'hex') "
        "AND original_request_evidence->>'root_proof_sha256'=encode(root_proof_sha256,'hex') "
        "AND original_request_evidence->>'signature_algorithm'='ECDSA-P256-SHA256' "
        "AND original_request_evidence->>'signature_encoding'='DER/standard-padded-base64' "
        "AND original_request_evidence->>'domain'=E'autplay:portable-acc"
        "ount:'||lower(purpose)||E':v2\\n' "
        "AND original_request_evidence->>'root_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'device_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'root_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'device_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'next_refresh_token_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'public_id' ~ '^[a-z0-9_]{3,24}$' "
        "AND original_request_evidence->>'account_role' IN ('USER','ADMIN','OWNER') "
        "AND jsonb_typeof(original_request_evidence->'device_name')='string' "
        "AND length(original_request_evidence->>'device_name') BETWEEN 1 AND 120 "
        "AND jsonb_typeof(original_request_evidence->'app_version')='string' "
        "AND length(original_request_evidence->>'app_version') BETWEEN 1 AND 32 "
        "AND jsonb_typeof(receipt)='object' AND octet_length(receipt::text)<=4096 "
        f"AND receipt ?& ARRAY[{receipt_fields}] "
        f"AND receipt - ARRAY[{receipt_fields}]='{{}}'::jsonb "
        "AND receipt->>'contract_version'='v2' AND receipt->'schema_version'='2'::jsonb "
        "AND receipt->>'platform'=platform AND receipt->>'account_id'=account_id::text "
        "AND receipt->>'binding_commit_id'=binding_commit_id::text "
        "AND receipt->>'session_family_id'=receipt->>'session_id' "
        "AND receipt->>'session_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]"
        "{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND receipt->>'device_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{"
        "4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND receipt->>'token_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4"
        "}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND jsonb_typeof(receipt->'issued_at')='string' AND length(receipt->>'issued_at')<=30 "
        "AND jsonb_typeof(receipt->'access_expires_at')='string' AND "
        "length(receipt->>'access_expires_at')<=30 "
        "AND jsonb_typeof(receipt->'refresh_expires_at')='string' AND "
        "length(receipt->>'refresh_expires_at')<=30 "
        "AND receipt->'generation'='0'::jsonb))),false)"
    )


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
    receipt: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    receipt_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    contract_version: Mapped[str] = mapped_column(Text(), server_default=text("'v1'"))
    platform: Mapped[str | None] = mapped_column(Text(), nullable=True)
    original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    root_proof_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    original_request_evidence: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    binding_commit_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    authority_generation: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    root_revision: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
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
        CheckConstraint(portable_v2_evidence_check(), name="portable_challenge_v2_evidence_check"),
        Index("portable_challenge_source_time", "source_key", "created_at"),
        Index("portable_challenge_expiry", "expires_at"),
        Index("portable_challenge_binding_commit", "binding_commit_id", unique=True),
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
