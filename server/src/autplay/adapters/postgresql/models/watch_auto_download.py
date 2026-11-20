"""Staged watch auto-download gate and owner consent evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

_CONFIRMATION = "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
_VALID_CONSENT = (
    "(enabled AND gate_revision IS NOT NULL AND "
    f"confirmation_code='{_CONFIRMATION}') OR "
    "(NOT enabled AND gate_revision IS NULL AND confirmation_code IS NULL)"
)


class WatchAutoDownloadGateRow(Base):
    __tablename__ = "watch_auto_download_gate"

    singleton_id: Mapped[int] = mapped_column(Integer(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, server_default=text("false"))
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("singleton_id", name="watch_auto_download_gate_pkey"),
        CheckConstraint("singleton_id=1", name="watch_auto_download_gate_singleton_id_check"),
        CheckConstraint("revision>=1", name="watch_auto_download_gate_revision_check"),
        {"schema": "discovery"},
    )


class WatchAutoDownloadConsentRow(Base):
    __tablename__ = "watch_auto_download_consent"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    gate_revision: Mapped[int | None] = mapped_column(BigInteger())
    confirmation_code: Mapped[str | None] = mapped_column(Text())
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="watch_auto_download_consent_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="watch_auto_download_consent_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision>=1", name="watch_auto_download_consent_revision_check"),
        CheckConstraint(_VALID_CONSENT, name="ck_watch_auto_consent_confirmation"),
        {"schema": "discovery"},
    )


class WatchAutoDownloadConsentRevisionRow(Base):
    __tablename__ = "watch_auto_download_consent_revision"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    gate_revision: Mapped[int | None] = mapped_column(BigInteger())
    confirmation_code: Mapped[str | None] = mapped_column(Text())
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "user_id", "revision", name="watch_auto_download_consent_revision_pkey"
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="watch_auto_download_consent_revision_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision>=1", name="watch_auto_download_consent_revision_revision_check"),
        CheckConstraint(_VALID_CONSENT, name="ck_watch_auto_consent_revision_confirmation"),
        {"schema": "discovery"},
    )


__all__ = (
    "WatchAutoDownloadConsentRevisionRow",
    "WatchAutoDownloadConsentRow",
    "WatchAutoDownloadGateRow",
)
