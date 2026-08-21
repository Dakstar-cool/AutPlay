# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the library PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class UserTrackRefRow(Base):
    """Persistence row for ``library.user_track_ref``."""

    __tablename__ = "user_track_ref"

    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    recording_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    resolution_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'UNRESOLVED'"),
    )
    raw_title: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_artist: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_album: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    raw_duration_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    current_match_decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    resolution_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("user_track_ref_id", name="user_track_ref_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="user_track_ref_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="user_track_ref_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "raw_duration_ms IS NULL OR raw_duration_ms > 0",
            name="user_track_ref_raw_duration_ms_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="user_track_ref_row_version_check",
        ),
        CheckConstraint(
            "resolution_status IN ('UNRESOLVED', 'CANDIDATES', 'RESOLVED', 'AMBIGUOUS', 'NOT_FOUND')",
            name="ck_user_track_ref_resolution_status",
        ),
        CheckConstraint(
            "(resolution_status = 'RESOLVED' AND recording_id IS NOT NULL AND resolved_at IS NOT NULL) OR (resolution_status <> 'RESOLVED' AND recording_id IS NULL)",
            name="ck_user_track_ref_resolution_target",
        ),
        CheckConstraint(
            "resolution_confidence IS NULL OR resolution_confidence BETWEEN 0 AND 1",
            name="ck_user_track_ref_resolution_confidence",
        ),
        CheckConstraint(
            "(current_match_decision_id IS NULL AND resolution_status = 'UNRESOLVED' AND recording_id IS NULL AND resolution_confidence IS NULL) OR current_match_decision_id IS NOT NULL",
            name="ck_user_track_ref_decision_projection",
        ),
        ForeignKeyConstraint(
            ["current_match_decision_id"],
            ["identity.match_decision.decision_id"],
            name="fk_user_track_ref_current_match_decision",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "library"},
    )


class UserTrackRefExternalReferenceRow(Base):
    """Persistence row for ``library.user_track_ref_external_reference``."""

    __tablename__ = "user_track_ref_external_reference"

    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    external_reference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    relation_role: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'ALIAS'"),
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="user_track_ref_external_reference_user_track_ref_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["external_reference_id"],
            ["identity.external_reference.external_reference_id"],
            name="user_track_ref_external_reference_external_reference_id_fkey",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "user_track_ref_id",
            "external_reference_id",
            name="user_track_ref_external_reference_pkey",
        ),
        CheckConstraint(
            "relation_role IN ('PRIMARY_SOURCE', 'ALIAS', 'IMPORT_EVIDENCE')",
            name="ck_user_track_ref_external_role",
        ),
        {"schema": "library"},
    )


class LibraryEntryRow(Base):
    """Persistence row for ``library.library_entry``."""

    __tablename__ = "library_entry"

    library_entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    source: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    availability_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PENDING'"),
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
        PrimaryKeyConstraint("library_entry_id", name="library_entry_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="library_entry_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="library_entry_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="library_entry_row_version_check",
        ),
        CheckConstraint(
            "source IN ('LOCAL', 'IMPORT', 'SEARCH', 'SHARE', 'RESTORE')",
            name="ck_library_entry_source",
        ),
        CheckConstraint(
            "availability_status IN ('LOCAL', 'VAULT', 'EXTERNAL', 'PENDING', 'NOT_FOUND', 'AMBIGUOUS')",
            name="ck_library_entry_availability",
        ),
        {"schema": "library"},
    )


class UserTrackPreferenceRow(Base):
    """Persistence row for ``library.user_track_preference``."""

    __tablename__ = "user_track_preference"

    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    preference: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'NEUTRAL'"),
    )
    rating: Mapped[int | None] = mapped_column(
        SmallInteger(),
        nullable=True,
    )
    excluded_from_taste: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )
    updated_by_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
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
        PrimaryKeyConstraint("user_track_ref_id", name="user_track_preference_pkey"),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="user_track_preference_user_track_ref_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["updated_by_event_id"],
            ["sync.sync_event.event_id"],
            name="user_track_preference_updated_by_event_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="user_track_preference_row_version_check",
        ),
        CheckConstraint(
            "preference IN ('NEUTRAL', 'LIKED', 'DISLIKED')",
            name="ck_user_track_preference",
        ),
        CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5",
            name="ck_user_track_rating",
        ),
        {"schema": "library"},
    )


