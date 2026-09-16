"""Owner-scoped search and selected acquisition persistence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class InternetSearchRow(Base):
    __tablename__ = "internet_search"
    __table_args__ = (
        UniqueConstraint("user_id", "search_id", name="internet_search_user_id_search_id_key"),
        CheckConstraint("length(query) BETWEEN 1 AND 200", name="internet_search_query_check"),
        CheckConstraint(
            "jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<=5",
            name="internet_search_candidates_check",
        ),
        CheckConstraint(
            "octet_length(snapshot_sha256)=32", name="internet_search_snapshot_sha256_check"
        ),
        Index("internet_search_owner_time", "user_id", text("created_at DESC")),
        {"schema": "discovery"},
    )
    search_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    query: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    snapshot_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InternetAcquisitionRow(Base):
    __tablename__ = "internet_acquisition"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "search_id",
            "candidate_id",
            name="internet_acquisition_user_id_search_id_candidate_id_key",
        ),
        ForeignKeyConstraint(
            ["user_id", "search_id"],
            ["discovery.internet_search.user_id", "discovery.internet_search.search_id"],
            name="internet_acquisition_user_id_search_id_fkey",
        ),
        CheckConstraint(
            "state IN ('QUEUED','DOWNLOADING','UPLOADING','PROCESSING','READY','FAILED')",
            name="internet_acquisition_state_check",
        ),
        {"schema": "discovery"},
    )
    acquisition_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    device_id: Mapped[UUID] = mapped_column(ForeignKey("account.device.device_id"))
    search_id: Mapped[UUID] = mapped_column()
    candidate_id: Mapped[str] = mapped_column(Text)
    selected_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.job.job_id"))
    state: Mapped[str] = mapped_column(Text, server_default=text("'QUEUED'"))
    user_track_ref_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("library.user_track_ref.user_track_ref_id")
    )
    upload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.upload_session.upload_session_id")
    )
    audio_variant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.audio_variant.audio_variant_id")
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
