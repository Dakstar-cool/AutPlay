"""Real PostgreSQL and filesystem evidence for manual A1B expansion readiness."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.discovery_runtime import (
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.models import (
    AcquisitionRecordRow,
    BulkOperationRow,
    DiscoveryCandidateRow,
    ImportJobRow,
    JobRow,
    LibraryEntryRow,
    ListeningEventRow,
    SyncEventRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    ProviderArtist,
    ProviderArtistTracks,
)
from autplay.domain.jobs import LeaseFence
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
)
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker


def _sessions(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_import(session: Session, name: str) -> tuple[UUID, UUID]:
    owner = UserAccountRow(display_name=name, role="OWNER", status="ACTIVE")
    session.add(owner)
    session.flush([owner])
    job = JobRow(
        job_type="library.import",
        schema_version=1,
        user_id=owner.user_id,
        priority=3,
        state="QUEUED",
        payload={},
    )
    session.add(job)
    session.flush([job])
    imported = ImportJobRow(
        job_id=job.job_id,
        user_id=owner.user_id,
        adapter_id="autplay.txt",
        adapter_version="1.0.0",
        input_sha256=hashlib.sha256(name.encode()).digest(),
        input_schema_version="1",
        mode="LIBRARY_ONLY",
        summary={"format": "TXT"},
    )
    session.add(imported)
    session.flush([imported])
    return owner.user_id, imported.import_job_id


def _artist() -> ProviderArtist:
    return ProviderArtist("20", "Open Artist", "https://www.jamendo.com/artist/20")


def _track(track_id: str = "10", *, allowed: bool = True) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        track_id,
        "20",
        f"Popular {track_id}",
        "Open Artist",
        "Open Album",
        180,
        "https://creativecommons.org/licenses/by/4.0/",
        f"https://www.jamendo.com/track/{track_id}",
        allowed,
        (
            f"https://prod-1.storage.jamendo.com/download/track/{track_id}/mp32/"
            if allowed
            else None
        ),
    )


def _preview_values(
    track: DiscoveryCandidate,
) -> tuple[tuple[BulkArtistResolution, ...], tuple[ProviderArtistTracks, ...]]:
    artist = _artist()
    return (
        (BulkArtistResolution("Open Artist", 4, "EXACT_MATCH", artist),),
        (ProviderArtistTracks(artist.provider_artist_id, 1, (track,)),),
    )


def test_bulk_preview_and_start_are_owner_scoped_exactly_replayable(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    try:
        with sessions.begin() as session:
            owner_id, import_id = _seed_import(session, "A1B owner")
            other_id, _ = _seed_import(session, "Other owner")
        preview_operation = uuid4()
        start_operation = uuid4()
        resolutions, pages = _preview_values(_track())
        with sessions.begin() as session:
            first = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=preview_operation,
                resolutions=resolutions,
                pages=pages,
            )
        with sessions.begin() as session:
            replay = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=preview_operation,
                resolutions=resolutions,
                pages=pages,
            )
        assert first.bulk_operation_id == replay.bulk_operation_id
        assert first.replayed is False and replay.replayed is True

        with sessions() as session, pytest.raises(BulkDiscoveryError, match="operation_conflict"):
            _, changed_pages = _preview_values(_track("11"))
            PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=preview_operation,
                resolutions=resolutions,
                pages=changed_pages,
            )
        with (
            sessions() as session,
            pytest.raises(BulkDiscoveryError, match="discovery_target_not_found"),
        ):
            PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=other_id,
                import_job_id=import_id,
                operation_id=uuid4(),
                resolutions=resolutions,
                pages=pages,
            )

        with sessions.begin() as session:
            started = PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=owner_id,
                bulk_operation_id=first.bulk_operation_id,
                operation_id=start_operation,
            )
        with sessions.begin() as session:
            start_replay = PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=owner_id,
                bulk_operation_id=first.bulk_operation_id,
                operation_id=start_operation,
            )
            queued_job = session.scalar(
                select(JobRow).where(JobRow.job_type == "discovery.acquire")
            )
        assert started.queued_count == 1 and started.replayed is False
        assert start_replay.replayed is True
        assert queued_job is not None
        assert isinstance(queued_job.payload, dict)
        assert set(queued_job.payload) == {"candidate_id"}
        assert "url" not in str(queued_job.payload).casefold()

        with sessions() as session, pytest.raises(BulkDiscoveryError, match="operation_conflict"):
            PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=owner_id,
                bulk_operation_id=first.bulk_operation_id,
                operation_id=uuid4(),
            )

        with sessions.begin() as session:
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(DiscoveryCandidateRow.user_id == owner_id)
            )
            assert candidate is not None
            candidate.acquisition_state = "FAILED_TERMINAL"
            candidate.error_code = "source_authorization_unavailable"
        with sessions.begin() as session:
            terminal_preview = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=uuid4(),
                resolutions=resolutions,
                pages=pages,
            )
            terminal_start = PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=owner_id,
                bulk_operation_id=terminal_preview.bulk_operation_id,
                operation_id=uuid4(),
            )
        with sessions.begin() as session:
            acquisition_jobs = tuple(
                session.scalars(select(JobRow).where(JobRow.job_type == "discovery.acquire"))
            )
        assert terminal_start.state == "FAILED_TERMINAL"
        assert terminal_start.queued_count == 0 and terminal_start.failed_count == 1
        assert len(acquisition_jobs) == 1
    finally:
        engine.dispose()


def test_search_add_uses_same_owner_scoped_vault_queue(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "Search owner")
            other_id, _ = _seed_import(session, "Other search owner")
        operation_id = uuid4()
        evidence = _track()
        with sessions.begin() as session:
            first = PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=owner_id,
                operation_id=operation_id,
                evidence=evidence,
            )
        with sessions.begin() as session:
            replay = PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=owner_id,
                operation_id=operation_id,
                evidence=evidence,
            )
            operation = session.get(BulkOperationRow, first.bulk_operation_id)
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == evidence.provider_track_id,
                )
            )
            queued_job = session.get(JobRow, candidate.job_id) if candidate is not None else None
            status = PostgresBulkDiscoveryRepository(session).status(
                owner_user_id=owner_id,
                bulk_operation_id=first.bulk_operation_id,
            )

        assert first.replayed is False and replay.replayed is True
        assert first.bulk_operation_id == replay.bulk_operation_id
        assert operation is not None and operation.import_job_id is None
        assert operation.state == "QUEUED" and status.state == "QUEUED"
        assert candidate is not None and candidate.acquisition_state == "QUEUED"
        assert queued_job is not None and queued_job.payload == {
            "candidate_id": str(candidate.candidate_id)
        }
        assert "url" not in str(queued_job.payload).casefold()

        with sessions() as session, pytest.raises(BulkDiscoveryError, match="operation_conflict"):
            PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=owner_id,
                operation_id=operation_id,
                evidence=_track("11"),
            )
        with (
            sessions() as session,
            pytest.raises(BulkDiscoveryError, match="discovery_target_not_found"),
        ):
            PostgresBulkDiscoveryRepository(session).status(
                owner_user_id=other_id,
                bulk_operation_id=first.bulk_operation_id,
            )
    finally:
        engine.dispose()


def test_expired_raw_preview_cleanup_is_bounded(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, import_id = _seed_import(session, "Retention owner")
            preview = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=uuid4(),
                resolutions=_preview_values(_track())[0],
                pages=_preview_values(_track())[1],
            )
            operation = session.get(BulkOperationRow, preview.bulk_operation_id)
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(DiscoveryCandidateRow.user_id == owner_id)
            )
            assert operation is not None and candidate is not None
            operation.updated_at = now - timedelta(days=31)
            candidate.updated_at = now - timedelta(days=31)
            candidate_id = candidate.candidate_id

        with sessions.begin() as session:
            first_deleted = PostgresBulkDiscoveryRepository(session).cleanup_expired(
                now=now,
                limit=1,
            )
        with sessions.begin() as session:
            assert session.get(BulkOperationRow, preview.bulk_operation_id) is None
            assert session.get(DiscoveryCandidateRow, candidate_id) is not None
            second_deleted = PostgresBulkDiscoveryRepository(session).cleanup_expired(
                now=now,
                limit=1,
            )
        with sessions.begin() as session:
            assert session.get(DiscoveryCandidateRow, candidate_id) is None
        assert first_deleted == 1 and second_deleted == 1

        with sessions() as session, pytest.raises(ValueError, match=r"within 1\.\.10000"):
            PostgresBulkDiscoveryRepository(session).cleanup_expired(now=now, limit=0)
    finally:
        engine.dispose()


def test_ready_requires_vault_provenance_library_sync_and_analysis_job(
    database_url: str, tmp_path: Path
) -> None:
    engine, sessions = _sessions(database_url)
    limits = VaultLimits(max_object_bytes=1024 * 1024, max_chunk_bytes=1024)
    storage = FilesystemVaultStorage(tmp_path / "vault", limits=limits)
    payload = b"A1B fixture audio bytes"
    track = _track()
    try:
        with sessions.begin() as session:
            owner_id, import_id = _seed_import(session, "Ready owner")
            preview = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=uuid4(),
                resolutions=_preview_values(track)[0],
                pages=_preview_values(track)[1],
            )
            PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=owner_id,
                bulk_operation_id=preview.bulk_operation_id,
                operation_id=uuid4(),
            )
        now = datetime.now(UTC)
        with sessions.begin() as session:
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(DiscoveryCandidateRow.user_id == owner_id)
            )
            assert candidate is not None and candidate.job_id is not None
            acquisition_job = session.get(JobRow, candidate.job_id)
            assert acquisition_job is not None
            acquisition_job.state = "RUNNING"
            acquisition_job.lease_owner = "a1b-test-worker"
            acquisition_job.lease_deadline = now + timedelta(minutes=2)
            acquisition_job.heartbeat_at = now
            acquisition_job.started_at = now
            acquisition_job.attempt_count = 1
            fence = LeaseFence(acquisition_job.job_id, "a1b-test-worker", 1)
            candidate_id = candidate.candidate_id
        staging_key = OpaqueStorageKey(f"disc-{candidate_id.hex}")
        storage.create_staging(staging_key)
        storage.write_chunk(
            staging_key,
            offset=0,
            payload=payload,
            payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
        )
        verified = storage.verify_staging(staging_key)
        with sessions.begin() as session:
            repository = PostgresBulkDiscoveryRepository(session)
            target = repository.claim_acquisition(
                candidate_id=candidate_id,
                owner_user_id=owner_id,
                fence=fence,
            )
            assert target is not None and target.provider_track_id == "10"
        with sessions.begin() as session:
            prepared = PostgresBulkDiscoveryRepository(session).prepare_ingest(
                candidate_id=candidate_id,
                owner_user_id=owner_id,
                fence=fence,
                evidence=track,
                staging_key=staging_key,
                verified=verified,
                limits=limits,
            )

        metadata = AudioTechnicalMetadata("mp3", "mp3", 48_000, 2, 180_000, 128_000, None)
        fingerprint = ChromaprintEvidence("chromaprint", "1.6.1", 180_000, b"fixture-fp")
        with sessions.begin() as session:
            vault = PostgresVaultRuntime(session)
            ingest = vault.start_ingest(prepared.upload_session_id, prepared.ingest_job_id)
            assert ingest is not None
        with sessions.begin() as session:
            assert (
                PostgresVaultRuntime(session).prepare_commit(
                    ingest, verified, metadata, fingerprint
                )
                == "PUBLISH"
            )
        committed = storage.commit_staging(staging_key, verified)
        with sessions.begin() as session:
            assert PostgresVaultRuntime(session).finalize_published(
                ingest,
                committed.storage_key,
                metadata,
                fingerprint,
                reused=False,
            )

        verified_object = storage.verify_object(committed.storage_key)
        assert verified_object.byte_size == len(payload)
        assert verified_object.sha256.value == hashlib.sha256(payload).digest()
        with sessions.begin() as session:
            candidate = session.get(DiscoveryCandidateRow, candidate_id)
            operation = session.get(BulkOperationRow, preview.bulk_operation_id)
            library_entry = (
                session.get(LibraryEntryRow, candidate.library_entry_id) if candidate else None
            )
            acquisition = session.scalar(
                select(AcquisitionRecordRow).where(
                    AcquisitionRecordRow.authorized_by_user_id == owner_id
                )
            )
            analysis_job = session.scalar(
                select(JobRow).where(JobRow.job_type == "audio.standard_analysis")
            )
            event_types = set(
                session.scalars(
                    select(SyncEventRow.event_type).where(SyncEventRow.user_id == owner_id)
                )
            )
            impressions = session.scalar(
                select(ListeningEventRow).where(ListeningEventRow.user_id == owner_id).limit(1)
            )
        assert candidate is not None
        assert candidate.acquisition_state == "READY" and candidate.analysis_state == "QUEUED"
        assert candidate.audio_variant_id is not None and candidate.staging_key is None
        assert operation is not None and operation.state == "COMPLETED"
        assert operation.ready_count == 1 and operation.failed_count == 0
        assert library_entry is not None and library_entry.availability_status == "VAULT"
        assert acquisition is not None and acquisition.rights_capability == "AUTHORIZED_DOWNLOAD"
        assert acquisition.source_uri_encrypted is None and acquisition.adapter_version == "1.0.0"
        assert analysis_job is not None
        assert analysis_job.payload == {"candidate_id": str(candidate_id)}
        assert {"USER_TRACK_REF_PATCHED", "LIBRARY_ENTRY_UPSERTED"}.issubset(event_types)
        assert impressions is None

        analysis_now = datetime.now(UTC)
        with sessions.begin() as session:
            analysis_job = session.get(JobRow, analysis_job.job_id)
            assert analysis_job is not None
            analysis_job.state = "RUNNING"
            analysis_job.lease_owner = "analysis-test-worker"
            analysis_job.lease_deadline = analysis_now + timedelta(minutes=2)
            analysis_job.heartbeat_at = analysis_now
            analysis_job.started_at = analysis_now
            analysis_job.attempt_count = 1
            analysis_fence = LeaseFence(analysis_job.job_id, "analysis-test-worker", 1)
        with sessions.begin() as session:
            assert PostgresBulkDiscoveryRepository(session).claim_analysis(
                candidate_id=candidate_id,
                owner_user_id=owner_id,
                fence=analysis_fence,
            )
        with sessions.begin() as session:
            assert PostgresBulkDiscoveryRepository(session).complete_analysis(
                candidate_id=candidate_id,
                owner_user_id=owner_id,
                fence=analysis_fence,
            )
        with sessions.begin() as session:
            final_candidate = session.get(DiscoveryCandidateRow, candidate_id)
            assert final_candidate is not None
            assert final_candidate.analysis_state == "COMPLETE"
    finally:
        engine.dispose()


def test_concurrent_exact_start_creates_one_acquisition_job(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    try:
        with sessions.begin() as session:
            owner_id, import_id = _seed_import(session, "Concurrent owner")
            resolutions, pages = _preview_values(_track())
            preview = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=owner_id,
                import_job_id=import_id,
                operation_id=uuid4(),
                resolutions=resolutions,
                pages=pages,
            )
        operation_id = uuid4()

        def start_once() -> bool:
            with sessions.begin() as session:
                return (
                    PostgresBulkDiscoveryRepository(session)
                    .start(
                        owner_user_id=owner_id,
                        bulk_operation_id=preview.bulk_operation_id,
                        operation_id=operation_id,
                    )
                    .replayed
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            replayed = tuple(executor.map(lambda _: start_once(), range(2)))
        with sessions.begin() as session:
            jobs = tuple(
                session.scalars(select(JobRow).where(JobRow.job_type == "discovery.acquire"))
            )
        assert sorted(replayed) == [False, True]
        assert len(jobs) == 1
    finally:
        engine.dispose()
