"""Immutable top-five Internet lookup and durable explicit candidate acquisition."""

from __future__ import annotations

import hashlib
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import rfc8785
from sqlalchemy import String, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.internet_music import InternetMusicProvider, InternetMusicProviderError
from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.library_runtime import LibraryRepository
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    JobRow,
    LibraryEntryRow,
    SyncEventRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.internet_music import (
    InternetAcquisitionRow,
    InternetSearchRow,
)
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.adapters.postgresql.resource_authority import ResourceAuthorityGate
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_track_match import (
    attach_published_match,
    owned_match,
    same_track,
)
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost, JobResourceWait
from autplay.application.music_library import MusicError, MusicLibraryService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.vault_uploads import VaultNotFoundError, VaultPrincipal
from autplay.domain.acquisition_policy import MAX_TRACK_SECONDS
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.jobs import (
    JobCancellationRequested,
    JobKey,
    JobLease,
    RetryableJobError,
    TerminalJobError,
)
from autplay.domain.music_search import SEARCH_POLICY
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.ports.jobs import EnqueueJob

INTERNET_ACQUIRE_JOB = JobKey("music.internet.acquire", 1)
_PENDING_JOBS = ("QUEUED", "RUNNING", "RETRY_WAIT", "PAUSED")


class InternetMusicService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        vault: Any,
        provider: InternetMusicProvider | None = None,
    ) -> None:
        self.sessions = sessions
        self.vault = vault
        self.library = MusicLibraryService(sessions, vault)
        self.provider = provider or InternetMusicProvider()

    def search(
        self,
        principal: Principal,
        query: str,
        operation_id: UUID,
        catalogue_context_id: UUID | None = None,
    ) -> dict[str, Any]:
        query = " ".join(query.split())
        if not 1 <= len(query) <= 200:
            raise MusicError("music_query_invalid", "Enter a music query.", 422)
        with self.sessions() as session:
            row = session.get(InternetSearchRow, operation_id)
            if row is not None:
                if row.user_id != principal.user_id or row.query != query:
                    raise MusicError("music_operation_conflict", "The operation conflicts.", 409)
                PostgresCatalogueContextRepository(session).replay(
                    principal.user_id, operation_id, query, catalogue_context_id
                )
                return self._search_view(row)
            if catalogue_context_id is not None:
                PostgresCatalogueContextRepository(session).load(
                    principal.user_id, catalogue_context_id, admission=True
                )
            count = session.scalar(
                select(func.count())
                .select_from(InternetSearchRow)
                .where(
                    InternetSearchRow.user_id == principal.user_id,
                    InternetSearchRow.created_at > datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            if count is not None and count >= 15:
                raise MusicError(
                    "music_search_rate_limit",
                    "Please wait before searching again.",
                    429,
                    retryable=True,
                )
        try:
            candidates = [dict(item, policy=SEARCH_POLICY) for item in self.provider.search(query)]
        except Exception as error:
            raise MusicError(
                "music_search_unavailable",
                "Internet search is temporarily unavailable.",
                503,
                retryable=True,
            ) from error
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            existing = session.get(InternetSearchRow, operation_id)
            if existing is not None:
                if existing.user_id != principal.user_id or existing.query != query:
                    raise MusicError("music_operation_conflict", "The operation conflicts.", 409)
                PostgresCatalogueContextRepository(session).replay(
                    principal.user_id, operation_id, query, catalogue_context_id
                )
                return self._search_view(existing)
            context_repository = PostgresCatalogueContextRepository(session)
            catalogue_context = (
                context_repository.load(principal.user_id, catalogue_context_id, admission=True)
                if catalogue_context_id
                else None
            )
            row = InternetSearchRow(
                search_id=operation_id,
                user_id=principal.user_id,
                query=query,
                candidates=candidates,
                snapshot_sha256=hashlib.sha256(rfc8785.dumps(candidates)).digest(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            session.add(row)
            session.flush()
            context_repository.bind_search(
                principal.user_id, operation_id, query, catalogue_context
            )
            return self._search_view(row)

    def select(
        self,
        principal: Principal,
        search_id: UUID,
        candidate_id: str,
        *,
        recording_mbid: UUID | None = None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise MusicError(
                    "music_acquisition_unavailable", "The download is unavailable.", 503
                )
            try:
                authority = ResourceAuthorityGate(
                    session, automatic_acquisition_enabled=False
                ).authenticate(principal, now, lock_rows=False)
            except ResourceAdmissionError as error:
                raise MusicError("auth_invalid", "Authentication is unavailable.", 401) from error
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            search = session.get(InternetSearchRow, search_id)
            if search is None or search.user_id != principal.user_id:
                raise MusicError("music_search_not_found", "The search is unavailable.", 404)
            existing = session.scalar(
                select(InternetAcquisitionRow).where(
                    InternetAcquisitionRow.user_id == principal.user_id,
                    InternetAcquisitionRow.search_id == search_id,
                    InternetAcquisitionRow.candidate_id == candidate_id,
                )
            )
            if existing is not None:
                if existing.selected_snapshot.get("policy") != SEARCH_POLICY:
                    raise MusicError(
                        "music_search_expired", "Search again for a studio track.", 409
                    )
                self._restore_ready(session, principal, existing)
                return self._status_view(session, existing)
            if search.expires_at <= datetime.now(UTC):
                raise MusicError(
                    "music_search_expired", "Search again to refresh the results.", 409
                )
            candidate = next(
                (item for item in search.candidates if item["candidate_id"] == candidate_id), None
            )
            if candidate is None:
                raise MusicError(
                    "music_candidate_not_found", "Select a result from this search.", 404
                )
            if candidate.get("policy") != SEARCH_POLICY:
                raise MusicError("music_search_expired", "Search again for a studio track.", 409)
            # After handoff the provider job can be complete while Vault still owns the bytes.
            pending_ingest = exists(
                select(UploadSessionRow.upload_session_id)
                .join(JobRow, JobRow.job_id == UploadSessionRow.job_id)
                .where(
                    UploadSessionRow.source_internet_acquisition_id
                    == InternetAcquisitionRow.acquisition_id,
                    UploadSessionRow.upload_session_id == InternetAcquisitionRow.upload_id,
                    UploadSessionRow.state.in_(("SEALED", "PROCESSING", "COMMIT_PREPARED")),
                    JobRow.state.in_(_PENDING_JOBS),
                )
                .correlate(InternetAcquisitionRow)
            )
            exact_source = (InternetAcquisitionRow.candidate_id == candidate_id) & (
                InternetAcquisitionRow.selected_snapshot["provider"].astext == candidate["provider"]
            )
            previous_selections = session.scalars(
                select(InternetAcquisitionRow)
                .join(JobRow, JobRow.job_id == InternetAcquisitionRow.job_id)
                .where(
                    InternetAcquisitionRow.user_id == principal.user_id,
                    ((InternetAcquisitionRow.state == "READY") & exact_source)
                    | (
                        (InternetAcquisitionRow.selected_snapshot["policy"].astext == SEARCH_POLICY)
                        & InternetAcquisitionRow.state.not_in(("READY", "FAILED"))
                        & (
                            JobRow.state.in_(_PENDING_JOBS)
                            | ((JobRow.state == "COMPLETED") & pending_ingest)
                        )
                    ),
                )
                .order_by(exact_source.desc(), InternetAcquisitionRow.created_at.desc())
            )
            for previous in previous_selections:
                if recording_mbid is not None:
                    original = PostgresCatalogueContextRepository(session).for_acquisition(
                        principal.user_id, previous.acquisition_id
                    )
                    if (
                        original is None
                        or original.card.recording_mbid != recording_mbid
                        or previous.candidate_id != candidate_id
                        or previous.selected_snapshot.get("provider") != candidate["provider"]
                    ):
                        continue
                if (previous.candidate_id, previous.selected_snapshot.get("provider")) != (
                    candidate_id,
                    candidate["provider"],
                ) and not same_track(previous.selected_snapshot, candidate):
                    continue
                # Older completed selections can be identified by a fresh studio result
                # once the original audio is still playable and its duration agrees.
                if previous.selected_snapshot.get(
                    "policy"
                ) != SEARCH_POLICY and not self._playable_source(
                    session,
                    principal.user_id,
                    previous.user_track_ref_id,
                    previous.audio_variant_id,
                    candidate["duration_ms"],
                ):
                    continue
                status = self._status_view(session, previous)
                if status["state"] != "FAILED":
                    self._restore_ready(session, principal, previous)
                    return status
            saved = None
            if recording_mbid is None:
                saved = self._imported_source(session, principal.user_id, candidate)
                if saved is None:
                    saved = owned_match(session, principal.user_id, candidate)
            if saved is None and recording_mbid is None:
                saved = attach_published_match(
                    session, VaultPrincipal(principal.user_id, principal.device_id), candidate
                )
                if saved is not None:
                    self._ensure_vault_entry(session, principal, saved[0], create=True)
            count = session.scalar(
                select(func.count())
                .select_from(InternetAcquisitionRow)
                .join(JobRow, JobRow.job_id == InternetAcquisitionRow.job_id)
                .where(
                    InternetAcquisitionRow.user_id == principal.user_id,
                    JobRow.state.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
                )
            )
            if saved is None and count is not None and count >= 20:
                raise MusicError(
                    "music_queue_full", "Wait for current downloads to finish.", 429, retryable=True
                )
            identity = uuid4()
            job = PostgresJobRepository(session).enqueue(
                EnqueueJob(
                    key=INTERNET_ACQUIRE_JOB,
                    user_id=principal.user_id,
                    payload={"acquisition_id": str(identity)},
                    priority=2,
                    idempotency_scope=f"music-acquire:{principal.user_id}",
                    idempotency_key=f"{search_id}:{candidate_id}",
                )
            )
            if saved is not None:
                # Persist a pollable receipt. The job is already complete at commit, so
                # no worker can claim it or contact the provider.
                completed = session.get_one(JobRow, job.job_id)
                completed.state, completed.completed_at = "COMPLETED", now
            row = InternetAcquisitionRow(
                acquisition_id=identity,
                user_id=principal.user_id,
                device_id=principal.device_id,
                authority_generation=authority.authority_generation,
                source_session_family_id=authority.session_family_id,
                source_session_mode=authority.session_mode,
                search_id=search_id,
                candidate_id=candidate_id,
                selected_snapshot=candidate,
                job_id=job.job_id,
                state="READY" if saved else "QUEUED",
                user_track_ref_id=saved[0] if saved else None,
                audio_variant_id=saved[1] if saved else None,
            )
            session.add(row)
            session.flush()
            return self._view(row)

    @staticmethod
    def _imported_source(
        session: Session, owner_id: UUID, candidate: dict[str, Any]
    ) -> tuple[UUID, UUID] | None:
        source_id, provider = candidate["candidate_id"], candidate["provider"]
        evidence = TrackMetadataRow.document["acquisition_evidence"]
        imported = (
            select(UserTrackRefRow.user_track_ref_id, AudioVariantRow.audio_variant_id)
            .join(
                TrackMetadataRow,
                TrackMetadataRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .join(AudioVariantRow, AudioVariantRow.recording_id == UserTrackRefRow.recording_id)
            .where(
                UserTrackRefRow.user_id == owner_id,
                UserTrackRefRow.deleted_at.is_(None),
                evidence["provider"].astext == provider.upper(),
                evidence["source_id"].astext == source_id,
                TrackMetadataRow.document["audio_variant_id"].astext
                == AudioVariantRow.audio_variant_id.cast(String),
            )
        )
        for ref_id, variant_id in session.execute(imported):
            if InternetMusicService._playable_source(
                session, owner_id, ref_id, variant_id, candidate["duration_ms"]
            ):
                return ref_id, variant_id
        return None

    @staticmethod
    def _playable_source(
        session: Session,
        owner_id: UUID,
        ref_id: UUID | None,
        variant_id: UUID | None,
        expected_ms: int,
    ) -> bool:
        if ref_id is None or variant_id is None:
            return False
        try:
            playable = PostgresVaultRuntime(session).resolve_owner_playback_variant(
                owner_id, ref_id
            )
        except VaultNotFoundError:
            return False
        if playable != variant_id:
            return False
        duration = session.get_one(AudioVariantRow, variant_id).duration_ms
        return (
            duration is not None
            and 0 < duration <= MAX_TRACK_SECONDS * 1000
            and abs(duration - expected_ms) <= max(3000, expected_ms * 0.02)
        )

    def status(self, principal: Principal, identity: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(InternetAcquisitionRow, identity)
            if row is None or row.user_id != principal.user_id:
                raise MusicError("music_acquisition_not_found", "The download is unavailable.", 404)
            return self._status_view(session, row)

    @staticmethod
    def _restore_ready(session: Session, principal: Principal, row: InternetAcquisitionRow) -> None:
        if row.state != "READY":
            return
        InternetMusicService._ensure_vault_entry(session, principal, row.user_track_ref_id)

    @staticmethod
    def _ensure_vault_entry(
        session: Session, principal: Principal, ref_id: UUID | None, *, create: bool = False
    ) -> None:
        ref = session.get(UserTrackRefRow, ref_id) if ref_id else None
        if ref is None or ref.deleted_at is not None:
            raise MusicError("music_track_removed", "The track needs library review.", 409)
        entry = session.scalar(
            select(LibraryEntryRow)
            .where(
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.user_track_ref_id == ref.user_track_ref_id,
            )
            .order_by(LibraryEntryRow.removed_at.asc().nullsfirst())
            .limit(1)
            .with_for_update()
        )
        if entry is None and not create:
            raise MusicError("music_track_removed", "The track needs library review.", 409)
        if entry is None:
            identity = LibraryRepository(session).add_library_entry(
                principal,
                library_entry_id=uuid4(),
                user_track_ref_id=ref.user_track_ref_id,
                source="SEARCH",
                availability_status="VAULT",
                now=datetime.now(UTC),
            )
            entry = session.get_one(LibraryEntryRow, identity)
        elif entry.removed_at is not None:
            LibraryRepository(session).restore_library_entry(
                principal,
                entry.library_entry_id,
                base_version=entry.row_version,
                now=datetime.now(UTC),
            )
        else:
            return
        session.execute(
            insert(SyncEventRow)
            .values(
                event_id=uuid5(
                    NAMESPACE_URL, f"music-restore:{entry.library_entry_id}:{entry.row_version}"
                ),
                user_id=principal.user_id,
                origin_device_id=None,
                event_type="LIBRARY_ENTRY_UPSERTED",
                schema_version=1,
                aggregate_type="LIBRARY_ENTRY",
                aggregate_id=entry.library_entry_id,
                payload={
                    "server_user_track_ref_id": str(ref.user_track_ref_id),
                    "source": entry.source,
                    "availability_status": entry.availability_status,
                    "removed_at_ms": None,
                },
                operation="UPSERT",
                server_row_version=entry.row_version,
            )
            .on_conflict_do_nothing(index_elements=[SyncEventRow.event_id])
        )

    def _status_view(self, session: Session, row: InternetAcquisitionRow) -> dict[str, Any]:
        result = self._view(row)
        if row.state == "READY":
            if row.upload_id is None and row.user_track_ref_id is not None:
                try:
                    playable = PostgresVaultRuntime(session).resolve_playback_variant(
                        VaultPrincipal(row.user_id, row.device_id), row.user_track_ref_id
                    )
                except VaultNotFoundError:
                    playable = None
                if playable is None or playable != row.audio_variant_id:
                    result.update(
                        state="FAILED", error_code="music_track_removed", audio_variant_id=None
                    )
            return result
        job = session.get(JobRow, row.job_id)
        failed = job is not None and job.state in {"FAILED", "CANCELLED"}
        if row.state == "PROCESSING" and row.upload_id is not None:
            upload = session.get(UploadSessionRow, row.upload_id)
            if (
                upload
                and upload.source_internet_acquisition_id == row.acquisition_id
                and upload.job_id
            ):
                ingest_job = session.get(JobRow, upload.job_id)
                failed = failed or (
                    ingest_job is not None and ingest_job.state in {"FAILED", "CANCELLED"}
                )
        if failed:
            result.update(state="FAILED", error_code=row.error_code or "music_acquisition_failed")
        return result

    @staticmethod
    def _search_view(row: InternetSearchRow) -> dict[str, Any]:
        return {
            "contract_version": "internet-music-v1",
            "search_id": str(row.search_id),
            "candidates": [
                {key: value for key, value in item.items() if key != "policy"}
                for item in row.candidates
                if item.get("policy") == SEARCH_POLICY
            ],
        }

    @staticmethod
    def _view(row: InternetAcquisitionRow) -> dict[str, Any]:
        return {
            "contract_version": "internet-music-v1",
            "acquisition_id": str(row.acquisition_id),
            "state": row.state,
            "user_track_ref_id": str(row.user_track_ref_id) if row.user_track_ref_id else None,
            "audio_variant_id": str(row.audio_variant_id) if row.audio_variant_id else None,
            "error_code": row.error_code,
        }


class InternetMusicHandler:
    def __init__(self, service: InternetMusicService) -> None:
        self.service = service

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        identity = UUID(str(lease.payload["acquisition_id"]))
        with self.service.sessions.begin() as session:
            row = self._locked(session, identity, context)
            if row.state == "READY":
                return
            if row.selected_snapshot.get("policy") != SEARCH_POLICY:
                row.state, row.error_code = "FAILED", "provider_track_policy_rejected"
                session.commit()
                raise TerminalJobError("provider_track_policy_rejected")
            principal = Principal(row.user_id, row.device_id, UUID(int=0), AccountRole.USER)
            # Worker authority is rechecked against live account/device state on every attempt.
            active = session.scalar(
                text("""SELECT 1 FROM account.user_account u
                JOIN account.device d ON d.user_id=u.user_id
                WHERE u.user_id=:owner AND d.device_id=:device AND u.status='ACTIVE'
                AND u.deleted_at IS NULL AND d.revoked_at IS NULL"""),
                {"owner": row.user_id, "device": row.device_id},
            )
            if active != 1:
                raise MusicError("music_owner_unavailable", "The owner is unavailable.", 403)
            if row.user_track_ref_id is None:
                ref = UserTrackRefRow(
                    user_id=row.user_id,
                    raw_title=row.selected_snapshot["title"],
                    raw_artist=row.selected_snapshot["artist"],
                    raw_duration_ms=row.selected_snapshot["duration_ms"],
                    resolution_status="UNRESOLVED",
                )
                session.add(ref)
                session.flush()
                session.add(
                    LibraryEntryRow(
                        user_id=row.user_id,
                        user_track_ref_id=ref.user_track_ref_id,
                        source="SEARCH",
                        availability_status="PENDING",
                    )
                )
                row.user_track_ref_id = ref.user_track_ref_id
            ref_id, candidate_id, upload_id = row.user_track_ref_id, row.candidate_id, row.upload_id
            row.state = "DOWNLOADING" if upload_id is None else "PROCESSING"
            row.error_code = None
        recording_id = self._guarded(
            identity, context, lambda: self.service.library.prepare(principal, ref_id)
        )
        try:
            remote = self.service.vault.status(principal, upload_id) if upload_id else None
            if remote is None or remote.state == "OPEN":
                with tempfile.TemporaryDirectory(prefix="autplay-music-") as scratch:
                    path = self.service.provider.download(candidate_id, Path(scratch))
                    context.raise_if_cancelled()
                    with path.open("rb") as audio:
                        digest = hashlib.file_digest(audio, "sha256").hexdigest()
                    if remote is None:
                        remote, _ = self._guarded(
                            identity,
                            context,
                            lambda: self.service.vault.create(
                                principal,
                                recording_id=recording_id,
                                expected_size=path.stat().st_size,
                                declared_sha256=digest,
                                idempotency_key=f"internet-music:{identity}:{digest}",
                            ),
                        )
                        upload_id = remote.upload_id
                        with self.service.sessions.begin() as session:
                            row = self._locked(session, identity, context)
                            row.upload_id, row.state = upload_id, "UPLOADING"
                    if remote.expected_size != path.stat().st_size:
                        raise ValueError("music_source_changed")
                    with path.open("rb") as audio:
                        offset = remote.offset
                        audio.seek(offset)
                        while chunk := audio.read(1024 * 1024):
                            context.raise_if_cancelled()
                            next_offset = self._guarded(
                                identity,
                                context,
                                partial(
                                    self.service.vault.append,
                                    principal,
                                    upload_id,
                                    offset=offset,
                                    chunk_index=offset // (1024 * 1024),
                                    payload=chunk,
                                    payload_sha256=hashlib.sha256(chunk).hexdigest(),
                                ),
                            )
                            offset = next_offset
                        self._guarded(
                            identity,
                            context,
                            lambda: self.service.vault.complete(principal, upload_id),
                        )
            with self.service.sessions.begin() as session:
                self._locked(session, identity, context).state = "PROCESSING"
            if upload_id is None:
                raise ValueError("music_upload_missing")
            final_upload_id = upload_id
            for _ in range(60):
                context.raise_if_cancelled()
                remote = self.service.vault.status(principal, upload_id)
                if remote.state in {"COMMITTED", "REUSED"}:
                    self._guarded(
                        identity,
                        context,
                        lambda: self.service.library.publish(principal, ref_id, final_upload_id),
                        published=True,
                    )
                    return
                if remote.state not in {"SEALED", "PROCESSING", "COMMIT_PREPARED"}:
                    raise ValueError("music_upload_failed")
                time.sleep(2)
            raise RetryableJobError("music_ingest_pending")
        except JobLeaseLost, JobCancellationRequested, JobResourceWait:
            raise
        except InternetMusicProviderError as error:
            self._record_failure(identity, context, error.code, terminal=not error.retryable)
            if error.retryable:
                raise RetryableJobError(error.code) from error
            raise TerminalJobError(error.code) from error
        except MusicError as error:
            terminal = error.status_code in {403, 404}
            self._record_failure(identity, context, error.code, terminal=terminal)
            if terminal:
                raise TerminalJobError(error.code) from error
            raise RetryableJobError(error.code) from error
        except RetryableJobError as error:
            self._record_failure(identity, context, error.error.code, terminal=False)
            raise
        except Exception as error:
            self._record_failure(identity, context, "music_acquisition_unavailable", terminal=False)
            raise RetryableJobError("music_acquisition_unavailable") from error

    def _record_failure(
        self,
        identity: UUID,
        context: JobExecutionContext,
        code: str,
        *,
        terminal: bool,
    ) -> None:
        with self.service.sessions.begin() as session:
            row = self._locked(session, identity, context)
            row.error_code = code[:100]
            if terminal:
                row.state = "FAILED"

    def _guarded[R](
        self,
        identity: UUID,
        context: JobExecutionContext,
        operation: Callable[[], R],
        *,
        published: bool = False,
    ) -> R:
        # Keep the lease row and live authority locked across the short Vault commit.
        # Cancellation, lease recovery and device revocation serialize after this boundary.
        with self.service.sessions.begin() as session:
            row = self._locked(session, identity, context)
            active = session.scalar(
                text("""SELECT 1 FROM account.user_account u
                JOIN account.device d ON d.user_id=u.user_id
                WHERE u.user_id=:owner AND d.device_id=:device AND u.status='ACTIVE'
                AND u.deleted_at IS NULL AND d.revoked_at IS NULL FOR SHARE OF u,d"""),
                {"owner": row.user_id, "device": row.device_id},
            )
            if active != 1:
                raise MusicError("music_owner_unavailable", "The owner is unavailable.", 403)
            result = operation()
            if published:
                row.state, row.audio_variant_id, row.error_code = "READY", UUID(str(result)), None
            return result

    @staticmethod
    def _locked(
        session: Session, identity: UUID, context: JobExecutionContext
    ) -> InternetAcquisitionRow:
        fence = context.fence
        job = session.scalar(
            select(JobRow)
            .where(
                JobRow.job_id == fence.job_id,
                JobRow.state == "RUNNING",
                JobRow.lease_owner == fence.worker_id,
                JobRow.attempt_count == fence.attempt_no,
                JobRow.lease_deadline > func.now(),
                JobRow.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        if job is None:
            raise JobLeaseLost
        row = session.scalar(
            select(InternetAcquisitionRow)
            .where(
                InternetAcquisitionRow.acquisition_id == identity,
                InternetAcquisitionRow.job_id == fence.job_id,
                InternetAcquisitionRow.user_id == job.user_id,
            )
            .with_for_update()
        )
        if row is None:
            raise JobLeaseLost
        row.updated_at = datetime.now(UTC)
        return row
