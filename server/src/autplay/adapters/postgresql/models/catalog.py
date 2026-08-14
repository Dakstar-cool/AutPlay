# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the catalog PostgreSQL schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ArtistRow(Base):
    """Persistence row for ``catalog.artist``."""

    __tablename__ = "artist"

    artist_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    sort_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalized_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    artist_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    disambiguation: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    country_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    identity_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'ACTIVE'"),
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
        PrimaryKeyConstraint("artist_id", name="artist_pkey"),
        CheckConstraint(
            "length(name) BETWEEN 1 AND 1000",
            name="artist_name_check",
        ),
        CheckConstraint(
            "length(sort_name) BETWEEN 1 AND 1000",
            name="artist_sort_name_check",
        ),
        CheckConstraint(
            "length(normalized_name) BETWEEN 1 AND 1000",
            name="artist_normalized_name_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="artist_row_version_check",
        ),
        CheckConstraint(
            "artist_type IN ('PERSON', 'GROUP', 'ORCHESTRA', 'OTHER', 'UNKNOWN')",
            name="ck_artist_type",
        ),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="ck_artist_country_code",
        ),
        CheckConstraint(
            "identity_status IN ('ACTIVE', 'PROVISIONAL', 'MERGED', 'DEPRECATED')",
            name="ck_artist_identity_status",
        ),
        {"schema": "catalog"},
    )


class ArtistCreditRow(Base):
    """Persistence row for ``catalog.artist_credit``."""

    __tablename__ = "artist_credit"

    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    display_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalized_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
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
        PrimaryKeyConstraint("artist_credit_id", name="artist_credit_pkey"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 2000",
            name="artist_credit_display_name_check",
        ),
        CheckConstraint(
            "length(normalized_name) BETWEEN 1 AND 2000",
            name="artist_credit_normalized_name_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="artist_credit_row_version_check",
        ),
        {"schema": "catalog"},
    )


class ArtistCreditNameRow(Base):
    """Persistence row for ``catalog.artist_credit_name``."""

    __tablename__ = "artist_credit_name"

    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    artist_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    credited_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    join_phrase: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("''"),
    )
    role: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PRIMARY'"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["artist_credit_id"],
            ["catalog.artist_credit.artist_credit_id"],
            name="artist_credit_name_artist_credit_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "position >= 0",
            name="artist_credit_name_position_check",
        ),
        ForeignKeyConstraint(
            ["artist_id"],
            ["catalog.artist.artist_id"],
            name="artist_credit_name_artist_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(credited_name) BETWEEN 1 AND 1000",
            name="artist_credit_name_credited_name_check",
        ),
        PrimaryKeyConstraint("artist_credit_id", "position", name="artist_credit_name_pkey"),
        CheckConstraint(
            "role IN ('PRIMARY', 'FEATURED', 'REMIXER', 'CONDUCTOR', 'OTHER')",
            name="ck_artist_credit_name_role",
        ),
        {"schema": "catalog"},
    )


class WorkRow(Base):
    """Persistence row for ``catalog.work``."""

    __tablename__ = "work"

    work_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    work_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'OTHER'"),
    )
    language_code: Mapped[str | None] = mapped_column(
        Text(),
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
        PrimaryKeyConstraint("work_id", name="work_pkey"),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 2000",
            name="work_title_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="work_row_version_check",
        ),
        CheckConstraint(
            "work_type IN ('SONG', 'COMPOSITION', 'OTHER')",
            name="ck_work_type",
        ),
        CheckConstraint(
            "language_code IS NULL OR length(language_code) BETWEEN 2 AND 35",
            name="ck_work_language_code",
        ),
        {"schema": "catalog"},
    )


