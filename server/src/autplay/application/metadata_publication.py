"""Commit descriptive revisions and sync changes under the caller's owner publication lock."""

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import SyncEventRow, UserTrackRefRow
from autplay.adapters.postgresql.models.track_metadata import (
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.adapters.postgresql.shared_catalog import ACCOUNT_CATALOG
from autplay.adapters.postgresql.stage_credits import credit_for_recording
from autplay.application.metadata_projection import coherent_album_group, metadata_view
from autplay.domain.track_names import project_names


def save_name_revision(
    session: Session,
    ref: UserTrackRefRow,
    row: TrackMetadataRow | None,
    document: dict[str, Any],
    recording: UUID,
    *,
    artwork_sha256: str | None = None,
) -> None:
    if row is None:
        row = TrackMetadataRow(
            user_track_ref_id=ref.user_track_ref_id,
            revision=0,
            generation=1,
            candidates=[],
            state="READY",
        )
        session.add(row)
    row.document = document
    if artwork_sha256:
        row.artwork_sha256 = artwork_sha256
    _write_metadata_revision(session, ref, row, recording)


def publish_metadata_revision(
    session: Session,
    ref: UserTrackRefRow,
    row: TrackMetadataRow,
    *,
    operation_id: UUID | None = None,
    digest: bytes | None = None,
) -> None:
    recording = ref.recording_id
    if recording is None:
        recording = session.scalar(
            text(f"""
            SELECT recording_id FROM ({ACCOUNT_CATALOG}) linked
            WHERE user_id=:owner AND user_track_ref_id=:ref
            ORDER BY recording_id, audio_variant_id LIMIT 1
            """),
            {"owner": ref.user_id, "ref": ref.user_track_ref_id},
        )
    row.document = project_names(
        row.document, ref.raw_title, ref.raw_artist, credit_for_recording(session, recording)
    )
    _write_metadata_revision(session, ref, row, recording, operation_id=operation_id, digest=digest)


def _write_metadata_revision(
    session: Session,
    ref: UserTrackRefRow,
    row: TrackMetadataRow,
    recording: UUID | None,
    *,
    operation_id: UUID | None = None,
    digest: bytes | None = None,
) -> None:
    if row.document.get("album_group_v1") is not None:
        row.document = {**row.document, "album_group_v1": coherent_album_group(row.document)}
        if (
            row.document["album_group_v1"] is None
            and (row.document.get("artwork_source") or {}).get("source") == "MUSICBRAINZ"
        ):
            row.artwork_sha256 = None
            row.document = {**row.document, "artwork_source": None}
    row.revision += 1
    row.updated_at = datetime.now(UTC)
    ref.row_version += 1
    ref.updated_at = row.updated_at
    view = metadata_view(row)
    session.flush()
    session.add(
        TrackMetadataRevisionRow(
            user_track_ref_id=ref.user_track_ref_id,
            revision=row.revision,
            snapshot=view,
            operation_id=operation_id,
            request_sha256=digest,
        )
    )
    session.add(
        SyncEventRow(
            event_id=uuid5(NAMESPACE_URL, f"metadata:{ref.user_track_ref_id}:{row.revision}"),
            user_id=ref.user_id,
            origin_device_id=None,
            event_type="USER_TRACK_REF_PATCHED",
            schema_version=1,
            aggregate_type="USER_TRACK_REF",
            aggregate_id=ref.user_track_ref_id,
            operation="UPSERT",
            server_row_version=ref.row_version,
            payload={
                "recording_id": str(recording) if recording else None,
                "title": ref.raw_title,
                "artist": ref.raw_artist,
                "album": ref.raw_album,
                "duration_ms": ref.raw_duration_ms,
                "resolution_status": "RESOLVED"
                if recording and not ref.recording_id
                else ref.resolution_status,
                "metadata_v1": view,
            },
        )
    )
