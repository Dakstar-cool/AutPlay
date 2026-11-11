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
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
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
    issued_by_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    invitation_secret_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = ({"schema": "account"},)


class _DeviceProfileReceiptEvidence:
    """Historical v1 has no invented platform or reconstructed original evidence."""

    contract_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    platform: Mapped[str | None] = mapped_column(Text)
    original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    original_request_evidence: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    authority_generation: Mapped[int | None] = mapped_column(BigInteger)


def _profile_evidence_constraint(operation: str, name: str) -> CheckConstraint:
    enrollment = operation == "enrollment.exchange"
    credential = "invitation_secret_sha256" if enrollment else "current_refresh_token_sha256"
    keys = (
        "'operation','contract_version','schema_version','platform','server_instance_id','identity_epoch',"
        "'identity_thumbprint_sha256','api_origin','stream_origin','device_public_key_spki_hex',"
        "'device_key_thumbprint_sha256','matched_body_key','domain','signature_algorithm','proof_digest',"
        "'original_document_sha256','signature_hex','fields','credential_hashes'"
    )
    field_names = (
        (
            "exchange_id",
            "binding_commit_id",
            "invitation_id",
            "expected_user_id",
            "device_name",
            "app_version",
            "next_refresh_token_sha256",
            "client_nonce_b64url",
            "request_sha256",
        )
        if enrollment
        else (
            "rotation_id",
            "device_id",
            "parent_session_id",
            "current_generation",
            "next_refresh_token_sha256",
            "request_sha256",
        )
    )
    fields = ",".join(f"'{field}'" for field in field_names)
    scalar_types = (
        "".join(
            f"AND jsonb_typeof(original_request_evidence->'{field}')='string' "
            for field in (
                "operation",
                "contract_version",
                "platform",
                "server_instance_id",
                "identity_thumbprint_sha256",
                "api_origin",
                "stream_origin",
                "device_public_key_spki_hex",
                "device_key_thumbprint_sha256",
                "domain",
                "signature_algorithm",
                "proof_digest",
                "original_document_sha256",
                "signature_hex",
            )
        )
        + "AND jsonb_typeof(original_request_evidence->'identity_epoch')='number' "
    )
    field_types = "".join(
        f"AND jsonb_typeof(original_request_evidence->'fields'->'{field}')="
        f"'{'number' if field == 'current_generation' else 'string'}' "
        for field in field_names
    )
    return CheckConstraint(
        "(contract_version = 'v1' AND platform IS NULL "
        "AND original_document_sha256 IS NULL AND original_request_evidence IS NULL "
        "AND authority_generation IS NULL) OR COALESCE((contract_version = 'v2' "
        "AND platform IN ('ANDROID', 'MACOS') "
        "AND octet_length(original_document_sha256) = 32 "
        "AND authority_generation >= 1 "
        "AND jsonb_typeof(original_request_evidence) = 'object' "
        "AND octet_length(original_request_evidence::text) <= 16384 "
        f"AND original_request_evidence->>'operation' = '{operation}' "
        "AND original_request_evidence->>'contract_version' = 'v2' "
        "AND original_request_evidence->'schema_version' = '2'::jsonb "
        "AND original_request_evidence->>'platform' = platform "
        "AND original_request_evidence->>'original_document_sha256' = "
        "encode(original_document_sha256,'hex') "
        "AND original_request_evidence->>'proof_digest' = encode(request_sha256,'hex') "
        "AND original_request_evidence->>'device_key_thumbprint_sha256' "
        "= encode(device_key_thumbprint_sha256,'hex') "
        "AND original_request_evidence->>'signature_algorithm' = 'ES256-P1363' "
        "AND original_request_evidence->>'signature_hex' ~ '^[0-9a-f]{128}$' "
        f"AND original_request_evidence ?& ARRAY[{keys}] "
        f"AND original_request_evidence - ARRAY[{keys}] = '{{}}'::jsonb "
        f"{scalar_types}"
        f"AND original_request_evidence->>'domain' = E'autplay:device-compatibility:{
            operation.replace(('.'), (':'))
        }:v2\\n' "
        f"AND original_request_evidence->'matched_body_key' = '{
            ('true') if enrollment else ('false')
        }'::jsonb "
        "AND original_request_evidence->>'device_public_key_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND jsonb_typeof(original_request_evidence->'fields') = 'object' "
        f"AND (original_request_evidence->'fields') ?& ARRAY[{fields}] "
        f"AND (original_request_evidence->'fields') - ARRAY[{fields}] = '{{}}'::jsonb "
        f"{field_types}"
        "AND jsonb_typeof(original_request_evidence->'credential_hashes') = 'object' "
        f"AND (original_request_evidence->'credential_hashes') - '{credential}' = '{{}}'::jsonb "
        "AND original_request_evidence->'credential_hashes'->>"
        f"'{credential}' ~ '^[0-9a-f]{{64}}$'), false)",
        name=name,
    )


class EnrollmentExchangeReceiptRow(_DeviceProfileReceiptEvidence, Base):
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
    __table_args__ = (
        _profile_evidence_constraint(
            "enrollment.exchange", "enrollment_receipt_contract_evidence_check"
        ),
        {"schema": "account"},
    )


