"""Immutable terminal-upload ownership survives process and HTTP lifetimes."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class UploadCleanupClaimRow(Base):
    __tablename__ = "upload_cleanup_claim"
    claim_id: Mapped[UUID] = mapped_column(
        ForeignKey("vault.upload_session.upload_session_id"), primary_key=True
    )
    storage_key: Mapped[str] = mapped_column(Text)
    terminal_state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "vault.provider_maintenance.execution_id",
            use_alter=True,
            name="upload_cleanup_claim_completed_execution_id_fkey",
            deferrable=True,
            initially="IMMEDIATE",
        )
    )
    __table_args__ = (
        UniqueConstraint("claim_id", "storage_key", name="upload_cleanup_claim_identity_key"),
        UniqueConstraint("storage_key", name="upload_cleanup_claim_storage_key"),
        CheckConstraint(
            "storage_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$' "
            "AND terminal_state IN ('CANCELLED','EXPIRED')",
            name="upload_cleanup_claim_target_check",
        ),
        CheckConstraint(
            "(completed_at IS NULL AND completed_execution_id IS NULL) OR "
            "(completed_at IS NOT NULL AND completed_execution_id IS NOT NULL "
            "AND completed_at>=created_at)",
            name="upload_cleanup_claim_completion_check",
        ),
        Index(
            "ix_upload_cleanup_pending", "claim_id", postgresql_where=text("completed_at IS NULL")
        ),
        {"schema": "vault"},
    )
