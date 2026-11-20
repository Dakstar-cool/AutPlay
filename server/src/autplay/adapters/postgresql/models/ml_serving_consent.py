"""Independent-serving-ledger projection and immutable Android grant binding."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RegistrationSessionBindingRow(Base):
    __tablename__ = "registration_session_binding"
    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.control_step_up_challenge.challenge_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    session_generation: Mapped[int | None] = mapped_column(BigInteger)
    session_rotated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = ({"schema": "ml"},)


class AndroidGrantBindingRow(Base):
    __tablename__ = "android_grant_binding"
    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.control_step_up_challenge.challenge_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    grant_sha256: Mapped[bytes] = mapped_column(BYTEA)
    session_generation: Mapped[int | None] = mapped_column(BigInteger)
    session_rotated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint("octet_length(grant_sha256)=32", name="android_grant_binding_hash_check"),
        CheckConstraint(
            "jsonb_typeof(document)='object' AND NOT (document ? 'nonce') "
            "AND octet_length(document::text)<=8192",
            name="android_grant_binding_document_check",
        ),
        {"schema": "ml"},
    )


class ServingConsentCurrentRow(Base):
    __tablename__ = "serving_consent_current"
    owner_tag: Mapped[str] = mapped_column(Text, primary_key=True)
    purpose: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    operation_id: Mapped[UUID] = mapped_column()
    decision: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "owner_tag ~ '^[0-9a-f]{64}$' AND purpose='SONA_R1C_DESCENDANT_MODEL_SERVING_V1'",
            name="serving_consent_current_scope_check",
        ),
        CheckConstraint(
            "decision IN ('GRANTED','DENIED','WITHDRAWN') "
            "AND revision BETWEEN 1 AND 9007199254740991",
            name="serving_consent_current_result_check",
        ),
        {"schema": "ml"},
    )


class ServingConsentReceiptRow(Base):
    __tablename__ = "serving_consent_receipt"
    owner_tag: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    )
    actor_tag: Mapped[str | None] = mapped_column(Text)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    decision: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    challenge_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ml.control_step_up_receipt.challenge_id", ondelete="RESTRICT")
    )
    __table_args__ = (
        CheckConstraint(
            "owner_tag ~ '^[0-9a-f]{64}$' AND octet_length(request_sha256)=32",
            name="serving_consent_receipt_scope_check",
        ),
        CheckConstraint(
            "decision IN ('GRANTED','DENIED','WITHDRAWN') "
            "AND revision BETWEEN 1 AND 9007199254740991",
            name="serving_consent_receipt_result_check",
        ),
        CheckConstraint(
            "(decision='GRANTED')=(challenge_id IS NOT NULL)",
            name="serving_consent_receipt_proof_check",
        ),
        {"schema": "ml"},
    )
