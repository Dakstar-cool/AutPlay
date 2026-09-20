"""Owner-reachable descriptive projections and immutable revision/art evidence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class MetadataArtworkRow(Base):
    __tablename__ = "metadata_artwork"
    __table_args__ = (
        CheckConstraint(
            "length(sha256)=64 AND sha256 ~ '^[a-f0-9]{64}$'", name="metadata_artwork_sha256_check"
        ),
        CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 2097152", name="metadata_artwork_content_check"
        ),
        {"schema": "library"},
    )
    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TrackMetadataRow(Base):
    __tablename__ = "track_metadata"
    __table_args__ = (
        CheckConstraint("revision >= 1 AND generation >= 1", name="track_metadata_versions_check"),
        CheckConstraint(
            "state IN ('QUEUED','READY','REVIEW','NOT_FOUND','RETRY','FAILED')",
            name="track_metadata_state_check",
        ),
        CheckConstraint(
            "jsonb_typeof(document)='object' AND octet_length(document::text)<=65536",
            name="track_metadata_document_check",
        ),
        CheckConstraint(
            "jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<=5 "
            "AND octet_length(candidates::text)<=65536",
            name="track_metadata_candidates_check",
        ),
        {"schema": "library"},
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        ForeignKey("library.user_track_ref.user_track_ref_id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    state: Mapped[str] = mapped_column(Text, server_default=text("'QUEUED'"))
    error_code: Mapped[str | None] = mapped_column(Text)
    artwork_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("library.metadata_artwork.sha256")
    )
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.job.job_id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TrackMetadataRevisionRow(Base):
    __tablename__ = "track_metadata_revision"
    __table_args__ = (
        UniqueConstraint(
            "user_track_ref_id", "operation_id", name="track_metadata_revision_operation_key"
        ),
        CheckConstraint("revision >= 1", name="track_metadata_revision_revision_check"),
        CheckConstraint(
            "octet_length(snapshot::text)<=140000 AND jsonb_typeof(snapshot)='object'",
            name="track_metadata_revision_snapshot_check",
        ),
        CheckConstraint(
            "request_sha256 IS NULL OR octet_length(request_sha256)=32",
            name="track_metadata_revision_request_check",
        ),
        {"schema": "library"},
    )
    user_track_ref_id: Mapped[UUID] = mapped_column(
        ForeignKey("library.track_metadata.user_track_ref_id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    operation_id: Mapped[UUID | None] = mapped_column()
    request_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
