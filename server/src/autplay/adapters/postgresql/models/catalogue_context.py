"""Immutable owner-scoped lookup context and original source-search request binding."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class InternetCatalogueContextRow(Base):
    __tablename__ = "internet_catalogue_context"
    __table_args__ = (
        UniqueConstraint("user_id", "context_id", name="internet_catalogue_context_owner_key"),
        CheckConstraint(
            "jsonb_typeof(card)='object' AND octet_length(card::text)<=16384 "
            "AND card->>'schema_version'='1'",
            name="internet_catalogue_context_card_check",
        ),
        CheckConstraint(
            "octet_length(context_sha256)=32", name="internet_catalogue_context_sha_check"
        ),
        CheckConstraint(
            "expires_at=observed_at+interval '24 hours'",
            name="internet_catalogue_context_expiry_check",
        ),
        Index("internet_catalogue_context_owner_time", "user_id", text("observed_at DESC")),
        {"schema": "discovery"},
    )
    context_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    card: Mapped[dict[str, object]] = mapped_column(JSONB)
    context_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InternetSearchContextRow(Base):
    __tablename__ = "internet_search_context"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "search_id"],
            ["discovery.internet_search.user_id", "discovery.internet_search.search_id"],
            name="internet_search_context_search_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id", "context_id"],
            [
                "discovery.internet_catalogue_context.user_id",
                "discovery.internet_catalogue_context.context_id",
            ],
            name="internet_search_context_owner_fkey",
        ),
        CheckConstraint(
            "octet_length(request_sha256)=32", name="internet_search_context_sha_check"
        ),
        {"schema": "discovery"},
    )
    search_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column()
    context_id: Mapped[UUID | None] = mapped_column()
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary)
