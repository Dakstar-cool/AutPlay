"""Owner-owned portable taste evidence, independent of catalog and Vault authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PortableTasteSignalRow(Base):
    __tablename__ = "portable_taste_signal"
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    record_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("library.user_track_ref.user_track_ref_id", ondelete="RESTRICT"),
    )
    interaction: Mapped[str] = mapped_column(Text())
    weight: Mapped[int] = mapped_column(SmallInteger())
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    row_version: Mapped[int] = mapped_column(BigInteger(), server_default=text("1"))
    __table_args__ = (
        CheckConstraint(
            "interaction IN ('LIKE','DISLIKE','SKIP','COMPLETE','EXCLUDE')",
            name="portable_taste_signal_interaction_check",
        ),
        CheckConstraint("weight BETWEEN -1000 AND 1000", name="portable_taste_signal_weight_check"),
        CheckConstraint(
            "occurred_at_ms BETWEEN 0 AND 8640000000000000 AND row_version>=1",
            name="portable_taste_signal_revision_check",
        ),
        Index("portable_taste_signal_account_cursor", "user_id", "updated_at", "record_id"),
        {"schema": "library"},
    )


__all__ = ("PortableTasteSignalRow",)