class SessionRotationReceiptRow(_DeviceProfileReceiptEvidence, Base):
    __tablename__ = "session_rotation_receipt"
    rotation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    parent_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    successor_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = (
        _profile_evidence_constraint("session.rotate", "rotation_receipt_contract_evidence_check"),
        {"schema": "account"},
    )


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


class _AdmissionOriginalEvidence:
    contract_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    original_request_evidence: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True)
    )


def _admission_evidence_constraint(operations: tuple[str, ...], name: str) -> CheckConstraint:
    """Closed storage projection, including the challenge hash rather than its bearer."""
    keys = (
        "'operation','contract_version','schema_version','platform','server_instance_id','identity_epoch',"
        "'identity_thumbprint_sha256','api_origin','stream_origin','device_public_key_spki_hex',"
        "'device_key_thumbprint_sha256','matched_body_key','domain','signature_algorithm','proof_digest',"
        "'original_document_sha256','signature_hex','fields','credential_hashes'"
    )
    # Match the frozen 0071 safe_evidence scalar types as well as its key names.
    fields = {
        "admission.submit": (
            ("request_id", "string"),
            ("client_nonce_b64url", "string"),
            ("api_major", "number"),
            ("nickname", "string"),
            ("app_version", "string"),
            ("requested_at", "string"),
        ),
        "trusted.challenge": (
            ("challenge_request_id", "string"),
            ("account_id", "string"),
            ("client_nonce_b64url", "string"),
        ),
        "admission.exchange": (
            ("request_id", "string"),
            ("request_sha256", "string"),
            ("exchange_id", "string"),
            ("binding_commit_id", "string"),
            ("poll_bearer_sha256", "string"),
            ("approved_account_id", "string"),
            ("client_nonce_b64url", "string"),
            ("next_refresh_token_sha256", "string"),
        ),
        "trusted.exchange": (
            ("challenge_id", "string"),
            ("exchange_id", "string"),
            ("binding_commit_id", "string"),
            ("account_id", "string"),
            ("next_refresh_token_sha256", "string"),
            ("client_nonce_b64url", "string"),
        ),
    }
    variants = []
    for operation in operations:
        required = ",".join(f"'{field}'" for field, _ in fields[operation])
        allowed = required + (",'device_model_hint'" if operation == "admission.submit" else "")
        scalar_types = "".join(
            f"AND jsonb_typeof(original_request_evidence->'fields'->'{field}')='{kind}' "
            for field, kind in fields[operation]
        )
        if operation == "admission.submit":
            scalar_types += (
                "AND original_request_evidence->'fields'->'api_major'='1'::jsonb "
                "AND (NOT (original_request_evidence->'fields' ? 'device_model_hint') "
                "OR jsonb_typeof(original_request_evidence->'fields'->'device_mo"
                "del_hint') IN ('string','null')) "
            )
        credentials = (
            "AND original_request_evidence->'credential_hashes' ? 'challenge_sha256' "
            "AND (original_request_evidence->'credential_hashes') - 'challenge_sha256' "
            "= '{}'::jsonb "
            "AND original_request_evidence->'credential_hashes'->>'challenge"
            "_sha256' ~ '^[0-9a-f]{64}$' "
            if operation == "trusted.exchange"
            else "AND original_request_evidence->'credential_hashes' = '{}'::jsonb "
        )
        variants.append(
            f"(original_request_evidence->>'operation' = '{operation}' "
            f"AND original_request_evidence->>'domain' = E'autplay:device-compatibility:{
                operation.replace(('.'), (':'))
            }:v2\\n' "
            f"AND (original_request_evidence->'fields') ?& ARRAY[{required}] "
            f"AND (original_request_evidence->'fields') - ARRAY[{allowed}] = '{{}}'::jsonb "
            f"{scalar_types}{credentials})"
        )
    return CheckConstraint(
        "(contract_version='v1' AND original_document_sha256 IS NULL "
        "AND original_request_evidence IS NULL) "
        "OR COALESCE((contract_version='v2' AND platform IN ('ANDROID','MACOS') "
        "AND octet_length(original_document_sha256)=32 "
        "AND jsonb_typeof(original_request_evidence)='object' "
        "AND octet_length(original_request_evidence::text)<=16384 "
        f"AND original_request_evidence ?& ARRAY[{keys}] "
        f"AND original_request_evidence - ARRAY[{keys}]='{{}}'::jsonb "
        "AND original_request_evidence->>'contract_version'='v2' "
        "AND original_request_evidence->'schema_version'='2'::jsonb "
        "AND original_request_evidence->>'platform'=platform "
        "AND jsonb_typeof(original_request_evidence->'server_instance_id')='string' "
        "AND jsonb_typeof(original_request_evidence->'identity_epoch')='number' "
        "AND jsonb_typeof(original_request_evidence->'api_origin')='string' "
        "AND jsonb_typeof(original_request_evidence->'stream_origin')='string' "
        "AND original_request_evidence->>'original_document_sha256'=enco"
        "de(original_document_sha256,'hex') "
        "AND original_request_evidence->>'proof_digest'=encode(request_sha256,'hex') "
        "AND original_request_evidence->>'device_key_thumbprint_sha256'="
        "encode(device_key_thumbprint_sha256,'hex') "
        "AND original_request_evidence->>'device_public_key_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'signature_algorithm'='ES256-P1363' "
        "AND original_request_evidence->>'signature_hex' ~ '^[0-9a-f]{128}$' "
        "AND original_request_evidence->'matched_body_key'='true'::jsonb "
        "AND jsonb_typeof(original_request_evidence->'fields')='object' "
        "AND jsonb_typeof(original_request_evidence->'credential_hashes')='object' "
        "AND (" + " OR ".join(variants) + ")),false)",
        name=name,
    )


