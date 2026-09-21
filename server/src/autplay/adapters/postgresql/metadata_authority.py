"""Fresh owner/job/library authority for every registered metadata boundary."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autplay.application.job_worker import JobLeaseLost
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.vault_uploads import VaultNotFoundError, VaultPrincipal
from autplay.domain.jobs import LeaseFence
from autplay.domain.metadata_execution import MetadataAudioTarget
from autplay.domain.vault import VerifiedStagedFile

from .models import AudioVariantRow, JobRow, LibraryEntryRow, UserAccountRow, UserTrackRefRow
from .models.track_metadata import TrackMetadataRow
from .resource_limits import lock_resource_admission
from .vault_runtime import PostgresVaultRuntime


def lock_metadata_job(
    session: Session,
    ref_id: UUID,
    generation: int,
    fence: LeaseFence,
) -> tuple[UserTrackRefRow, TrackMetadataRow, JobRow]:
    lock_resource_admission(session)
    user_id = session.scalar(select(JobRow.user_id).where(JobRow.job_id == fence.job_id))
    if user_id is None:
        raise JobLeaseLost
    _acquire_sync_owner_publish_lock(session, user_id)
    job = session.get(JobRow, fence.job_id, with_for_update=True, populate_existing=True)
    ref = session.scalar(
        select(UserTrackRefRow)
        .join(
            LibraryEntryRow,
            LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
        )
        .where(
            UserTrackRefRow.user_track_ref_id == ref_id,
            UserTrackRefRow.user_id == user_id,
            UserTrackRefRow.deleted_at.is_(None),
            LibraryEntryRow.user_id == user_id,
            LibraryEntryRow.removed_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = session.get(TrackMetadataRow, ref_id, with_for_update=True, populate_existing=True)
    account = session.get(UserAccountRow, user_id, populate_existing=True)
    now = session.scalar(select(func.clock_timestamp()))
    if (
        not isinstance(now, datetime)
        or job is None
        or ref is None
        or row is None
        or account is None
        or account.status != "ACTIVE"
        or account.deleted_at is not None
        or job.job_type != "music.metadata.enrich"
        or job.schema_version != 1
        or job.state != "RUNNING"
        or job.lease_owner != fence.worker_id
        or job.attempt_count != fence.attempt_no
        or job.lease_deadline is None
        or job.lease_deadline <= now
        or job.cancel_requested_at is not None
        or row.job_id != fence.job_id
        or row.generation != generation
        or not isinstance(job.payload, dict)
        or job.payload.get("user_track_ref_id") != str(ref_id)
        or job.payload.get("generation") != generation
        or type(job.payload.get("authority_generation")) is not int
        or job.payload["authority_generation"] != account.authority_generation
    ):
        raise JobLeaseLost
    return ref, row, job


def metadata_audio(session: Session, ref: UserTrackRefRow) -> MetadataAudioTarget | None:
    runtime = PostgresVaultRuntime(session)
    try:
        # Publication uses the same nonblocking canonical/object locks. A conflict
        # fails this short transaction instead of reversing the owner/ref order.
        runtime.resolve_owner_playback_variant(ref.user_id, ref.user_track_ref_id)
        variant_id = runtime._resolve_publication_variant(ref.user_id, ref.user_track_ref_id)
        stream = runtime.resolve_stream(VaultPrincipal(ref.user_id, UUID(int=0)), variant_id)
    except VaultNotFoundError:
        return None
    variant = session.get(AudioVariantRow, variant_id, populate_existing=True)
    if variant is None or ref.recording_id != variant.recording_id:
        raise JobLeaseLost
    return MetadataAudioTarget(
        variant.recording_id,
        variant_id,
        variant.vault_object_id,
        stream.storage_key,
        VerifiedStagedFile(stream.byte_size, stream.sha256),
    )
