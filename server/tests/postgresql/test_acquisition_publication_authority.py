"""Staged A1 bytes cannot gain account authority after recovery at any publication phase."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.discovery_runtime import (
    DISCOVERY_ACQUIRE_JOB,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    AudioVariantRow,
    DiscoveryCandidateRow,
    LibraryEntryRow,
    UploadSessionRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.discovery import AcquisitionAuthorizationReceipt
from autplay.domain.jobs import JobKey, TerminalJobError
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
)
from sqlalchemy import func, select

from .test_discovery_runtime import _seed_import, _sessions, _track
from .test_resource_admission_runtime import present


@pytest.mark.parametrize("phase", ["start", "prepare", "finalize", "handler_prepare"])
def test_account_generation_changed_after_handoff_prevents_vault_publication(
    database_url: str,
    tmp_path: Path,
    phase: str,
) -> None:
    engine, sessions = _sessions(database_url)
    limits = VaultLimits(max_object_bytes=1024 * 1024, max_chunk_bytes=1024)
    storage = FilesystemVaultStorage(tmp_path / "vault", limits=limits)
    payload = b"synthetic A1 publication evidence"
    track = _track()
    try:
        with sessions.begin() as session:
            owner, _ = _seed_import(session, "Publication authority")
        with sessions.begin() as session:
            PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=owner,
                operation_id=uuid4(),
                evidence=track,
            )
            candidate_id = present(session.scalar(select(DiscoveryCandidateRow.candidate_id)))
            lease = PostgresJobRepository(session).claim(
                worker_id="publication-authority",
                supported=(DISCOVERY_ACQUIRE_JOB,),
                lease_interval=timedelta(minutes=2),
                limit=1,
            )[0]
        with sessions.begin() as session:
            target = present(
                PostgresBulkDiscoveryRepository(session).claim_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    fence=lease.fence,
                )
            )
        key = OpaqueStorageKey(f"disc-{target.acquisition_attempt_id.hex}")
        storage.create_staging(key)
        storage.write_chunk(
            key,
            offset=0,
            payload=payload,
            payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
        )
        verified = storage.verify_staging(key)
        with sessions.begin() as session:
            prepared = PostgresBulkDiscoveryRepository(session).prepare_ingest(
                candidate_id=candidate_id,
                owner_user_id=owner,
                fence=lease.fence,
                evidence=track,
                staging_key=key,
                verified=verified,
                limits=limits,
            )

        def recover() -> None:
            with sessions.begin() as session:
                lock_resource_admission(session)
                present(session.get(UserAccountRow, owner)).authority_generation += 1
            with (
                sessions() as session,
                pytest.raises(BulkDiscoveryError, match="source_authorization_unavailable"),
            ):
                PostgresBulkDiscoveryRepository(session).require_ingest_boundary(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    acquisition_attempt_id=target.acquisition_attempt_id,
                    automatic_enabled=False,
                )

        if phase == "handler_prepare":
            with sessions.begin() as session:
                ingest_lease = PostgresJobRepository(session).claim(
                    worker_id="publication-ingest",
                    supported=(JobKey("vault.ingest", 1),),
                    lease_interval=timedelta(minutes=2),
                    limit=1,
                )[0]
            context = JobExecutionContext(
                uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
                fence=ingest_lease.fence,
                lease_interval=timedelta(minutes=2),
            )
            handler = VaultIngestHandler(
                repository=TransactionalIngestRepository(
                    SqlAlchemyVaultUnitOfWorkFactory(sessions)
                ),
                storage=storage,
                media=_Media(),
                fingerprints=_Fingerprints(recover),
            )
            with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
                handler(context, ingest_lease)
            assert not storage.inventory().object_keys
        elif phase == "start":
            recover()
        with sessions.begin() as session:
            ingest = PostgresVaultRuntime(session).start_ingest(
                prepared.upload_session_id,
                prepared.ingest_job_id,
            )
        if phase in {"start", "handler_prepare"}:
            assert ingest is None
        else:
            assert ingest is not None
            metadata = AudioTechnicalMetadata("mp3", "mp3", 48000, 2, 180000, 128000, None)
            evidence = ChromaprintEvidence("chromaprint", "1.6.1", 180000, b"fixture-fp")
            if phase == "prepare":
                recover()
            with sessions.begin() as session:
                action = PostgresVaultRuntime(session).prepare_commit(
                    ingest, verified, metadata, evidence
                )
            if phase == "prepare":
                assert action == "SOURCE_UNAVAILABLE"
            else:
                assert action == "PUBLISH"
                committed = storage.commit_staging(key, verified)
                recover()
                with sessions.begin() as session:
                    assert not PostgresVaultRuntime(session).finalize_published(
                        ingest,
                        committed.storage_key,
                        metadata,
                        evidence,
                        reused=False,
                        authorization_receipt=AcquisitionAuthorizationReceipt(
                            candidate_id=candidate_id,
                            provider_track_id=track.provider_track_id,
                            provider_artist_id=track.provider_artist_id,
                            boundary="PRE_MATERIALIZE",
                            checked_at=datetime.now(UTC),
                        ),
                    )
        with sessions() as session:
            upload = present(session.get(UploadSessionRow, prepared.upload_session_id))
            candidate = present(session.get(DiscoveryCandidateRow, candidate_id))
            assert (
                upload.state == "QUARANTINED"
                and upload.error_code == "source_authorization_unavailable"
            )
            assert candidate.acquisition_state != "READY" and candidate.audio_variant_id is None
            assert candidate.error_code == "source_authorization_unavailable"
            assert (
                present(
                    session.get(AcquisitionAttemptRow, target.acquisition_attempt_id)
                ).error_code
                == "source_authorization_unavailable"
            )
            assert (
                present(
                    session.get(LibraryEntryRow, candidate.library_entry_id)
                ).availability_status
                != "VAULT"
            )
            assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 0
    finally:
        engine.dispose()


class _Media:
    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        assert path.is_file()
        return AudioTechnicalMetadata("mp3", "mp3", 48000, 2, 180000, 128000, None)


class _Fingerprints:
    def __init__(self, recover: Callable[[], None]) -> None:
        self._recover = recover

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        assert path.is_file()
        self._recover()
        return ChromaprintEvidence("chromaprint", "1.6.1", 180000, b"fixture-fp")
