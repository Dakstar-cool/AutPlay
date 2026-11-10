"""Typed owner-scoped release-watch choices and favorite projection rows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ArtistWatchChoiceRow(Base):
    __tablename__ = "artist_watch_choice"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    artist_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    manual_follow: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=text("false")
    )
    explicit_exclusion: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "artist_id", name="artist_watch_choice_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="artist_watch_choice_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["artist_id"],
            ["catalog.artist.artist_id"],
            name="artist_watch_choice_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="artist_watch_choice_revision_check"),
        CheckConstraint(
            "NOT (manual_follow AND explicit_exclusion)", name="ck_artist_watch_choice_exclusive"
        ),
        Index(
            "ix_artist_watch_choice_followed",
            "user_id",
            "artist_id",
            postgresql_where=text("manual_follow"),
        ),
        {"schema": "library"},
    )


class FavoriteArtistRefreshRow(Base):
    __tablename__ = "favorite_artist_refresh"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text(), nullable=False)
    evidence_window_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    evidence_window_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    evidence_event_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    evidence_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="favorite_artist_refresh_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="favorite_artist_refresh_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("generation >= 1", name="favorite_artist_refresh_generation_check"),
        CheckConstraint(
            "length(algorithm_version) BETWEEN 1 AND 80",
            name="favorite_artist_refresh_algorithm_version_check",
        ),
        CheckConstraint(
            "evidence_event_count BETWEEN 0 AND 50000",
            name="favorite_artist_refresh_evidence_event_count_check",
        ),
        CheckConstraint(
            "octet_length(evidence_sha256)=32", name="favorite_artist_refresh_evidence_sha256_check"
        ),
        CheckConstraint(
            "evidence_window_start < evidence_window_end", name="ck_favorite_refresh_window"
        ),
        {"schema": "library"},
    )


class FavoriteArtistProjectionRow(Base):
    __tablename__ = "favorite_artist_projection"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    artist_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text(), nullable=False)
    score_milli: Mapped[int] = mapped_column(Integer(), nullable=False)
    qualified_play_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    distinct_recording_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    last_played_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    evidence_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    projected_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "artist_id", name="favorite_artist_projection_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="favorite_artist_projection_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["artist_id"],
            ["catalog.artist.artist_id"],
            name="favorite_artist_projection_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["library.favorite_artist_refresh.user_id"],
            name="fk_favorite_projection_refresh",
            ondelete="RESTRICT",
        ),
        CheckConstraint("generation >= 1", name="favorite_artist_projection_generation_check"),
        CheckConstraint(
            "length(algorithm_version) BETWEEN 1 AND 80",
            name="favorite_artist_projection_algorithm_version_check",
        ),
        CheckConstraint(
            "score_milli BETWEEN 1 AND 1000000", name="favorite_artist_projection_score_milli_check"
        ),
        CheckConstraint(
            "qualified_play_count BETWEEN 3 AND 50000",
            name="favorite_artist_projection_qualified_play_count_check",
        ),
        CheckConstraint(
            "distinct_recording_count BETWEEN 2 AND 50000",
            name="favorite_artist_projection_distinct_recording_count_check",
        ),
        CheckConstraint(
            "octet_length(evidence_sha256)=32",
            name="favorite_artist_projection_evidence_sha256_check",
        ),
        Index("ix_favorite_projection_rank", "user_id", text("score_milli DESC"), "artist_id"),
        {"schema": "library"},
    )


class FavoriteArtistRefreshRetryRow(Base):
    __tablename__ = "favorite_artist_refresh_retry"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    retry_after: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="favorite_artist_refresh_retry_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="favorite_artist_refresh_retry_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "retry_after=attempted_at+INTERVAL '24 hours'", name="ck_favorite_retry_interval"
        ),
        Index("ix_favorite_refresh_retry_due", "retry_after", "user_id"),
        {"schema": "library"},
    )


__all__ = (
    "ArtistWatchChoiceRow",
    "FavoriteArtistProjectionRow",
    "FavoriteArtistRefreshRetryRow",
    "FavoriteArtistRefreshRow",
)
