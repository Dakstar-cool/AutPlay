"""Fenced Internet identity preparation and atomic provider-to-ingest handoff."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.internet_acquisition import (
    InternetAcquisitionTarget,
    InternetHandoffReceipt,
)
from autplay.application.job_worker import JobLeaseLost
from autplay.application.music_library import MusicError, MusicLibraryService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.discovery import DiscoveryError
from autplay.domain.jobs import JobKey, RetryableJobError, TerminalJobError
from autplay.domain.resource_admission import AcquisitionClaim, ResourceAdmissionError
from autplay.domain.vault import Sha256Digest, VaultLimits, VerifiedStagedFile
from autplay.ports.jobs import EnqueueJob

from .acquisition_authority import acquisition_generation, require_acquisition_session
from .internet_ingest_authority import require_internet_ingest_authority
from .jobs_runtime import PostgresJobRepository
from .models import (
    InternetAcquisitionRow,
    JobRow,
    LibraryEntryRow,
    ProviderStagingRow,
    RecordingRedirectRow,
    RecordingRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from .models.resource_admission import ResourceIoExecutionRow
from .resource_limits import lock_resource_admission


class PostgresInternetAcquisitionRepository:
    def __init__(
        self, sessions: sessionmaker[Session], *, limits: VaultLimits | None = None
    ) -> None:
        self._sessions, self._limits = sessions, limits or VaultLimits()

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise JobLeaseLost
        return now

    def _job(self, session: Session, claim: AcquisitionClaim) -> JobRow:
        job = session.scalar(
            select(JobRow)
            .where(JobRow.job_id == claim.fence.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self._now(session)
        if (
            claim.resource_type != "INTERNET_ACQUISITION"
            or job is None
            or job.job_type != "music.internet.acquire"
            or job.schema_version != 1
            or not isinstance(job.payload, dict)
            or job.payload.get("acquisition_id") != str(claim.acquisition_id)
            or job.state != "RUNNING"
            or job.lease_owner != claim.fence.worker_id
            or job.attempt_count != claim.fence.attempt_no
            or job.cancel_requested_at is not None
            or job.lease_deadline is None
            or job.lease_deadline <= now
        ):
            raise JobLeaseLost
        return job

    @contextmanager
    def _transaction(
        self, claim: AcquisitionClaim
    ) -> Iterator[tuple[Session, InternetAcquisitionRow]]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            job = self._job(session, claim)
            if job.user_id is None:
                raise JobLeaseLost
            _acquire_sync_owner_publish_lock(session, job.user_id)
            source = session.scalar(
                select(InternetAcquisitionRow)
                .where(
                    InternetAcquisitionRow.acquisition_id == claim.acquisition_id,
                    InternetAcquisitionRow.job_id == job.job_id,
                    InternetAcquisitionRow.user_id == job.user_id,
                )
                .with_for_update()
            )
            if source is None:
                raise JobLeaseLost
            yield session, source
            session.flush()
            self._job(session, claim)

    def _authorize(self, session: Session, source: InternetAcquisitionRow) -> None:
        try:
            if acquisition_generation(session, source.user_id) != source.authority_generation:
                raise ResourceAdmissionError("source_authorization_unavailable")
            require_acquisition_session(
                session,
                user_id=source.user_id,
                device_id=source.device_id,
                family_id=source.source_session_family_id,
                mode=source.source_session_mode,
                now=self._now(session),
            )
        except ResourceAdmissionError as error:
            raise TerminalJobError("source_authorization_unavailable") from error

    @staticmethod
    def _receipt(session: Session, source: InternetAcquisitionRow) -> InternetHandoffReceipt | None:
        if source.upload_id is None:
            if (
                session.scalar(
                    select(UploadSessionRow.upload_session_id).where(
                        UploadSessionRow.source_internet_acquisition_id == source.acquisition_id
                    )
                )
                is not None
            ):
                raise TerminalJobError("music_handoff_conflict")
            return None
        upload = session.get(UploadSessionRow, source.upload_id)
        staging = session.scalar(
            select(ProviderStagingRow).where(
                ProviderStagingRow.upload_session_id == source.upload_id
            )
        )
        if (
            upload is None
            or staging is None
            or staging.state != "HANDED_OFF"
            or upload.actor_kind != "INTERNET"
            or upload.user_id != source.user_id
            or upload.source_internet_acquisition_id != source.acquisition_id
            or source.user_track_ref_id is None
            or upload.job_id is None
            or upload.declared_sha256 is None
            or upload.staging_key != staging.staging_key
            or staging.user_id != source.user_id
            or staging.acquisition_id != source.acquisition_id
            or staging.resource_type != "INTERNET_ACQUISITION"
            or staging.sha256 != upload.declared_sha256
            or staging.byte_size != upload.expected_size
        ):
            raise TerminalJobError("music_handoff_conflict")
        return InternetHandoffReceipt(
            InternetAcquisitionTarget(
                source.acquisition_id,
                source.user_id,
                source.user_track_ref_id,
                upload.target_recording_id,
                source.candidate_id,
            ),
            staging.execution_id,
            upload.upload_session_id,
            upload.job_id,
            upload.expected_size,
            Sha256Digest(upload.declared_sha256),
        )

    def prepare(
        self, claim: AcquisitionClaim
    ) -> InternetAcquisitionTarget | InternetHandoffReceipt:
        with self._transaction(claim) as (session, source):
            existing = self._receipt(session, source)
            if existing is not None:
                return existing
            self._authorize(session, source)
            if source.state not in {"QUEUED", "DOWNLOADING"}:
                raise TerminalJobError("music_acquisition_unavailable")
            if source.user_track_ref_id is None:
                snapshot = source.selected_snapshot
                title, artist, duration = (
                    snapshot.get("title"),
                    snapshot.get("artist"),
                    snapshot.get("duration_ms"),
                )
                if (
                    not isinstance(title, str)
                    or not 1 <= len(title) <= 500
                    or not isinstance(artist, str)
                    or not 1 <= len(artist) <= 500
                    or type(duration) is not int
                    or not 1 <= duration <= 7_200_000
                ):
                    raise TerminalJobError("music_candidate_invalid")
                ref = UserTrackRefRow(
                    user_id=source.user_id,
                    raw_title=title,
                    raw_artist=artist,
                    raw_duration_ms=duration,
                    resolution_status="UNRESOLVED",
                )
                session.add(ref)
                session.flush()
                source.user_track_ref_id = ref.user_track_ref_id
                session.add(
                    LibraryEntryRow(
                        user_id=source.user_id,
                        user_track_ref_id=ref.user_track_ref_id,
                        source="SEARCH",
                        availability_status="PENDING",
                    )
                )
                session.flush()
            # Existing identity writers lock Recording before the ref; match that order.
            recording = session.scalar(
                select(UserTrackRefRow.recording_id).where(
                    UserTrackRefRow.user_track_ref_id == source.user_track_ref_id
                )
            )
            if recording is not None:
                session.execute(
                    select(RecordingRow.recording_id)
                    .where(RecordingRow.recording_id == recording)
                    .with_for_update()
                )
            try:
                recording = MusicLibraryService.prepare_in_transaction(
                    session, source.user_id, source.user_track_ref_id
                )
            except MusicError as error:
                if error.retryable:
                    raise RetryableJobError(error.code) from error
                code = (
                    "source_authorization_unavailable"
                    if error.status_code in {403, 404}
                    else error.code
                )
                raise TerminalJobError(code) from error
            target = InternetAcquisitionTarget(
                source.acquisition_id,
                source.user_id,
                source.user_track_ref_id,
                recording,
                source.candidate_id,
            )
            self._target(session, source, target)
            source.state, source.updated_at = "DOWNLOADING", self._now(session)
            session.flush()
            self._authorize(session, source)
            return target

    @staticmethod
    def _target(
        session: Session, source: InternetAcquisitionRow, target: InternetAcquisitionTarget
    ) -> None:
        if (target.acquisition_id, target.user_id, target.ref_id, target.candidate_id) != (
            source.acquisition_id,
            source.user_id,
            source.user_track_ref_id,
            source.candidate_id,
        ):
            raise TerminalJobError("music_handoff_conflict")
        session.execute(
            select(RecordingRow.recording_id)
            .where(RecordingRow.recording_id == target.recording_id)
            .with_for_update()
        )
        session.execute(
            select(UserTrackRefRow.user_track_ref_id)
            .where(UserTrackRefRow.user_track_ref_id == target.ref_id)
            .with_for_update()
        )
        session.execute(
            select(LibraryEntryRow.library_entry_id)
            .where(
                LibraryEntryRow.user_track_ref_id == target.ref_id,
                LibraryEntryRow.user_id == target.user_id,
            )
            .order_by(LibraryEntryRow.library_entry_id)
            .with_for_update()
        )
        active = session.scalar(
            select(UserTrackRefRow.user_track_ref_id)
            .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
            .join(
                LibraryEntryRow,
                LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .where(
                UserTrackRefRow.user_track_ref_id == target.ref_id,
                UserTrackRefRow.user_id == target.user_id,
                UserTrackRefRow.deleted_at.is_(None),
                UserTrackRefRow.resolution_status == "RESOLVED",
                UserTrackRefRow.recording_id == target.recording_id,
                RecordingRow.deleted_at.is_(None),
                LibraryEntryRow.removed_at.is_(None),
                LibraryEntryRow.user_id == target.user_id,
                ~exists().where(RecordingRedirectRow.source_recording_id == target.recording_id),
            )
        )
        if active is None:
            raise TerminalJobError("source_authorization_unavailable")

    def handoff(
        self,
        claim: AcquisitionClaim,
        target: InternetAcquisitionTarget,
        execution_id: UUID,
        verified: VerifiedStagedFile,
    ) -> InternetHandoffReceipt:
        if (
            type(verified.byte_size) is not int
            or not 1 <= verified.byte_size <= self._limits.max_object_bytes
        ):
            raise TerminalJobError("music_download_invalid")
        with self._transaction(claim) as (session, source):
            existing = self._receipt(session, source)
            if existing is not None:
                if (
                    existing.target,
                    existing.execution_id,
                    existing.byte_size,
                    existing.sha256,
                ) != (target, execution_id, verified.byte_size, verified.sha256):
                    raise TerminalJobError("music_handoff_conflict")
                return existing
            self._authorize(session, source)
            self._target(session, source, target)
            if source.state != "DOWNLOADING":
                raise TerminalJobError("music_acquisition_unavailable")
            if session.scalar(
                select(
                    exists().where(
                        ResourceIoExecutionRow.kind == "PROVIDER",
                        ResourceIoExecutionRow.actual_target_id == source.acquisition_id,
                        ResourceIoExecutionRow.closed_at.is_(None),
                    )
                )
            ):
                raise RetryableJobError("music_staging_busy")
            staging = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == execution_id)
                .with_for_update()
            )
            if (
                staging is None
                or staging.state != "EXITED"
                or staging.exit_code != 0
                or staging.closure_kind not in {"PROCESS_EXIT", "SUPERVISOR_EXIT"}
                or (
                    staging.user_id,
                    staging.resource_type,
                    staging.acquisition_id,
                    staging.job_id,
                    staging.job_worker_id,
                    staging.job_attempt,
                )
                != (
                    source.user_id,
                    claim.resource_type,
                    claim.acquisition_id,
                    claim.fence.job_id,
                    claim.fence.worker_id,
                    claim.fence.attempt_no,
                )
            ):
                raise TerminalJobError("music_staging_unavailable")
            now = self._now(session)
            staging.state, staging.sealed_at, staging.updated_at = "SEALED", now, now
            staging.byte_size, staging.sha256 = verified.byte_size, verified.sha256.value
            session.flush()
            upload_id = uuid5(NAMESPACE_URL, f"autplay:internet-ingest:v1:{source.acquisition_id}")
            job = PostgresJobRepository(session).enqueue(
                EnqueueJob(
                    key=JobKey("vault.ingest", 1),
                    user_id=source.user_id,
                    payload={"upload_session_id": str(upload_id)},
                    idempotency_scope=f"internet-ingest:{source.acquisition_id}",
                    idempotency_key=str(upload_id),
                )
            )
            chunks = (
                verified.byte_size + self._limits.max_chunk_bytes - 1
            ) // self._limits.max_chunk_bytes
            if chunks > self._limits.max_chunks:
                raise TerminalJobError("music_download_invalid")
            request = json.dumps(
                {
                    "source": str(source.acquisition_id),
                    "execution": str(execution_id),
                    "recording": str(target.recording_id),
                    "size": verified.byte_size,
                    "sha256": verified.sha256.hex,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            upload = UploadSessionRow(
                upload_session_id=upload_id,
                user_id=source.user_id,
                actor_kind="INTERNET",
                source_internet_acquisition_id=source.acquisition_id,
                target_recording_id=target.recording_id,
                idempotency_key=f"internet:{source.acquisition_id}",
                request_hash=hashlib.sha256(request.encode()).digest(),
                declared_sha256=verified.sha256.value,
                expected_size=verified.byte_size,
                received_size=verified.byte_size,
                chunk_size=self._limits.max_chunk_bytes,
                max_chunks=self._limits.max_chunks,
                chunk_count=chunks,
                staging_key=staging.staging_key,
                state="SEALED",
                job_id=job.job_id,
                sealed_at=now,
                expires_at=now + timedelta(hours=24),
            )
            session.add(upload)
            session.flush()
            source.upload_id, source.state, source.updated_at = upload_id, "PROCESSING", now
            staging.upload_session_id, staging.state, staging.handed_off_at = (
                upload_id,
                "HANDED_OFF",
                now,
            )
            session.flush()
            try:
                require_internet_ingest_authority(session, upload)
            except DiscoveryError as error:
                raise TerminalJobError(error.code) from error
            result = self._receipt(session, source)
            if result is None:
                raise TerminalJobError("music_handoff_conflict")
            return result