class RecordingRow(Base):
    """Persistence row for ``catalog.recording``."""

    __tablename__ = "recording"

    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    work_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalized_title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    duration_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    recording_kind: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    version_text: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    disambiguation: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    explicit: Mapped[bool | None] = mapped_column(
        Boolean(),
        nullable=True,
    )
    identity_status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PROVISIONAL'"),
    )
    metadata_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
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
        PrimaryKeyConstraint("recording_id", name="recording_pkey"),
        ForeignKeyConstraint(
            ["work_id"],
            ["catalog.work.work_id"],
            name="recording_work_id_fkey",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["artist_credit_id"],
            ["catalog.artist_credit.artist_credit_id"],
            name="recording_artist_credit_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 2000",
            name="recording_title_check",
        ),
        CheckConstraint(
            "length(normalized_title) BETWEEN 1 AND 2000",
            name="recording_normalized_title_check",
        ),
        CheckConstraint(
            "duration_ms > 0",
            name="recording_duration_ms_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="recording_row_version_check",
        ),
        CheckConstraint(
            "recording_kind IN ('STUDIO', 'LIVE', 'REMIX', 'EDIT', 'DEMO', 'OTHER', 'UNKNOWN')",
            name="ck_recording_kind",
        ),
        CheckConstraint(
            "identity_status IN ('ACTIVE', 'PROVISIONAL', 'MERGED', 'DEPRECATED')",
            name="ck_recording_identity_status",
        ),
        CheckConstraint(
            "metadata_confidence IS NULL OR metadata_confidence BETWEEN 0 AND 1",
            name="ck_recording_metadata_confidence",
        ),
        {"schema": "catalog"},
    )


class ReleaseGroupRow(Base):
    """Persistence row for ``catalog.release_group``."""

    __tablename__ = "release_group"

    release_group_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    normalized_title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    primary_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    secondary_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("ARRAY[]::text[]"),
    )
    first_release_date: Mapped[date | None] = mapped_column(
        Date(),
        nullable=True,
    )
    date_precision: Mapped[str | None] = mapped_column(
        Text(),
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
        PrimaryKeyConstraint("release_group_id", name="release_group_pkey"),
        ForeignKeyConstraint(
            ["artist_credit_id"],
            ["catalog.artist_credit.artist_credit_id"],
            name="release_group_artist_credit_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 2000",
            name="release_group_title_check",
        ),
        CheckConstraint(
            "length(normalized_title) BETWEEN 1 AND 2000",
            name="release_group_normalized_title_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="release_group_row_version_check",
        ),
        CheckConstraint(
            "primary_type IN ('ALBUM', 'SINGLE', 'EP', 'BROADCAST', 'OTHER')",
            name="ck_release_group_primary_type",
        ),
        CheckConstraint(
            "secondary_types <@ ARRAY['COMPILATION', 'SOUNDTRACK', 'LIVE', 'REMIX', 'DJ_MIX', 'MIXTAPE', 'OTHER']::text[]",
            name="ck_release_group_secondary_types",
        ),
        CheckConstraint(
            "(first_release_date IS NULL AND date_precision IS NULL) OR (first_release_date IS NOT NULL AND date_precision IN ('YEAR', 'MONTH', 'DAY'))",
            name="ck_release_group_date_precision",
        ),
        {"schema": "catalog"},
    )


class ReleaseRow(Base):
    """Persistence row for ``catalog.release``."""

    __tablename__ = "release"

    release_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    release_group_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    country_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    release_date: Mapped[date | None] = mapped_column(
        Date(),
        nullable=True,
    )
    date_precision: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    barcode: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    label_name: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    catalog_number: Mapped[str | None] = mapped_column(
        Text(),
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
        PrimaryKeyConstraint("release_id", name="release_pkey"),
        ForeignKeyConstraint(
            ["release_group_id"],
            ["catalog.release_group.release_group_id"],
            name="release_release_group_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["artist_credit_id"],
            ["catalog.artist_credit.artist_credit_id"],
            name="release_artist_credit_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 2000",
            name="release_title_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="release_row_version_check",
        ),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="ck_release_country_code",
        ),
        CheckConstraint(
            "(release_date IS NULL AND date_precision IS NULL) OR (release_date IS NOT NULL AND date_precision IN ('YEAR', 'MONTH', 'DAY'))",
            name="ck_release_date_precision",
        ),
        CheckConstraint(
            "status IN ('OFFICIAL', 'PROMOTION', 'BOOTLEG', 'PSEUDO', 'UNKNOWN')",
            name="ck_release_status",
        ),
        {"schema": "catalog"},
    )


