"""Typed SQLAlchemy mappings for the playlist PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .types import JsonValue


class PlaylistRow(Base):
    """Persistence row for ``playlist.playlist``."""

    __tablename__ = "playlist"

    playlist_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PRIVATE'"),
    )
    playlist_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'MANUAL'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("playlist_id", name="playlist_pkey"),
        ForeignKeyConstraint(
            ["owner_user_id"],
            ["account.user_account.user_id"],
            name="playlist_owner_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(name) BETWEEN 1 AND 500",
            name="playlist_name_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="playlist_row_version_check",
        ),
        CheckConstraint(
            "visibility IN ('PRIVATE', 'SHARED', 'PUBLIC')",
            name="ck_playlist_visibility",
        ),
        CheckConstraint(
            "playlist_type IN ('MANUAL', 'SMART', 'SYSTEM')",
            name="ck_playlist_type",
        ),
        {"schema": "playlist"},
    )


class PlaylistEntryRow(Base):
    """Persistence row for ``playlist.playlist_entry``."""

    __tablename__ = "playlist_entry"

    playlist_entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    playlist_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    position_key: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    added_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    source_position: Mapped[int | None] = mapped_column(
        Integer(),
        nullable=True,
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("playlist_entry_id", name="playlist_entry_pkey"),
        ForeignKeyConstraint(
            ["playlist_id"],
            ["playlist.playlist.playlist_id"],
            name="playlist_entry_playlist_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="playlist_entry_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(position_key) BETWEEN 1 AND 128",
            name="playlist_entry_position_key_check",
        ),
        ForeignKeyConstraint(
            ["added_by_user_id"],
            ["account.user_account.user_id"],
            name="playlist_entry_added_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_position IS NULL OR source_position >= 0",
            name="playlist_entry_source_position_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="playlist_entry_row_version_check",
        ),
        {"schema": "playlist"},
    )


class SmartPlaylistRuleRow(Base):
    """Persistence row for ``playlist.smart_playlist_rule``."""

    __tablename__ = "smart_playlist_rule"

    playlist_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    rule_schema_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    rule_json: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
    )
    compiled_hash: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    last_validated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("playlist_id", name="smart_playlist_rule_pkey"),
        ForeignKeyConstraint(
            ["playlist_id"],
            ["playlist.playlist.playlist_id"],
            name="smart_playlist_rule_playlist_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "rule_schema_version >= 1",
            name="smart_playlist_rule_rule_schema_version_check",
        ),
        CheckConstraint(
            "octet_length(compiled_hash) = 32",
            name="ck_smart_playlist_rule_hash_len",
        ),
        {"schema": "playlist"},
    )


Index(
    "ix_playlist_owner_active",
    PlaylistRow.owner_user_id,
    PlaylistRow.updated_at.desc(),
    postgresql_where=text("deleted_at IS NULL"),
)

Index(
    "uq_playlist_entry_active_position",
    PlaylistEntryRow.playlist_id,
    PlaylistEntryRow.position_key,
    unique=True,
    postgresql_where=text("removed_at IS NULL"),
)

Index(
    "ix_playlist_entry_order",
    PlaylistEntryRow.playlist_id,
    PlaylistEntryRow.position_key,
    PlaylistEntryRow.playlist_entry_id,
    postgresql_where=text("removed_at IS NULL"),
)


__all__ = (
    "PlaylistEntryRow",
    "PlaylistRow",
    "SmartPlaylistRuleRow",
)
