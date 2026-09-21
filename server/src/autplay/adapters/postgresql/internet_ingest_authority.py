"""Original Internet selection authority survives handoff, never account recovery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.acquisition_authority import (
    acquisition_generation,
    require_acquisition_session,
)
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    JobRow,
    LibraryEntryRow,
    RecordingRedirectRow,
    RecordingRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.discovery import DiscoveryError
from autplay.domain.resource_admission import ResourceAdmissionError


def lock_internet_ingest_scope(session: Session, upload_id: UUID) -> None:
    """Caller holds admission and ingest job; source job precedes owner/upload locks."""
    source = session.execute(
        select(InternetAcquisitionRow.job_id, InternetAcquisitionRow.user_id)
        .join(
            UploadSessionRow,
            UploadSessionRow.source_internet_acquisition_id
            == InternetAcquisitionRow.acquisition_id,
        )
        .where(UploadSessionRow.upload_session_id == upload_id)
    ).one_or_none()
    if source is None:
        return
    session.execute(select(JobRow.job_id).where(JobRow.job_id == source.job_id).with_for_update())
    _acquire_sync_owner_publish_lock(session, source.user_id)


def require_internet_ingest_authority(
    session: Session, upload: UploadSessionRow
) -> InternetAcquisitionRow:
    """Caller owns admission/source-job/owner locks; no current source worker is required."""
    source_id = upload.source_internet_acquisition_id
    if (
        upload.actor_kind != "INTERNET"
        or source_id is None
        or upload.device_id is not None
        or upload.source_candidate_id is not None
        or upload.source_acquisition_attempt_id is not None
    ):
        raise DiscoveryError("source_authorization_unavailable")
    source = session.scalar(
        select(InternetAcquisitionRow)
        .where(InternetAcquisitionRow.acquisition_id == source_id)
        .execution_options(populate_existing=True)
    )
    if (
        source is None
        or source.user_id != upload.user_id
        or source.upload_id != upload.upload_session_id
        or source.user_track_ref_id is None
        or source.state not in {"PROCESSING", "READY"}
        or source.authority_generation is None
    ):
        raise DiscoveryError("source_authorization_unavailable")
    job = session.get(JobRow, source.job_id, populate_existing=True)
    if (
        job is None
        or job.user_id != source.user_id
        or job.job_type != "music.internet.acquire"
        or job.schema_version != 1
        or not isinstance(job.payload, dict)
        or job.payload.get("acquisition_id") != str(source_id)
        or job.state not in {"RUNNING", "RETRY_WAIT", "COMPLETED"}
        or job.cancel_requested_at is not None
    ):
        raise DiscoveryError("source_authorization_unavailable")
    # Catalog/identity and direct library writers also operate outside admission.
    # Match their Recording -> ref -> entry order, then use a new statement snapshot.
    session.execute(
        select(RecordingRow.recording_id)
        .where(RecordingRow.recording_id == upload.target_recording_id)
        .with_for_update()
    )
    session.execute(
        select(UserTrackRefRow.user_track_ref_id)
        .where(UserTrackRefRow.user_track_ref_id == source.user_track_ref_id)
        .with_for_update()
    )
    session.execute(
        select(LibraryEntryRow.library_entry_id)
        .where(
            LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
            LibraryEntryRow.user_id == source.user_id,
        )
        .order_by(LibraryEntryRow.library_entry_id)
        .with_for_update()
    )
    now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise DiscoveryError("source_authorization_unavailable")
    try:
        if acquisition_generation(session, source.user_id) != source.authority_generation:
            raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
        require_acquisition_session(
            session,
            user_id=source.user_id,
            device_id=source.device_id,
            family_id=source.source_session_family_id,
            mode=source.source_session_mode,
            now=now,
        )
    except ResourceAdmissionError as error:
        raise DiscoveryError("source_authorization_unavailable") from error
    owned = session.scalar(
        select(UserTrackRefRow.user_track_ref_id)
        .join(
            LibraryEntryRow, LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id
        )
        .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
        .where(
            UserTrackRefRow.user_track_ref_id == source.user_track_ref_id,
            UserTrackRefRow.user_id == source.user_id,
            UserTrackRefRow.recording_id == upload.target_recording_id,
            UserTrackRefRow.resolution_status == "RESOLVED",
            UserTrackRefRow.deleted_at.is_(None),
            LibraryEntryRow.user_id == source.user_id,
            LibraryEntryRow.removed_at.is_(None),
            RecordingRow.deleted_at.is_(None),
            ~exists().where(RecordingRedirectRow.source_recording_id == RecordingRow.recording_id),
        )
    )
    if owned is None:
        raise DiscoveryError("source_authorization_unavailable")
    return source
