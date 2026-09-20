"""A claimed CAS key cannot be published until exact retirement has completed."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class OrphanObjectClaimRow(Base):
    __tablename__ = "orphan_object_claim"
    claim_id: Mapped[UUID] = mapped_column(primary_key=True)
    storage_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "vault.provider_maintenance.execution_id",
            use_alter=True,
            name="orphan_object_claim_completed_execution_id_fkey",
        )
    )
    __table_args__ = (
        UniqueConstraint("claim_id", "storage_key", name="orphan_object_claim_identity_key"),
        CheckConstraint("storage_key ~ '^[0-9a-f]{64}$'", name="orphan_object_claim_key_check"),
        CheckConstraint(
            "(completed_at IS NULL AND completed_execution_id IS NULL) OR "
            "(completed_at IS NOT NULL AND completed_execution_id IS NOT NULL "
            "AND completed_at>=created_at)",
            name="orphan_object_claim_completion_check",
        ),
        Index(
            "uq_orphan_object_claim_active",
            "storage_key",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
        ),
        {"schema": "vault"},
    )
