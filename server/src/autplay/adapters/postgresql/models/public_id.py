"""Additive, same-server public ID registration authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PublicIdRegistrationRow(Base):
    __tablename__ = "public_id_registration"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    public_id: Mapped[str] = mapped_column(Text(collation="C"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("public_id", name="uq_social_public_id"),
        CheckConstraint(
            "public_id COLLATE \"C\" ~ '^[a-z0-9_]{3,24}$'",
            name="ck_social_public_id_format",
        ),
        {"schema": "social"},
    )