class ListeningEventRow(Base):
    """Persistence row for ``library.listening_event``."""

    __tablename__ = "listening_event"

    listening_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    recording_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    played_ms: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("0"),
    )
    track_duration_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    completion_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    event_origin: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'ORGANIC'"),
    )
    context: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'GENERAL'"),
    )
    recommendation_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    explicit_feedback: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'NONE'"),
    )
    excluded_from_taste: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("listening_event_id", name="listening_event_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="listening_event_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="listening_event_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_track_ref_id"],
            ["library.user_track_ref.user_track_ref_id"],
            name="listening_event_user_track_ref_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="listening_event_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "played_ms >= 0",
            name="listening_event_played_ms_check",
        ),
        CheckConstraint(
            "track_duration_ms IS NULL OR track_duration_ms > 0",
            name="listening_event_track_duration_ms_check",
        ),
        CheckConstraint(
            "completion_ratio IS NULL OR completion_ratio BETWEEN 0 AND 1",
            name="listening_event_completion_ratio_check",
        ),
        ForeignKeyConstraint(
            ["recommendation_request_id"],
            ["ml.recommendation_request.recommendation_request_id"],
            name="listening_event_recommendation_request_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_listening_event_device_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_origin IN ('ORGANIC', 'RECOMMENDED', 'PLAYLIST', 'SEARCH', 'WAVE')",
            name="ck_listening_event_origin",
        ),
        CheckConstraint(
            "context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY')",
            name="ck_listening_event_context",
        ),
        CheckConstraint(
            "explicit_feedback IN ('NONE', 'LIKE', 'DISLIKE')",
            name="ck_listening_event_feedback",
        ),
        CheckConstraint(
            "event_origin <> 'RECOMMENDED' OR recommendation_request_id IS NOT NULL",
            name="ck_listening_event_recommendation_origin",
        ),
        {"schema": "library"},
    )


Index(
    "uq_user_track_ref_active_recording",
    UserTrackRefRow.user_id,
    UserTrackRefRow.recording_id,
    unique=True,
    postgresql_where=text("recording_id IS NOT NULL AND deleted_at IS NULL"),
)

Index(
    "ix_user_track_ref_recording_user_active",
    UserTrackRefRow.recording_id,
    UserTrackRefRow.user_id,
    postgresql_where=text("recording_id IS NOT NULL AND deleted_at IS NULL"),
)

Index(
    "ix_user_track_ref_user_status",
    UserTrackRefRow.user_id,
    UserTrackRefRow.resolution_status,
    UserTrackRefRow.updated_at.desc(),
    postgresql_where=text("deleted_at IS NULL"),
)

Index(
    "ix_user_track_ref_external_reverse",
    UserTrackRefExternalReferenceRow.external_reference_id,
    UserTrackRefExternalReferenceRow.user_track_ref_id,
)

Index(
    "uq_library_entry_active",
    LibraryEntryRow.user_id,
    LibraryEntryRow.user_track_ref_id,
    unique=True,
    postgresql_where=text("removed_at IS NULL"),
)

Index(
    "ix_library_entry_page",
    LibraryEntryRow.user_id,
    LibraryEntryRow.added_at.desc(),
    LibraryEntryRow.library_entry_id,
    postgresql_where=text("removed_at IS NULL"),
)

Index(
    "ix_listening_event_user_time",
    ListeningEventRow.user_id,
    ListeningEventRow.started_at.desc(),
    ListeningEventRow.listening_event_id,
)

Index(
    "ix_listening_event_recording_time",
    ListeningEventRow.recording_id,
    ListeningEventRow.started_at.desc(),
    postgresql_where=text("recording_id IS NOT NULL"),
)


__all__ = (
    "LibraryEntryRow",
    "ListeningEventRow",
    "UserTrackPreferenceRow",
    "UserTrackRefExternalReferenceRow",
    "UserTrackRefRow",
)
