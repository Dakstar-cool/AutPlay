"""Durable A1B preview/start persistence using the existing PostgreSQL job queue."""

from __future__ import annotations

import hashlib
import hmac
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.application.identity_evidence import (
    candidate_aggregate_sha256,
    canonical_query_snapshot,
)
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    ProviderArtistTracks,
)
from autplay.domain.import_identity import (
    CANDIDATE_GENERATION_VERSION,
    FEATURE_EXTRACTOR_VERSIONS,
    MATCHER_VERSION,
    NORMALIZATION_VERSION,
    normalize_text,
)
from autplay.domain.jobs import JobKey, JsonValue, LeaseFence
from autplay.domain.vault import OpaqueStorageKey, VaultLimits, VerifiedStagedFile
from autplay.ports.jobs import EnqueueJob

from .identity_decisions import CreateRecordingReviewCommand, execute_create_recording_review
from .import_runtime import PostgresImportRepository
from .jobs_runtime import PostgresJobRepository
from .models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    AudioFingerprintRow,
    AudioVariantRow,
    BulkOperationItemRow,
    BulkOperationRow,
    DiscoveryCandidateRow,
    ExternalReferenceRow,
    ImportJobRow,
    JobRow,
    LibraryEntryRow,
    MatchDecisionRow,
    RecordingRow,
    SourceProviderRow,
    UploadSessionRow,
    UserTrackRefExternalReferenceRow,
    UserTrackRefRow,
    VaultObjectRow,
    VaultReplicaRow,
)

JAMENDO_PROVIDER_ID = UUID("426dc183-ab26-5a6e-9350-3f8bb57cd575")
DISCOVERY_ACQUIRE_JOB = JobKey("discovery.acquire", 1)
STANDARD_ANALYSIS_JOB = JobKey("audio.standard_analysis", 1)


