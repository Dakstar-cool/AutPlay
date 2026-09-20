"""Atomic exited-provider receipt to A1 identity, upload and ingest handoff."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.controlled_discovery import (
    DiscoveryAcquisitionTarget,
    DiscoveryHandoffReceipt,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.discovery import DiscoveryEvidence
from autplay.domain.jobs import LeaseFence, RetryableJobError
from autplay.domain.resource_admission import AcquisitionClaim
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits, VerifiedStagedFile

from .discovery_runtime import (
    DISCOVERY_ACQUIRE_JOB,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from .models import (
    AcquisitionAttemptRow,
    DiscoveryCandidateRow,
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


class PostgresControlledDiscoveryRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        limits: VaultLimits | None = None,
        automatic_enabled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._sessions, self._limits = sessions, limits or VaultLimits()
        self._automatic_enabled = automatic_enabled

    def fail(
        self, candidate_id: UUID, owner: UUID, fence: LeaseFence, code: str, *, terminal: bool
    ) -> None:
        try:
            with self._sessions.begin() as session:
                PostgresBulkDiscoveryRepository(session).fail_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    fence=fence,
                    error_code=code,
                    terminal=terminal,
                )
        except BulkDiscoveryError as error:
            if error.code not in {
                "lease_fence_lost",
                "source_authorization_unavailable",
                "policy_revision_stale",
            }:
                raise

    @contextmanager
    def _transaction(
        self, candidate_id: UUID, owner: UUID, fence: LeaseFence
    ) -> Iterator[tuple[Session, PostgresBulkDiscoveryRepository, DiscoveryCandidateRow]]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            repository = PostgresBulkDiscoveryRepository(session)
            repository._require_fence(
                fence, owner, expected_key=DISCOVERY_ACQUIRE_JOB, candidate_id=candidate_id
            )
            _acquire_sync_owner_publish_lock(session, owner)
            candidate = session.scalar(
                select(DiscoveryCandidateRow)
                .where(
                    DiscoveryCandidateRow.candidate_id == candidate_id,
                    DiscoveryCandidateRow.user_id == owner,
                    DiscoveryCandidateRow.job_id == fence.job_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if candidate is None:
                raise BulkDiscoveryError("source_authorization_unavailable")
            yield session, repository, candidate
            session.flush()
            repository._require_fence(
                fence, owner, expected_key=DISCOVERY_ACQUIRE_JOB, candidate_id=candidate_id
            )

    @staticmethod
    def _target(
        candidate: DiscoveryCandidateRow, attempt: AcquisitionAttemptRow
    ) -> DiscoveryAcquisitionTarget:
        return DiscoveryAcquisitionTarget(
            candidate.candidate_id,
            attempt.acquisition_attempt_id,
            candidate.user_id,
            candidate.provider_track_id,
            candidate.provider_artist_id,
            attempt.origin,
        )

    @staticmethod
    def _receipt(
        session: Session, candidate: DiscoveryCandidateRow
    ) -> DiscoveryHandoffReceipt | None:
        if candidate.current_acquisition_attempt_id is None:
            raise BulkDiscoveryError("source_authorization_unavailable")
        attempt = session.get(AcquisitionAttemptRow, candidate.current_acquisition_attempt_id)
        if (
            attempt is None
            or attempt.candidate_id != candidate.candidate_id
            or attempt.job_id != candidate.job_id
        ):
            raise BulkDiscoveryError("source_authorization_unavailable")
        upload = session.scalar(
            select(UploadSessionRow).where(
                UploadSessionRow.source_acquisition_attempt_id == attempt.acquisition_attempt_id
            )
        )
        if upload is None:
            return None
        staging = session.scalar(
            select(ProviderStagingRow).where(
                ProviderStagingRow.upload_session_id == upload.upload_session_id
            )
        )
        job = session.get(JobRow, upload.job_id) if upload.job_id is not None else None
        if (
            staging is None
            or staging.state != "HANDED_OFF"
            or upload.actor_kind != "PROVIDER"
            or upload.user_id != candidate.user_id
            or upload.device_id is not None
            or upload.source_candidate_id != candidate.candidate_id
            or upload.source_internet_acquisition_id is not None
            or upload.target_recording_id != candidate.recording_id
            or upload.staging_key != staging.staging_key
            or (
                candidate.staging_key != staging.staging_key
                and not (
                    candidate.staging_key is None
                    and candidate.acquisition_state == "READY"
                    and attempt.state == "COMPLETED"
                    and upload.state in {"COMMITTED", "REUSED"}
                    and candidate.audio_variant_id == upload.audio_variant_id
                    and upload.audio_variant_id is not None
                )
            )
            or upload.declared_sha256 is None
            or upload.declared_sha256 != staging.sha256
            or upload.expected_size != staging.byte_size
            or staging.resource_type != "DISCOVERY_ACQUISITION"
            or staging.acquisition_id != attempt.acquisition_attempt_id
            or staging.user_id != candidate.user_id
            or staging.job_id != candidate.job_id
            or job is None
            or job.job_type != "vault.ingest"
            or job.schema_version != 1
            or job.user_id != candidate.user_id
            or job.payload != {"upload_session_id": str(upload.upload_session_id)}
        ):
            raise BulkDiscoveryError("discovery_handoff_conflict")
        return DiscoveryHandoffReceipt(
            PostgresControlledDiscoveryRepository._target(candidate, attempt),
            staging.execution_id,
            upload.upload_session_id,
            job.job_id,
            upload.target_recording_id,
            upload.expected_size,
            Sha256Digest(upload.declared_sha256),
        )

    def prepare(
        self, candidate_id: UUID, owner: UUID, fence: LeaseFence
    ) -> DiscoveryAcquisitionTarget | DiscoveryHandoffReceipt:
        with self._transaction(candidate_id, owner, fence) as (session, repository, candidate):
            # Completed ingest may already mark the attempt COMPLETED. A committed
            # receipt is returned before live authority checks or new provider bytes.
            receipt = self._receipt(session, candidate)
            if receipt is not None:
                return receipt
            claimed = repository.claim_acquisition(
                candidate_id=candidate_id,
                owner_user_id=owner,
                fence=fence,
                automatic_enabled=self._automatic_enabled(),
            )
            if claimed is None:
                raise BulkDiscoveryError("discovery_handoff_conflict")
            attempt = repository._require_current_attempt(candidate, fence=fence)
            return self._target(candidate, attempt)

    def _authorize(
        self,
        repository: PostgresBulkDiscoveryRepository,
        candidate: DiscoveryCandidateRow,
        claim: AcquisitionClaim,
    ) -> AcquisitionAttemptRow:
        if (
            claim.resource_type != "DISCOVERY_ACQUISITION"
            or claim.acquisition_id != candidate.current_acquisition_attempt_id
            or candidate.acquisition_state != "ACQUIRING"
        ):
            raise BulkDiscoveryError("source_authorization_unavailable")
        attempt = repository._require_current_attempt(candidate, fence=claim.fence)
        repository._require_operator_gate(attempt, automatic_enabled=self._automatic_enabled())
        repository._require_provider()
        repository._require_candidate_artist(candidate)
        return attempt

    @staticmethod
    def _validate_identity(
        session: Session, candidate: DiscoveryCandidateRow, attempt: AcquisitionAttemptRow
    ) -> None:
        # Recording/ref locks are held by materialization; also cover the reused
        # library row and reject removal after the immutable selection was made.
        session.execute(
            select(RecordingRow.recording_id)
            .where(RecordingRow.recording_id == candidate.recording_id)
            .with_for_update()
        )
        session.execute(
            select(UserTrackRefRow.user_track_ref_id)
            .where(UserTrackRefRow.user_track_ref_id == candidate.user_track_ref_id)
            .with_for_update()
        )
        entries = session.scalars(
            select(LibraryEntryRow)
            .where(
                LibraryEntryRow.user_id == candidate.user_id,
                LibraryEntryRow.user_track_ref_id == candidate.user_track_ref_id,
            )
            .order_by(LibraryEntryRow.library_entry_id)
            .with_for_update()
        ).all()
        if any(
            row.removed_at is not None and row.removed_at >= attempt.created_at for row in entries
        ):
            raise BulkDiscoveryError("source_authorization_unavailable")
        active = session.scalar(
            select(UserTrackRefRow.user_track_ref_id)
            .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
            .join(
                LibraryEntryRow,
                LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .where(
                UserTrackRefRow.user_track_ref_id == candidate.user_track_ref_id,
                UserTrackRefRow.user_id == candidate.user_id,
                UserTrackRefRow.deleted_at.is_(None),
                UserTrackRefRow.resolution_status == "RESOLVED",
                UserTrackRefRow.recording_id == candidate.recording_id,
                RecordingRow.deleted_at.is_(None),
                LibraryEntryRow.library_entry_id == candidate.library_entry_id,
                LibraryEntryRow.user_id == candidate.user_id,
                LibraryEntryRow.removed_at.is_(None),
                ~exists().where(RecordingRedirectRow.source_recording_id == candidate.recording_id),
            )
        )
        if active is None:
            raise BulkDiscoveryError("source_authorization_unavailable")

    def handoff(
        self,
        claim: AcquisitionClaim,
        target: DiscoveryAcquisitionTarget,
        execution_id: UUID,
        verified: VerifiedStagedFile,
        evidence: DiscoveryEvidence,
    ) -> DiscoveryHandoffReceipt:
        chunks = (
            verified.byte_size + self._limits.max_chunk_bytes - 1
        ) // self._limits.max_chunk_bytes
        if (
            type(verified.byte_size) is not int
            or not 1 <= verified.byte_size <= self._limits.max_object_bytes
            or chunks > self._limits.max_chunks
        ):
            raise BulkDiscoveryError("discovery_content_invalid")
        if (
            claim.resource_type != "DISCOVERY_ACQUISITION"
            or claim.acquisition_id != target.acquisition_attempt_id
        ):
            raise BulkDiscoveryError("discovery_handoff_conflict")
        with self._transaction(target.candidate_id, target.user_id, claim.fence) as (
            session,
            repository,
            candidate,
        ):
            prior = self._receipt(session, candidate)
            if prior is not None:
                if (prior.target, prior.execution_id, prior.byte_size, prior.sha256) != (
                    target,
                    execution_id,
                    verified.byte_size,
                    verified.sha256,
                ):
                    raise BulkDiscoveryError("discovery_handoff_conflict")
                return prior
            attempt = self._authorize(repository, candidate, claim)
            if self._target(candidate, attempt) != target or (
                evidence.provider_track_id != target.provider_track_id
                or evidence.provider_artist_id != target.provider_artist_id
                or not evidence.acquisition_allowed
            ):
                raise BulkDiscoveryError("discovery_not_eligible")
            if session.scalar(
                select(
                    exists().where(
                        ResourceIoExecutionRow.kind == "PROVIDER",
                        ResourceIoExecutionRow.actual_target_id == claim.acquisition_id,
                        ResourceIoExecutionRow.closed_at.is_(None),
                    )
                )
            ):
                raise RetryableJobError("discovery_staging_busy")
            staging = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == execution_id)
                .with_for_update()
                .execution_options(populate_existing=True)
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
                    target.user_id,
                    claim.resource_type,
                    claim.acquisition_id,
                    claim.fence.job_id,
                    claim.fence.worker_id,
                    claim.fence.attempt_no,
                )
            ):
                raise BulkDiscoveryError("discovery_staging_unavailable")
            now = repository._authority_now()
            staging.state, staging.sealed_at, staging.updated_at = "SEALED", now, now
            staging.byte_size, staging.sha256 = verified.byte_size, verified.sha256.value
            session.flush()
            prepared = repository._create_ingest_locked(
                candidate,
                attempt,
                evidence,
                OpaqueStorageKey(staging.staging_key),
                verified,
                self._limits,
            )
            staging.upload_session_id, staging.state = prepared.upload_session_id, "HANDED_OFF"
            staging.handed_off_at = staging.updated_at = repository._authority_now()
            session.flush()
            self._validate_identity(session, candidate, attempt)
            repository._require_current_attempt(candidate, fence=claim.fence)
            repository._require_operator_gate(attempt, automatic_enabled=self._automatic_enabled())
            repository._require_provider()
            repository._require_candidate_artist(candidate)
            result = self._receipt(session, candidate)
            if result is None:
                raise BulkDiscoveryError("discovery_handoff_conflict")
            return result