class MediumRow(Base):
    """Persistence row for ``catalog.medium``."""

    __tablename__ = "medium"

    medium_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    release_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    format: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    track_count: Mapped[int | None] = mapped_column(
        Integer(),
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
        PrimaryKeyConstraint("medium_id", name="medium_pkey"),
        ForeignKeyConstraint(
            ["release_id"],
            ["catalog.release.release_id"],
            name="medium_release_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "position >= 1",
            name="medium_position_check",
        ),
        CheckConstraint(
            "track_count IS NULL OR track_count >= 0",
            name="medium_track_count_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="medium_row_version_check",
        ),
        UniqueConstraint("release_id", "position", name="uq_medium_release_position"),
        {"schema": "catalog"},
    )


class ReleaseTrackRow(Base):
    """Persistence row for ``catalog.release_track``."""

    __tablename__ = "release_track"

    release_track_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    medium_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    recording_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    artist_credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    number_text: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    duration_ms: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    hidden: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
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
        PrimaryKeyConstraint("release_track_id", name="release_track_pkey"),
        ForeignKeyConstraint(
            ["medium_id"],
            ["catalog.medium.medium_id"],
            name="release_track_medium_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="release_track_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["artist_credit_id"],
            ["catalog.artist_credit.artist_credit_id"],
            name="release_track_artist_credit_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "sequence_no >= 1",
            name="release_track_sequence_no_check",
        ),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 2000",
            name="release_track_title_check",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms > 0",
            name="release_track_duration_ms_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="release_track_row_version_check",
        ),
        UniqueConstraint("medium_id", "sequence_no", name="uq_release_track_medium_sequence"),
        {"schema": "catalog"},
    )


Index(
    "ix_artist_normalized_name_trgm",
    ArtistRow.normalized_name,
    postgresql_using="gin",
    postgresql_ops={"normalized_name": "gin_trgm_ops"},
)

Index(
    "ix_artist_credit_normalized_name_trgm",
    ArtistCreditRow.normalized_name,
    postgresql_using="gin",
    postgresql_ops={"normalized_name": "gin_trgm_ops"},
)

Index(
    "ix_artist_credit_name_artist",
    ArtistCreditNameRow.artist_id,
    ArtistCreditNameRow.artist_credit_id,
)

Index(
    "ix_recording_artist_credit",
    RecordingRow.artist_credit_id,
    RecordingRow.identity_status,
)

Index(
    "ix_recording_normalized_title_trgm",
    RecordingRow.normalized_title,
    postgresql_using="gin",
    postgresql_ops={"normalized_title": "gin_trgm_ops"},
)

Index(
    "ix_release_group_title_trgm",
    ReleaseGroupRow.normalized_title,
    postgresql_using="gin",
    postgresql_ops={"normalized_title": "gin_trgm_ops"},
)

Index(
    "ix_release_release_group",
    ReleaseRow.release_group_id,
    ReleaseRow.release_date,
)

Index(
    "ix_release_barcode",
    ReleaseRow.barcode,
    postgresql_where=text("barcode IS NOT NULL"),
)

Index(
    "ix_release_track_recording",
    ReleaseTrackRow.recording_id,
    ReleaseTrackRow.medium_id,
)


__all__ = (
    "ArtistCreditNameRow",
    "ArtistCreditRow",
    "ArtistRow",
    "MediumRow",
    "RecordingRow",
    "ReleaseGroupRow",
    "ReleaseRow",
    "ReleaseTrackRow",
    "WorkRow",
)