class BulkDiscoveryError(RuntimeError):
    """Owner-safe manual bulk operation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class BulkPreviewResult:
    bulk_operation_id: UUID
    planned_candidate_count: int
    downloadable_candidate_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class BulkStartResult:
    bulk_operation_id: UUID
    state: str
    queued_count: int
    ready_count: int
    failed_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class AcquisitionTarget:
    candidate_id: UUID
    owner_user_id: UUID
    provider_track_id: str


@dataclass(frozen=True, slots=True)
class AcquisitionPrepared:
    upload_session_id: UUID
    ingest_job_id: UUID
    recording_id: UUID


class PostgresBulkDiscoveryRepository:
    """Persist preview evidence and explicitly enqueue selected candidates."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_preview(
        self,
        *,
        owner_user_id: UUID,
        import_job_id: UUID,
        operation_id: UUID,
        resolutions: tuple[BulkArtistResolution, ...],
        pages: tuple[ProviderArtistTracks, ...],
    ) -> BulkPreviewResult:
        """Persist bounded metadata evidence without starting acquisition."""

        self._require_import(owner_user_id, import_job_id)
        self._require_provider()
        exact_ids = {
            item.provider_artist.provider_artist_id
            for item in resolutions
            if item.provider_artist is not None
        }
        if (
            not 1 <= len(exact_ids) <= 20
            or {page.provider_artist_id for page in pages} != exact_ids
        ):
            raise BulkDiscoveryError("discovery_artist_selection_invalid")
        tracks = tuple(track for page in pages for track in page.tracks)
        if not 1 <= len(tracks) <= 200 or len({track.provider_track_id for track in tracks}) != len(
            tracks
        ):
            raise BulkDiscoveryError("discovery_bulk_preview_invalid")
        provider_artist_ids: list[JsonValue] = list(sorted(exact_ids))
        provider_track_ids: list[JsonValue] = [track.provider_track_id for track in tracks]
        request_sha256 = _request_hash(
            {
                "action": "bulk_preview",
                "import_job_id": str(import_job_id),
                "operation_id": str(operation_id),
                "provider_artist_ids": provider_artist_ids,
                "provider_track_ids": provider_track_ids,
                "schema_version": 1,
            }
        )
        bulk_id = uuid5(
            NAMESPACE_URL,
            f"autplay:a1b-bulk-preview-v1:{owner_user_id}:{operation_id}",
        )
        inserted_id = self._session.scalar(
            insert(BulkOperationRow)
            .values(
                bulk_operation_id=bulk_id,
                user_id=owner_user_id,
                import_job_id=import_job_id,
                operation_id=operation_id,
                request_sha256=request_sha256,
                state="PREVIEW",
                selected_artist_count=len(exact_ids),
                planned_candidate_count=len(tracks),
            )
            .on_conflict_do_nothing(
                index_elements=[BulkOperationRow.user_id, BulkOperationRow.operation_id]
            )
            .returning(BulkOperationRow.bulk_operation_id)
        )
        replayed = inserted_id is None
        operation = self._session.scalar(
            select(BulkOperationRow)
            .where(
                BulkOperationRow.user_id == owner_user_id,
                BulkOperationRow.operation_id == operation_id,
            )
            .with_for_update()
        )
        if operation is None or not hmac.compare_digest(operation.request_sha256, request_sha256):
            raise BulkDiscoveryError("operation_conflict")
        if operation.import_job_id != import_job_id:
            raise BulkDiscoveryError("operation_conflict")

        downloadable = 0
        for ordinal, track in enumerate(tracks):
            candidate_id = uuid5(
                NAMESPACE_URL,
                f"autplay:a1b-candidate-v1:{owner_user_id}:{JAMENDO_PROVIDER_ID}:GLOBAL:{track.provider_track_id}",
            )
            initial_disposition = "SELECTABLE" if track.acquisition_allowed else "UNAVAILABLE"
            self._session.execute(
                insert(DiscoveryCandidateRow)
                .values(
                    candidate_id=candidate_id,
                    user_id=owner_user_id,
                    provider_id=JAMENDO_PROVIDER_ID,
                    market_scope="GLOBAL",
                    provider_track_id=track.provider_track_id,
                    provider_artist_id=track.provider_artist_id,
                    title=track.title,
                    artist=track.artist,
                    album=track.album,
                    duration_seconds=track.duration_seconds,
                    license_url=track.license_url,
                    share_url=track.share_url,
                    disposition=initial_disposition,
                    acquisition_state="NOT_REQUESTED",
                    source_authorization_revision=1,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DiscoveryCandidateRow.user_id,
                        DiscoveryCandidateRow.provider_id,
                        DiscoveryCandidateRow.market_scope,
                        DiscoveryCandidateRow.provider_track_id,
                    ]
                )
            )
            candidate = self._session.scalar(
                select(DiscoveryCandidateRow)
                .where(
                    DiscoveryCandidateRow.user_id == owner_user_id,
                    DiscoveryCandidateRow.provider_id == JAMENDO_PROVIDER_ID,
                    DiscoveryCandidateRow.market_scope == "GLOBAL",
                    DiscoveryCandidateRow.provider_track_id == track.provider_track_id,
                )
                .with_for_update()
            )
            if candidate is None or candidate.provider_artist_id != track.provider_artist_id:
                raise BulkDiscoveryError("discovery_provider_response_invalid")
            if candidate.acquisition_state == "NOT_REQUESTED":
                candidate.title = track.title
                candidate.artist = track.artist
                candidate.album = track.album
                candidate.duration_seconds = track.duration_seconds
                candidate.license_url = track.license_url
                candidate.share_url = track.share_url
                candidate.disposition = initial_disposition
                candidate.acquisition_state = "NOT_REQUESTED"
                candidate.job_id = None
                candidate.error_code = None
                candidate.updated_at = datetime.now(UTC)
                candidate.row_version += 1
            if candidate.disposition in {"SELECTABLE", "SELECTED", "ALREADY_IN_LIBRARY"}:
                downloadable += 1
            self._session.execute(
                insert(BulkOperationItemRow)
                .values(
                    bulk_operation_id=operation.bulk_operation_id,
                    candidate_id=candidate.candidate_id,
                    ordinal=ordinal,
                )
                .on_conflict_do_nothing()
            )
        self._session.flush()
        return BulkPreviewResult(
            operation.bulk_operation_id,
            operation.planned_candidate_count,
            downloadable,
            replayed,
        )

    def start(
        self,
        *,
        owner_user_id: UUID,
        bulk_operation_id: UUID,
        operation_id: UUID,
    ) -> BulkStartResult:
        """Bind a second explicit action and enqueue each currently selectable candidate."""

        self._require_provider()
        operation = self._session.scalar(
            select(BulkOperationRow)
            .where(
                BulkOperationRow.bulk_operation_id == bulk_operation_id,
                BulkOperationRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if operation is None:
            raise BulkDiscoveryError("discovery_target_not_found")
        candidate_ids = tuple(
            self._session.scalars(
                select(BulkOperationItemRow.candidate_id)
                .where(BulkOperationItemRow.bulk_operation_id == bulk_operation_id)
                .order_by(BulkOperationItemRow.ordinal)
            ).all()
        )
        if len(candidate_ids) != operation.planned_candidate_count:
            raise BulkDiscoveryError("discovery_bulk_preview_invalid")
        start_hash = _request_hash(
            {
                "action": "bulk_start",
                "bulk_operation_id": str(bulk_operation_id),
                "operation_id": str(operation_id),
                "schema_version": 1,
            }
        )
        if operation.start_operation_id is not None:
            if operation.start_operation_id != operation_id or not hmac.compare_digest(
                operation.start_request_sha256 or b"", start_hash
            ):
                raise BulkDiscoveryError("operation_conflict")
            return _start_result(operation, replayed=True)
        operation.start_operation_id = operation_id
        operation.start_request_sha256 = start_hash
        operation.started_at = datetime.now(UTC)

        queued = ready = failed = 0
        for candidate_id in candidate_ids:
            candidate = self._session.scalar(
                select(DiscoveryCandidateRow)
                .where(
                    DiscoveryCandidateRow.candidate_id == candidate_id,
                    DiscoveryCandidateRow.user_id == owner_user_id,
                )
                .with_for_update()
            )
            if candidate is None:
                raise BulkDiscoveryError("discovery_target_not_found")
            if candidate.acquisition_state == "READY":
                ready += 1
                continue
            if candidate.disposition not in {"SELECTABLE", "SELECTED"}:
                failed += 1
                continue
            if candidate.acquisition_state in {
                "QUEUED",
                "ACQUIRING",
                "INGESTING",
                "MATERIALIZING",
                "RETRY_WAIT",
            }:
                queued += 1
                continue
            if candidate.acquisition_state != "NOT_REQUESTED":
                failed += 1
                continue
            job = PostgresJobRepository(self._session).enqueue(
                EnqueueJob(
                    key=DISCOVERY_ACQUIRE_JOB,
                    user_id=owner_user_id,
                    priority=4,
                    payload={"candidate_id": str(candidate.candidate_id)},
                    idempotency_scope=f"discovery.acquire:{owner_user_id}",
                    idempotency_key=(
                        f"{candidate.candidate_id}:{candidate.source_authorization_revision}"
                    ),
                )
            )
            candidate.job_id = job.job_id
            candidate.disposition = "SELECTED"
            candidate.acquisition_state = "QUEUED"
            candidate.updated_at = datetime.now(UTC)
            candidate.row_version += 1
            queued += 1
        operation.queued_count = queued
        operation.ready_count = ready
        operation.failed_count = failed
        operation.state = (
            "QUEUED"
            if queued
            else "COMPLETED"
            if failed == 0
            else "FAILED_TERMINAL"
            if ready == 0
            else "PARTIAL"
        )
        if queued == 0:
            operation.completed_at = datetime.now(UTC)
        operation.updated_at = datetime.now(UTC)
        operation.row_version += 1
        self._session.flush()
        return _start_result(operation, replayed=False)

    def start_search_acquisition(
        self,
        *,
        owner_user_id: UUID,
        operation_id: UUID,
        evidence: DiscoveryCandidate,
    ) -> BulkStartResult:
        """Persist one explicit search result and enqueue Vault-first acquisition."""

        self._require_provider()
        if not evidence.acquisition_allowed:
            raise BulkDiscoveryError("discovery_not_eligible")
        request_sha256 = _request_hash(
            {
                "action": "search_acquire",
                "album": evidence.album,
                "artist": evidence.artist,
                "duration_seconds": evidence.duration_seconds,
                "license_url": evidence.license_url,
                "operation_id": str(operation_id),
                "provider_artist_id": evidence.provider_artist_id,
                "provider_track_id": evidence.provider_track_id,
                "schema_version": 1,
                "share_url": evidence.share_url,
                "title": evidence.title,
            }
        )
        bulk_id = uuid5(
            NAMESPACE_URL,
            f"autplay:a1b-search-acquire-v1:{owner_user_id}:{operation_id}",
        )
        inserted_id = self._session.scalar(
            insert(BulkOperationRow)
            .values(
                bulk_operation_id=bulk_id,
                user_id=owner_user_id,
                import_job_id=None,
                operation_id=operation_id,
                request_sha256=request_sha256,
                start_operation_id=operation_id,
                start_request_sha256=request_sha256,
                state="QUEUED",
                selected_artist_count=1,
                planned_candidate_count=1,
                queued_count=1,
                started_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=[BulkOperationRow.user_id, BulkOperationRow.operation_id]
            )
            .returning(BulkOperationRow.bulk_operation_id)
        )
        operation = self._session.scalar(
            select(BulkOperationRow)
            .where(
                BulkOperationRow.user_id == owner_user_id,
                BulkOperationRow.operation_id == operation_id,
            )
            .with_for_update()
        )
        if (
            operation is None
            or operation.import_job_id is not None
            or operation.start_operation_id != operation_id
            or not hmac.compare_digest(operation.request_sha256, request_sha256)
            or not hmac.compare_digest(operation.start_request_sha256 or b"", request_sha256)
        ):
            raise BulkDiscoveryError("operation_conflict")
        if inserted_id is None:
            return _start_result(operation, replayed=True)

        candidate_id = uuid5(
            NAMESPACE_URL,
            f"autplay:a1b-candidate-v1:{owner_user_id}:{JAMENDO_PROVIDER_ID}:GLOBAL:{evidence.provider_track_id}",
        )
        self._session.execute(
            insert(DiscoveryCandidateRow)
            .values(
                candidate_id=candidate_id,
                user_id=owner_user_id,
                provider_id=JAMENDO_PROVIDER_ID,
                market_scope="GLOBAL",
                provider_track_id=evidence.provider_track_id,
                provider_artist_id=evidence.provider_artist_id,
                title=evidence.title,
                artist=evidence.artist,
                album=evidence.album,
                duration_seconds=evidence.duration_seconds,
                license_url=evidence.license_url,
                share_url=evidence.share_url,
                disposition="SELECTABLE",
                acquisition_state="NOT_REQUESTED",
                source_authorization_revision=1,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    DiscoveryCandidateRow.user_id,
                    DiscoveryCandidateRow.provider_id,
                    DiscoveryCandidateRow.market_scope,
                    DiscoveryCandidateRow.provider_track_id,
                ]
            )
        )
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.user_id == owner_user_id,
                DiscoveryCandidateRow.provider_id == JAMENDO_PROVIDER_ID,
                DiscoveryCandidateRow.market_scope == "GLOBAL",
                DiscoveryCandidateRow.provider_track_id == evidence.provider_track_id,
            )
            .with_for_update()
        )
        if candidate is None or candidate.provider_artist_id != evidence.provider_artist_id:
            raise BulkDiscoveryError("discovery_provider_response_invalid")
        if candidate.acquisition_state == "NOT_REQUESTED":
            candidate.title = evidence.title
            candidate.artist = evidence.artist
            candidate.album = evidence.album
            candidate.duration_seconds = evidence.duration_seconds
            candidate.license_url = evidence.license_url
            candidate.share_url = evidence.share_url
            candidate.disposition = "SELECTABLE"
            candidate.error_code = None
            candidate.updated_at = datetime.now(UTC)
            candidate.row_version += 1
        self._session.add(
            BulkOperationItemRow(
                bulk_operation_id=operation.bulk_operation_id,
                candidate_id=candidate.candidate_id,
                ordinal=0,
            )
        )
        if candidate.acquisition_state == "READY":
            operation.queued_count = 0
            operation.ready_count = 1
            operation.state = "COMPLETED"
            operation.completed_at = datetime.now(UTC)
        elif candidate.acquisition_state in {
            "QUEUED",
            "ACQUIRING",
            "INGESTING",
            "MATERIALIZING",
            "RETRY_WAIT",
        }:
            operation.state = "QUEUED"
        elif candidate.acquisition_state == "NOT_REQUESTED" and candidate.disposition in {
            "SELECTABLE",
            "SELECTED",
        }:
            job = PostgresJobRepository(self._session).enqueue(
                EnqueueJob(
                    key=DISCOVERY_ACQUIRE_JOB,
                    user_id=owner_user_id,
                    priority=4,
                    payload={"candidate_id": str(candidate.candidate_id)},
                    idempotency_scope=f"discovery.acquire:{owner_user_id}",
                    idempotency_key=(
                        f"{candidate.candidate_id}:{candidate.source_authorization_revision}"
                    ),
                )
            )
            candidate.job_id = job.job_id
            candidate.disposition = "SELECTED"
            candidate.acquisition_state = "QUEUED"
            candidate.updated_at = datetime.now(UTC)
            candidate.row_version += 1
        else:
            raise BulkDiscoveryError("candidate_not_selectable")
        operation.updated_at = datetime.now(UTC)
        operation.row_version += 1
        self._session.flush()
        return _start_result(operation, replayed=False)

    def status(self, *, owner_user_id: UUID, bulk_operation_id: UUID) -> BulkStartResult:
        """Return the current owner-scoped durable operation snapshot."""

        operation = self._session.scalar(
            select(BulkOperationRow).where(
                BulkOperationRow.bulk_operation_id == bulk_operation_id,
                BulkOperationRow.user_id == owner_user_id,
            )
        )
        if operation is None:
            raise BulkDiscoveryError("discovery_target_not_found")
        return _start_result(operation, replayed=False)

    def cleanup_expired(
        self,
        *,
        now: datetime,
        limit: int = 10_000,
        retention: timedelta = timedelta(days=30),
    ) -> int:
        """Delete a bounded batch of expired raw/terminal discovery evidence."""

        if not 1 <= limit <= 10_000:
            raise ValueError("discovery cleanup limit must be within 1..10000")
        if retention <= timedelta(0):
            raise ValueError("discovery retention must be positive")
        cutoff = now - retention
        operation_ids = tuple(
            self._session.scalars(
                select(BulkOperationRow.bulk_operation_id)
                .where(
                    BulkOperationRow.updated_at <= cutoff,
                    BulkOperationRow.state.in_(
                        {
                            "PREVIEW",
                            "COMPLETED",
                            "PARTIAL",
                            "FAILED_TERMINAL",
                            "CANCELLED",
                        }
                    ),
                )
                .order_by(BulkOperationRow.updated_at, BulkOperationRow.bulk_operation_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if operation_ids:
            self._session.execute(
                delete(BulkOperationRow).where(
                    BulkOperationRow.bulk_operation_id.in_(operation_ids)
                )
            )
        remaining = limit - len(operation_ids)
        if remaining <= 0:
            return len(operation_ids)
        candidate_ids = tuple(
            self._session.scalars(
                select(DiscoveryCandidateRow.candidate_id)
                .where(
                    DiscoveryCandidateRow.updated_at <= cutoff,
                    DiscoveryCandidateRow.acquisition_state.in_(
                        {"NOT_REQUESTED", "FAILED_TERMINAL", "CANCELLED"}
                    ),
                    ~select(BulkOperationItemRow.candidate_id)
                    .where(BulkOperationItemRow.candidate_id == DiscoveryCandidateRow.candidate_id)
                    .exists(),
                    ~select(UploadSessionRow.source_candidate_id)
                    .where(
                        UploadSessionRow.source_candidate_id == DiscoveryCandidateRow.candidate_id
                    )
                    .exists(),
                )
                .order_by(DiscoveryCandidateRow.updated_at, DiscoveryCandidateRow.candidate_id)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if candidate_ids:
            self._session.execute(
                delete(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.candidate_id.in_(candidate_ids)
                )
            )
        return len(operation_ids) + len(candidate_ids)

    def claim_acquisition(
        self, *, candidate_id: UUID, owner_user_id: UUID, fence: LeaseFence
    ) -> AcquisitionTarget | None:
        """Fence and enter one candidate's external-I/O state."""

        self._require_fence(
            fence, owner_user_id, expected_key=DISCOVERY_ACQUIRE_JOB, candidate_id=candidate_id
        )
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if candidate is None or candidate.job_id != fence.job_id:
            raise BulkDiscoveryError("discovery_target_not_found")
        if candidate.acquisition_state == "READY":
            return None
        if candidate.acquisition_state not in {"QUEUED", "ACQUIRING", "RETRY_WAIT"}:
            raise BulkDiscoveryError("candidate_not_selectable")
        candidate.acquisition_state = "ACQUIRING"
        candidate.updated_at = datetime.now(UTC)
        candidate.row_version += 1
        refresh_bulk_operations(self._session, candidate.candidate_id, candidate.updated_at)
        self._session.flush()
        return AcquisitionTarget(
            candidate.candidate_id,
            candidate.user_id,
            candidate.provider_track_id,
        )

    def prepare_ingest(
        self,
        *,
        candidate_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        evidence: DiscoveryCandidate,
        staging_key: OpaqueStorageKey,
        verified: VerifiedStagedFile,
        limits: VaultLimits,
    ) -> AcquisitionPrepared:
        """Recheck evidence, explicitly create identity, and enqueue canonical Vault ingest."""

        self._require_fence(
            fence, owner_user_id, expected_key=DISCOVERY_ACQUIRE_JOB, candidate_id=candidate_id
        )
        self._require_provider()
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if (
            candidate is None
            or candidate.job_id != fence.job_id
            or candidate.acquisition_state not in {"ACQUIRING", "INGESTING"}
            or evidence.provider_track_id != candidate.provider_track_id
            or evidence.provider_artist_id != candidate.provider_artist_id
            or not evidence.acquisition_allowed
        ):
            raise BulkDiscoveryError("discovery_not_eligible")
        existing_upload = self._session.scalar(
            select(UploadSessionRow).where(
                UploadSessionRow.source_candidate_id == candidate.candidate_id
            )
        )
        if existing_upload is not None:
            if existing_upload.job_id is None:
                raise BulkDiscoveryError("discovery_ingest_state_invalid")
            return AcquisitionPrepared(
                existing_upload.upload_session_id,
                existing_upload.job_id,
                existing_upload.target_recording_id,
            )
        recording, user_ref, library_entry, external_reference = self._materialize_identity(
            candidate, evidence
        )
        upload_id = uuid5(
            NAMESPACE_URL,
            f"autplay:a1b-provider-upload-v1:{candidate.candidate_id}:{candidate.source_authorization_revision}",
        )
        ingest_job = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=JobKey("vault.ingest", 1),
                user_id=owner_user_id,
                priority=3,
                payload={"upload_session_id": str(upload_id)},
                idempotency_scope="vault.ingest",
                idempotency_key=str(upload_id),
            )
        )
        now = datetime.now(UTC)
        request_hash = hashlib.sha256(
            b"\0".join(
                (
                    str(recording.recording_id).encode("ascii"),
                    str(verified.byte_size).encode("ascii"),
                    verified.sha256.value,
                )
            )
        ).digest()
        self._session.add(
            UploadSessionRow(
                upload_session_id=upload_id,
                user_id=owner_user_id,
                device_id=None,
                actor_kind="PROVIDER",
                source_candidate_id=candidate.candidate_id,
                target_recording_id=recording.recording_id,
                idempotency_key=f"discovery:{candidate.candidate_id}",
                request_hash=request_hash,
                declared_sha256=verified.sha256.value,
                expected_size=verified.byte_size,
                received_size=verified.byte_size,
                chunk_size=limits.max_chunk_bytes,
                max_chunks=limits.max_chunks,
                chunk_count=math.ceil(verified.byte_size / limits.max_chunk_bytes),
                staging_key=staging_key.value,
                state="SEALED",
                job_id=ingest_job.job_id,
                expires_at=now + timedelta(hours=24),
                sealed_at=now,
            )
        )
        candidate.title = evidence.title
        candidate.artist = evidence.artist
        candidate.album = evidence.album
        candidate.duration_seconds = evidence.duration_seconds
        candidate.license_url = evidence.license_url
        candidate.share_url = evidence.share_url
        candidate.external_reference_id = external_reference.external_reference_id
        candidate.recording_id = recording.recording_id
        candidate.user_track_ref_id = user_ref.user_track_ref_id
        candidate.library_entry_id = library_entry.library_entry_id
        candidate.staging_key = staging_key.value
        candidate.acquisition_state = "INGESTING"
        candidate.updated_at = now
        candidate.row_version += 1
        refresh_bulk_operations(self._session, candidate.candidate_id, now)
        self._session.flush()
        return AcquisitionPrepared(upload_id, ingest_job.job_id, recording.recording_id)

    def fail_acquisition(
        self,
        *,
        candidate_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        error_code: str,
        terminal: bool,
    ) -> None:
        """Persist only a bounded error code while retaining retryable job authority."""

        self._require_fence(
            fence, owner_user_id, expected_key=DISCOVERY_ACQUIRE_JOB, candidate_id=candidate_id
        )
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if candidate is None or candidate.job_id != fence.job_id:
            raise BulkDiscoveryError("discovery_target_not_found")
        candidate.acquisition_state = "FAILED_TERMINAL" if terminal else "RETRY_WAIT"
        candidate.error_code = error_code[:100]
        candidate.updated_at = datetime.now(UTC)
        candidate.row_version += 1
        refresh_bulk_operations(self._session, candidate.candidate_id, candidate.updated_at)
        self._session.flush()

    def claim_analysis(self, *, candidate_id: UUID, owner_user_id: UUID, fence: LeaseFence) -> bool:
        """Fence the standard CPU analysis fact for one already-ready candidate."""

        self._require_fence(
            fence, owner_user_id, expected_key=STANDARD_ANALYSIS_JOB, candidate_id=candidate_id
        )
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if candidate is None or candidate.acquisition_state != "READY":
            raise BulkDiscoveryError("discovery_target_not_found")
        if candidate.analysis_state == "COMPLETE":
            return False
        if candidate.analysis_state not in {"QUEUED", "RUNNING", "FAILED_RETRYABLE"}:
            raise BulkDiscoveryError("discovery_analysis_state_invalid")
        candidate.analysis_state = "RUNNING"
        candidate.updated_at = datetime.now(UTC)
        candidate.row_version += 1
        self._session.flush()
        return True

    def complete_analysis(
        self, *, candidate_id: UUID, owner_user_id: UUID, fence: LeaseFence
    ) -> bool:
        """Record that canonical ingest produced validated technical/fingerprint evidence."""

        self._require_fence(
            fence, owner_user_id, expected_key=STANDARD_ANALYSIS_JOB, candidate_id=candidate_id
        )
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if candidate is None or candidate.analysis_state != "RUNNING":
            raise BulkDiscoveryError("discovery_analysis_state_invalid")
        evidence_exists = self._session.scalar(
            select(AudioVariantRow.audio_variant_id)
            .join(
                VaultObjectRow,
                VaultObjectRow.vault_object_id == AudioVariantRow.vault_object_id,
            )
            .join(
                VaultReplicaRow,
                VaultReplicaRow.vault_object_id == VaultObjectRow.vault_object_id,
            )
            .join(
                AudioFingerprintRow,
                AudioFingerprintRow.audio_variant_id == AudioVariantRow.audio_variant_id,
            )
            .where(
                AudioVariantRow.audio_variant_id == candidate.audio_variant_id,
                AudioVariantRow.recording_id == candidate.recording_id,
                AudioVariantRow.validation_status == "VALID",
                AudioVariantRow.deleted_at.is_(None),
                VaultObjectRow.commit_status == "COMMITTED",
                VaultReplicaRow.replica_status == "AVAILABLE",
                VaultReplicaRow.verified_at.is_not(None),
            )
            .limit(1)
        )
        if evidence_exists is None:
            candidate.analysis_state = "FAILED_TERMINAL"
            candidate.error_code = "discovery_analysis_evidence_invalid"
            candidate.updated_at = datetime.now(UTC)
            candidate.row_version += 1
            self._session.flush()
            return False
        candidate.analysis_state = "COMPLETE"
        candidate.updated_at = datetime.now(UTC)
        candidate.row_version += 1
        self._session.flush()
        return True

    def _materialize_identity(
        self, candidate: DiscoveryCandidateRow, evidence: DiscoveryCandidate
    ) -> tuple[RecordingRow, UserTrackRefRow, LibraryEntryRow, ExternalReferenceRow]:
        track_reference = self._session.scalar(
            select(ExternalReferenceRow)
            .where(
                ExternalReferenceRow.provider_id == JAMENDO_PROVIDER_ID,
                ExternalReferenceRow.external_entity_type == "TRACK",
                ExternalReferenceRow.external_id == evidence.provider_track_id,
                ExternalReferenceRow.market_scope == "GLOBAL",
            )
            .with_for_update()
        )
        if track_reference is not None and track_reference.recording_id is not None:
            user_ref = self._session.scalar(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.user_id == candidate.user_id,
                    UserTrackRefRow.recording_id == track_reference.recording_id,
                    UserTrackRefRow.resolution_status == "RESOLVED",
                    UserTrackRefRow.deleted_at.is_(None),
                )
            )
            if user_ref is None:
                raise BulkDiscoveryError("identity_review_required")
            recording = self._session.get(RecordingRow, track_reference.recording_id)
            if recording is None or recording.deleted_at is not None:
                raise BulkDiscoveryError("identity_review_required")
            library_entry = self._active_or_new_library_entry(candidate.user_id, user_ref)
            return recording, user_ref, library_entry, track_reference

        artist_reference = self._session.scalar(
            select(ExternalReferenceRow)
            .where(
                ExternalReferenceRow.provider_id == JAMENDO_PROVIDER_ID,
                ExternalReferenceRow.external_entity_type == "ARTIST",
                ExternalReferenceRow.external_id == evidence.provider_artist_id,
                ExternalReferenceRow.market_scope == "GLOBAL",
            )
            .with_for_update()
        )
        if artist_reference is not None and artist_reference.artist_id is None:
            raise BulkDiscoveryError("identity_review_required")
        artist = (
            self._session.get(ArtistRow, artist_reference.artist_id)
            if artist_reference is not None and artist_reference.artist_id is not None
            else None
        )
        if artist is None:
            artist = ArtistRow(
                name=evidence.artist,
                sort_name=evidence.artist,
                normalized_name=normalize_text(evidence.artist),
                artist_type="UNKNOWN",
                identity_status="PROVISIONAL",
            )
            self._session.add(artist)
            self._session.flush([artist])
            artist_reference = ExternalReferenceRow(
                provider_id=JAMENDO_PROVIDER_ID,
                external_entity_type="ARTIST",
                external_id=evidence.provider_artist_id,
                market_scope="GLOBAL",
                artist_id=artist.artist_id,
            )
            self._session.add(artist_reference)
        credit = ArtistCreditRow(
            display_name=evidence.artist,
            normalized_name=normalize_text(evidence.artist),
        )
        self._session.add(credit)
        self._session.flush([credit])
        self._session.add(
            ArtistCreditNameRow(
                artist_credit_id=credit.artist_credit_id,
                position=0,
                artist_id=artist.artist_id,
                credited_name=evidence.artist,
                join_phrase="",
                role="PRIMARY",
            )
        )
        if track_reference is None:
            track_reference = ExternalReferenceRow(
                provider_id=JAMENDO_PROVIDER_ID,
                external_entity_type="TRACK",
                external_id=evidence.provider_track_id,
                market_scope="GLOBAL",
            )
            self._session.add(track_reference)
        user_ref = UserTrackRefRow(
            user_id=candidate.user_id,
            resolution_status="UNRESOLVED",
            raw_title=evidence.title,
            raw_artist=evidence.artist,
            raw_album=evidence.album,
            raw_duration_ms=evidence.duration_seconds * 1000,
        )
        self._session.add(user_ref)
        self._session.flush()
        PostgresImportRepository(self._session).ensure_matcher_release()
        predecessor = _discovery_predecessor(candidate, evidence, user_ref)
        self._session.add(predecessor)
        self._session.flush([predecessor])
        library_holder: list[LibraryEntryRow] = []

        def project_owner(
            active: Session,
            decision: MatchDecisionRow,
            recording: RecordingRow,
        ) -> None:
            user_ref.recording_id = recording.recording_id
            user_ref.resolution_status = "RESOLVED"
            user_ref.current_match_decision_id = decision.decision_id
            user_ref.resolved_at = decision.decided_at
            user_ref.resolution_confidence = decision.confidence
            track_reference.recording_id = recording.recording_id
            active.add(
                UserTrackRefExternalReferenceRow(
                    user_track_ref_id=user_ref.user_track_ref_id,
                    external_reference_id=track_reference.external_reference_id,
                    relation_role="PRIMARY_SOURCE",
                )
            )
            library_entry = LibraryEntryRow(
                user_id=candidate.user_id,
                user_track_ref_id=user_ref.user_track_ref_id,
                source="SEARCH",
                availability_status="PENDING",
            )
            active.add(library_entry)
            active.flush([library_entry])
            library_holder.append(library_entry)

        recording = RecordingRow(
            artist_credit_id=credit.artist_credit_id,
            title=evidence.title,
            normalized_title=normalize_text(evidence.title),
            duration_ms=evidence.duration_seconds * 1000,
            recording_kind="UNKNOWN",
            identity_status="PROVISIONAL",
            metadata_confidence=Decimal("1.0000"),
        )
        review = _create_recording_review(candidate, predecessor)
        self._session.flush()
        execute_create_recording_review(
            self._session,
            CreateRecordingReviewCommand(recording, review, (), project_owner),
        )
        if not library_holder:
            raise BulkDiscoveryError("discovery_materialization_failed")
        return recording, user_ref, library_holder[0], track_reference

    def _active_or_new_library_entry(
        self, owner_user_id: UUID, user_ref: UserTrackRefRow
    ) -> LibraryEntryRow:
        existing = self._session.scalar(
            select(LibraryEntryRow).where(
                LibraryEntryRow.user_id == owner_user_id,
                LibraryEntryRow.user_track_ref_id == user_ref.user_track_ref_id,
                LibraryEntryRow.removed_at.is_(None),
            )
        )
        if existing is not None:
            return existing
        row = LibraryEntryRow(
            user_id=owner_user_id,
            user_track_ref_id=user_ref.user_track_ref_id,
            source="SEARCH",
            availability_status="PENDING",
        )
        self._session.add(row)
        self._session.flush([row])
        return row

    def _require_fence(
        self,
        fence: LeaseFence,
        owner_user_id: UUID,
        *,
        expected_key: JobKey,
        candidate_id: UUID,
    ) -> None:
        row = self._session.scalar(
            select(JobRow).where(JobRow.job_id == fence.job_id).with_for_update()
        )
        if (
            row is None
            or row.user_id != owner_user_id
            or row.state != "RUNNING"
            or row.lease_owner != fence.worker_id
            or row.attempt_count != fence.attempt_no
            or row.job_type != expected_key.job_type
            or row.schema_version != expected_key.schema_version
            or row.payload != {"candidate_id": str(candidate_id)}
        ):
            raise BulkDiscoveryError("lease_fence_lost")

    def _require_import(self, owner_user_id: UUID, import_job_id: UUID) -> None:
        row = self._session.scalar(
            select(ImportJobRow).where(
                ImportJobRow.import_job_id == import_job_id,
                ImportJobRow.user_id == owner_user_id,
            )
        )
        summary = row.summary if row is not None and isinstance(row.summary, dict) else {}
        if row is None or summary.get("format") != "TXT":
            raise BulkDiscoveryError("discovery_target_not_found")

    def _require_provider(self) -> None:
        provider = self._session.get(SourceProviderRow, JAMENDO_PROVIDER_ID)
        if (
            provider is None
            or not provider.enabled
            or provider.deleted_at is not None
            or provider.adapter_id != "autplay.jamendo.manual"
            or provider.adapter_version != "1.0.0"
            or not {"SEARCH", "DOWNLOAD"}.issubset(set(provider.capabilities))
        ):
            raise BulkDiscoveryError("source_authorization_unavailable")


def _discovery_predecessor(
    candidate: DiscoveryCandidateRow,
    evidence: DiscoveryCandidate,
    user_ref: UserTrackRefRow,
) -> MatchDecisionRow:
    snapshot_values: dict[str, JsonValue] = {
        "normalized_title": normalize_text(evidence.title),
        "normalized_artists": [normalize_text(evidence.artist)],
        "duration_ms": evidence.duration_seconds * 1000,
        "version_markers": [],
        "market_scope": "GLOBAL",
        "evidence_ids": [
            f"jamendo-track:{evidence.provider_track_id}",
            f"jamendo-artist:{evidence.provider_artist_id}",
        ],
    }
    if evidence.album:
        snapshot_values["normalized_release"] = normalize_text(evidence.album)
    snapshot = canonical_query_snapshot(snapshot_values)
    aggregate_hash, _ = candidate_aggregate_sha256([])
    request_hash = hashlib.sha256(
        snapshot.canonical_bytes + b"\0" + aggregate_hash + b"\0SHADOW"
    ).digest()
    return MatchDecisionRow(
        query_type="USER_TRACK_REF",
        owner_user_id=candidate.user_id,
        device_id=None,
        import_entry_id=None,
        user_track_ref_id=user_ref.user_track_ref_id,
        local_audio_id=None,
        external_reference_id=None,
        vault_object_id=None,
        audio_variant_id=None,
        query_snapshot=snapshot.value,
        query_snapshot_schema_version="1",
        snapshot_canonicalization_version="RFC8785",
        query_snapshot_sha256=snapshot.sha256,
        decision_kind="EVALUATION",
        execution_mode="SHADOW",
        review_action=None,
        reviewed_candidate_evidence_id=None,
        candidate_recording_id=None,
        decision_state="DEFERRED_EVIDENCE",
        candidate_count=0,
        candidate_evidence_sha256=aggregate_hash,
        candidate_evidence_size_bytes=0,
        evidence_mode="METADATA_ONLY",
        candidate_generation_version=CANDIDATE_GENERATION_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        feature_extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
        matcher_version=MATCHER_VERSION,
        calibrator_version=None,
        threshold_set_version=None,
        raw_score=None,
        confidence=None,
        top2_confidence=None,
        margin=None,
        evidence_tier=None,
        feature_scores=[],
        hard_conflicts=[],
        candidate_origins=[
            {
                "provider": "Jamendo",
                "provider_track_id": evidence.provider_track_id,
                "provider_artist_id": evidence.provider_artist_id,
            }
        ],
        explanation_schema_version="1",
        actor_type="SYSTEM",
        actor_user_id=None,
        idempotency_scope="a1b-discovery-evaluation",
        idempotency_key=str(candidate.candidate_id),
        request_sha256=request_hash,
        supersedes_decision_id=None,
        supersession_reason=None,
        decided_at=datetime.now(UTC),
    )


def _create_recording_review(
    candidate: DiscoveryCandidateRow, predecessor: MatchDecisionRow
) -> MatchDecisionRow:
    request_hash = hashlib.sha256(
        predecessor.request_sha256 + b"\0CREATE_RECORDING\0" + str(candidate.user_id).encode()
    ).digest()
    return MatchDecisionRow(
        query_type=predecessor.query_type,
        owner_user_id=predecessor.owner_user_id,
        device_id=predecessor.device_id,
        import_entry_id=predecessor.import_entry_id,
        user_track_ref_id=predecessor.user_track_ref_id,
        local_audio_id=predecessor.local_audio_id,
        external_reference_id=predecessor.external_reference_id,
        vault_object_id=predecessor.vault_object_id,
        audio_variant_id=predecessor.audio_variant_id,
        query_snapshot=deepcopy(predecessor.query_snapshot),
        query_snapshot_schema_version=predecessor.query_snapshot_schema_version,
        snapshot_canonicalization_version=predecessor.snapshot_canonicalization_version,
        query_snapshot_sha256=predecessor.query_snapshot_sha256,
        decision_kind="REVIEW_ACTION",
        execution_mode="APPLIED",
        review_action="CREATE_RECORDING",
        reviewed_candidate_evidence_id=None,
        candidate_recording_id=None,
        decision_state=predecessor.decision_state,
        candidate_count=0,
        candidate_evidence_sha256=predecessor.candidate_evidence_sha256,
        candidate_evidence_size_bytes=0,
        evidence_mode=predecessor.evidence_mode,
        candidate_generation_version=predecessor.candidate_generation_version,
        normalization_version=predecessor.normalization_version,
        feature_extractor_versions=deepcopy(predecessor.feature_extractor_versions),
        matcher_version=predecessor.matcher_version,
        calibrator_version=predecessor.calibrator_version,
        threshold_set_version=predecessor.threshold_set_version,
        raw_score=predecessor.raw_score,
        confidence=predecessor.confidence,
        top2_confidence=predecessor.top2_confidence,
        margin=predecessor.margin,
        evidence_tier=predecessor.evidence_tier,
        feature_scores=deepcopy(predecessor.feature_scores),
        hard_conflicts=deepcopy(predecessor.hard_conflicts),
        candidate_origins=deepcopy(predecessor.candidate_origins),
        explanation_schema_version=predecessor.explanation_schema_version,
        actor_type="USER",
        actor_user_id=candidate.user_id,
        idempotency_scope=f"a1b-discovery-review:{candidate.user_id}",
        idempotency_key=str(candidate.candidate_id),
        request_sha256=request_hash,
        supersedes_decision_id=predecessor.decision_id,
        supersession_reason="Explicit A1B manual provider-track selection",
        decided_at=predecessor.decided_at + timedelta(microseconds=1),
    )


def _request_hash(value: dict[str, JsonValue]) -> bytes:
    return hashlib.sha256(rfc8785.dumps(value)).digest()


def refresh_bulk_operations(session: Session, candidate_id: UUID, now: datetime) -> None:
    """Recompute every started operation containing one changed candidate."""

    operation_ids = tuple(
        session.scalars(
            select(BulkOperationItemRow.bulk_operation_id).where(
                BulkOperationItemRow.candidate_id == candidate_id
            )
        )
    )
    active_states = {"QUEUED", "ACQUIRING", "INGESTING", "MATERIALIZING", "RETRY_WAIT"}
    failed_states = {"FAILED_TERMINAL", "CANCELLED"}
    eligible_dispositions = {"SELECTABLE", "SELECTED", "ALREADY_IN_LIBRARY"}
    for operation_id in operation_ids:
        operation = session.scalar(
            select(BulkOperationRow)
            .where(BulkOperationRow.bulk_operation_id == operation_id)
            .with_for_update()
        )
        if operation is None or operation.started_at is None:
            continue
        states = tuple(
            session.execute(
                select(
                    DiscoveryCandidateRow.acquisition_state,
                    DiscoveryCandidateRow.disposition,
                )
                .join(
                    BulkOperationItemRow,
                    BulkOperationItemRow.candidate_id == DiscoveryCandidateRow.candidate_id,
                )
                .where(BulkOperationItemRow.bulk_operation_id == operation_id)
            ).all()
        )
        ready = sum(state == "READY" for state, _ in states)
        failed = sum(
            state in failed_states
            or (state == "NOT_REQUESTED" and disposition not in eligible_dispositions)
            for state, disposition in states
        )
        queued = sum(state in active_states for state, _ in states)
        operation.ready_count = ready
        operation.failed_count = failed
        operation.queued_count = queued
        if ready + failed == operation.planned_candidate_count:
            operation.state = (
                "COMPLETED" if failed == 0 else "FAILED_TERMINAL" if ready == 0 else "PARTIAL"
            )
            operation.completed_at = now
        else:
            operation.state = "RUNNING"
            operation.completed_at = None
        operation.updated_at = now
        operation.row_version += 1


def _start_result(operation: BulkOperationRow, *, replayed: bool) -> BulkStartResult:
    return BulkStartResult(
        operation.bulk_operation_id,
        operation.state,
        operation.queued_count,
        operation.ready_count,
        operation.failed_count,
        replayed,
    )


__all__ = (
    "DISCOVERY_ACQUIRE_JOB",
    "JAMENDO_PROVIDER_ID",
    "STANDARD_ANALYSIS_JOB",
    "BulkDiscoveryError",
    "BulkPreviewResult",
    "BulkStartResult",
    "PostgresBulkDiscoveryRepository",
    "refresh_bulk_operations",
)
