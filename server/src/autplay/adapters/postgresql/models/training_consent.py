"""Caller-owned shared-training policy and exact mutation evidence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class TrainingConsentRow(Base):
    __tablename__ = "training_consent"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"), primary_key=True
    )
    decision: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[int] = mapped_column(BigInteger)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "decision IN ('GRANTED','DENIED','WITHDRAWN')", name="training_consent_decision_check"
        ),
        CheckConstraint("revision>=1 AND policy_version=1", name="training_consent_versions_check"),
        CheckConstraint(
            "revision<=9007199254740991 AND (decision<>'GRANTED' OR revision<9007199254740991)",
            name="training_consent_terminal_revision_check",
        ),
        {"schema": "account"},
    )


class TrainingConsentOperationRow(Base):
    __tablename__ = "training_consent_operation"
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="CASCADE")
    )
    # No device FK: final purge removes device authority before removing the account.
    actor_device_id: Mapped[UUID] = mapped_column()
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    applied_decision: Mapped[str] = mapped_column(Text)
    applied_revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(request_sha256)=32", name="training_consent_operation_hash_check"
        ),
        CheckConstraint(
            "applied_decision IN ('GRANTED','DENIED','WITHDRAWN') AND applied_revision>=1",
            name="training_consent_operation_result_check",
        ),
        {"schema": "account"},
    )
