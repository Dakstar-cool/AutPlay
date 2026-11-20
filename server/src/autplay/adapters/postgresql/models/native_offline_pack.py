"""Personal v2 pack bindings and content-bound lifecycle receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class OfflinePackBindingReceiptRow(Base):
    __tablename__ = "offline_pack_binding_receipt"
    pack_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID]
    device_id: Mapped[UUID]
    recommendation_request_id: Mapped[UUID]
    serving_contract_version: Mapped[int] = mapped_column(Integer)
    decision_sha256: Mapped[bytes] = mapped_column(BYTEA)
    served_item_count: Mapped[int] = mapped_column(Integer)
    served_items_sha256: Mapped[bytes] = mapped_column(BYTEA)
    pack_payload_sha256: Mapped[bytes] = mapped_column(BYTEA)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    content_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    attribution_expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "recommendation_request_id"],
            [
                "ml.recommendation_request.user_id",
                "ml.recommendation_request.recommendation_request_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("serving_contract_version=2", name="native_pack_binding_version"),
        CheckConstraint("served_item_count BETWEEN 0 AND 100", name="native_pack_binding_count"),
        CheckConstraint(
            "content_expires_at>created_at AND content_expires_at<=created_at+interval '7 days' "
            "AND content_expires_at<=attribution_expires_at",
            name="native_pack_binding_expiry",
        ),
        *(
            CheckConstraint(f"octet_length({name})=32", name=f"native_pack_binding_{name}")
            for name in (
                "decision_sha256",
                "served_items_sha256",
                "pack_payload_sha256",
                "binding_sha256",
            )
        ),
        Index(
            "ix_native_pack_binding_owner", "owner_user_id", "recommendation_request_id", "pack_id"
        ),
        Index("ix_native_pack_binding_expiry", "attribution_expires_at", "pack_id"),
        {"schema": "ml"},
    )


class NativeOfflinePackPurgeReceiptRow(Base):
    __tablename__ = "native_offline_pack_purge_receipt"
    pack_id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_user_id: Mapped[UUID]
    binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    association_sha256: Mapped[bytes] = mapped_column(BYTEA)
    payload_sha256: Mapped[bytes] = mapped_column(BYTEA)
    reason: Mapped[str] = mapped_column(Text)
    purged_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    retain_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id"], ["account.user_account.user_id"], ondelete="CASCADE"
        ),
        CheckConstraint("stage IN ('CONTENT','BINDING')", name="native_pack_purge_stage"),
        CheckConstraint(
            "reason IN ('PACK_EXPIRED','SERVING_TRUTH_EXPIRED','ACCOUNT_DELETION')",
            name="native_pack_purge_reason",
        ),
        *(
            CheckConstraint(f"octet_length({name})=32", name=f"native_pack_purge_{name}")
            for name in ("binding_sha256", "association_sha256", "payload_sha256")
        ),
        Index("ix_native_pack_purge_owner", "owner_user_id", "retain_until", "pack_id"),
        {"schema": "ml"},
    )
