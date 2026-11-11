"""Hash-only recovery credential and immutable exact-operation receipts."""

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
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AccountRecoveryCredentialRow(Base):
    __tablename__ = "account_recovery_credential"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id"), primary_key=True
    )
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    identity_thumbprint_sha256: Mapped[bytes] = mapped_column(BYTEA)
    generation: Mapped[int] = mapped_column(BigInteger)
    verifier_sha256: Mapped[bytes] = mapped_column(BYTEA)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "generation>=1 AND identity_epoch>=1 AND updated_at>=created_at",
            name="recovery_credential_versions_check",
        ),
        CheckConstraint(
            "octet_length(verifier_sha256)=32 AND octet_length(identity_thumbprint_sha256)=32",
            name="recovery_credential_hashes_check",
        ),
        {"schema": "account"},
    )


class AccountRecoveryOperationRow(Base):
    __tablename__ = "account_recovery_operation"
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    actor_device_id: Mapped[UUID | None] = mapped_column()
    actor_family_id: Mapped[UUID | None] = mapped_column()
    previous_generation: Mapped[int] = mapped_column(BigInteger)
    result_generation: Mapped[int] = mapped_column(BigInteger)
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    spent_verifier_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    next_verifier_sha256: Mapped[bytes] = mapped_column(BYTEA)
    result_device_id: Mapped[UUID | None] = mapped_column()
    result_session_id: Mapped[UUID | None] = mapped_column()
    binding_commit_id: Mapped[UUID | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    contract_version: Mapped[str] = mapped_column(Text, server_default=text("'v1'"))
    platform: Mapped[str | None] = mapped_column(Text)
    original_document_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    original_request_document: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    signature_algorithm: Mapped[str | None] = mapped_column(Text)
    proof_domain: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "actor_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="recovery_operation_actor_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id", "result_device_id", "result_session_id"],
            [
                "account.user_session.user_id",
                "account.user_session.device_id",
                "account.user_session.session_id",
            ],
            name="recovery_operation_result_fkey",
        ),
        CheckConstraint(
            "previous_generation>=0 AND result_generation=previous_generation+1 "
            "AND authority_generation>=1 AND identity_epoch>=1",
            name="recovery_operation_versions_check",
        ),
        CheckConstraint(
            "octet_length(request_sha256)=32 AND octet_length(next_verifier_sha256)=32 "
            "AND (spent_verifier_sha256 IS NULL OR octet_length(spent_verifier_sha256)=32)",
            name="recovery_operation_hashes_check",
        ),
        CheckConstraint(
            "expires_at>created_at AND expires_at<=created_at+interval '1 day'",
            name="recovery_operation_expiry_check",
        ),
        CheckConstraint(
            "(kind='CONFIGURE' AND actor_device_id IS NOT NULL AND actor_family_id IS NOT NULL "
            "AND spent_verifier_sha256 IS NULL AND result_device_id IS NULL "
            "AND result_session_id IS NULL AND binding_commit_id IS NULL) OR "
            "(kind IN ('RECOVER','DELETE_CANCEL') "
            "AND actor_device_id IS NULL AND actor_family_id IS NULL "
            "AND spent_verifier_sha256 IS NOT NULL AND result_device_id IS NOT NULL "
            "AND result_session_id IS NOT NULL AND binding_commit_id IS NOT NULL "
            "AND previous_generation>=1)",
            name="recovery_operation_shape_check",
        ),
        CheckConstraint(
            "(contract_version='v1' AND platform IS NULL AND original_document_sha256 IS NULL "
            "AND original_request_document IS NULL AND signature_algorithm "
            "IS NULL AND proof_domain IS NULL) "
            "OR COALESCE((contract_version='v2' AND kind IN ('RECOVER','DELETE_CANCEL') "
            "AND platform IN ('ANDROID','MACOS') AND octet_length(original_document_sha256)=32 "
            "AND signature_algorithm='ES256-P1363' "
            "AND ((kind='RECOVER' AND proof_domain=E'autplay:device-compatib"
            "ility:recovery:recover:v2\\n' "
            "AND app_private.valid_recovery_commit_v2(original_request_document)) "
            "OR (kind='DELETE_CANCEL' AND proof_domain=E'autplay:device-comp"
            "atibility:deletion:cancel:v2\\n' "
            "AND app_private.valid_deletion_document_v2(original_request_document,'CANCEL'))) "
            "AND original_request_document->>'platform'=platform "
            "AND original_request_document->>'operation_id'=operation_id::text "
            "AND original_request_document->>'binding_commit_id'=binding_commit_id::text "
            "AND original_request_document->>'account_id'=user_id::text "
            "AND original_request_document->>'expected_server_instance_id'=s"
            "erver_instance_id::text "
            "AND original_request_document->>'expected_identity_epoch'=identity_epoch::text "
            "AND original_request_document->>'expected_code_generation'=previous_generation::text "
            "AND original_request_document->>'request_sha256'=encode(request_sha256,'hex') "
            "AND original_request_document->>'next_code_verifier_sha256'=enc"
            "ode(next_verifier_sha256,'hex') "
            "AND operation_id<>binding_commit_id),false)",
            name="recovery_operation_v2_check",
        ),
        Index("ix_recovery_operation_expiry", "expires_at"),
        Index("ix_recovery_operation_user", "user_id", "created_at"),
        {"schema": "account"},
    )