class DeviceAdmissionRow(_AdmissionOriginalEvidence, Base):
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
    approved_device_name: Mapped[str | None] = mapped_column(Text)
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
    approved_authority_generation: Mapped[int | None] = mapped_column(BigInteger)
    approved_account_role: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        _admission_evidence_constraint(("admission.submit",), "admission_v2_evidence_check"),
        CheckConstraint(
            "COALESCE(((contract_version='v1' AND "
            "approved_authority_generation IS NULL AND "
            "approved_account_role IS NULL) OR "
            "(contract_version='v2' AND expires_at=created_at+interval '900 seconds' "
            "AND ((approved_user_id IS NULL AND "
            "approved_authority_generation IS NULL AND "
            "approved_account_role IS NULL) "
            "OR (approved_user_id IS NOT NULL AND "
            "approved_authority_generation>=1 AND approved_account_role IN "
            "('OWNER','ADMIN','USER'))))),false)",
            name="admission_v2_authority_check",
        ),
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
            "approved_device_name IS NULL OR length(approved_device_name) BETWEEN 1 AND 120",
            name="device_admission_approved_name_check",
        ),
        CheckConstraint(
            "device_model_hint IS NULL OR length(device_model_hint) <= 96",
            name="device_admission_device_model_hint_check",
        ),
        CheckConstraint("platform IN ('ANDROID', 'MACOS')", name="device_admission_platform_check"),
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
    contract_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    platform: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "COALESCE(((contract_version='v1' AND platform IS NULL) OR "
            "(contract_version='v2' AND platform IN ('ANDROID','MACOS'))),false)",
            name="trusted_key_v2_platform_check",
        ),
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


class TrustedDeviceReenrollmentChallengeRow(_AdmissionOriginalEvidence, Base):
    __tablename__ = "trusted_device_reenrollment_challenge"
    challenge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"), nullable=False
    )
    device_key_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    client_nonce_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    challenge_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    platform: Mapped[str | None] = mapped_column(Text)
    authority_generation: Mapped[int | None] = mapped_column(BigInteger)
    trust_revision: Mapped[int | None] = mapped_column(BigInteger)
    account_role: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        _admission_evidence_constraint(
            ("trusted.challenge",), "trusted_challenge_v2_evidence_check"
        ),
        CheckConstraint(
            "COALESCE(((contract_version='v1' AND platform IS NULL AND "
            "authority_generation IS NULL AND trust_revision IS NULL AND "
            "account_role IS NULL) "
            "OR (contract_version='v2' AND authority_generation>=1 AND "
            "trust_revision>=1 AND account_role IN ('OWNER','ADMIN','USER') "
            "AND expires_at=created_at+interval '300 seconds')),false)",
            name="trusted_challenge_v2_authority_check",
        ),
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


class DeviceAdmissionExchangeReceiptRow(_AdmissionOriginalEvidence, Base):
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
    platform: Mapped[str | None] = mapped_column(Text)
    authority_generation: Mapped[int | None] = mapped_column(BigInteger)
    trust_revision: Mapped[int | None] = mapped_column(BigInteger)
    account_role: Mapped[str | None] = mapped_column(Text)
    receipt_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    __table_args__ = (
        _admission_evidence_constraint(
            ("admission.exchange", "trusted.exchange"),
            "admission_receipt_v2_evidence_check",
        ),
        CheckConstraint(
            "COALESCE(((contract_version='v1' AND platform IS NULL AND "
            "authority_generation IS NULL AND trust_revision IS NULL AND "
            "account_role IS NULL) "
            "OR (contract_version='v2' AND authority_generation>=1 AND "
            "(trust_revision IS NULL OR trust_revision>=1) AND account_role "
            "IN ('OWNER','ADMIN','USER') "
            "AND (original_request_evidence->>'operation'<>'trusted.exchange"
            "' OR trust_revision>=1) "
            "AND receipt_expires_at=created_at+interval '30 days')),false)",
            name="admission_receipt_v2_authority_check",
        ),
        Index(
            "admission_v2_binding_commit",
            "binding_commit_id",
            unique=True,
            postgresql_where=text("contract_version='v2'"),
        ),
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
    contract_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
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
        CheckConstraint("contract_version IN ('v1','v2')", name="admission_web_v2_version_check"),
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
