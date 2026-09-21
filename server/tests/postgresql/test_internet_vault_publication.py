"""Internet authority and library projection commit atomically with verified Vault bytes."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    InternetAcquisitionRow,
    LibraryEntryRow,
    RecordingCanonicalVariantRow,
    SyncEventRow,
    UploadSessionRow,
    UserAccountRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.application.music_library import MusicLibraryService
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.jobs import JobKey, LeaseTransition
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .test_internet_ingest_authority import PAYLOAD, PublicationFixture, publication
from .test_resource_admission_runtime import admission, present

__all__ = ["admission", "publication"]

METADATA = AudioTechnicalMetadata("flac", "flac", 48_000, 2, 1_000, None, 16)
EVIDENCE = ChromaprintEvidence("chromaprint", "1.6.1", 1_000, b"internet-evidence")


def storage_for(publication: PublicationFixture, tmp_path: Path) -> FilesystemVaultStorage:
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    key = OpaqueStorageKey(publication.upload_id.hex)
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    return storage


@pytest.mark.parametrize("phase", ["start", "prepare", "prepare_retry", "finalize"])
def test_original_authority_is_required_at_each_internet_publication_boundary(
    publication: PublicationFixture, tmp_path: Path, phase: str
) -> None:
    harness = publication.harness
    storage = storage_for(publication, tmp_path)
    repository = TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(harness.sessions))
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="internet-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    if phase != "start":
        ingest = present(
            repository.start_ingest(
                publication.upload_id,
                lease.fence.job_id,
                fence=lease.fence,
            )
        )
        verified = storage.verify_staging(ingest.staging_key)
        if phase in {"finalize", "prepare_retry"}:
            assert repository.prepare_commit(ingest, verified, METADATA, EVIDENCE) == "PUBLISH"
            if phase == "finalize":
                storage.commit_staging(ingest.staging_key, verified)
    with harness.sessions.begin() as session:
        lock_resource_admission(session)
        present(session.get(UserAccountRow, publication.actor.user_id)).authority_generation += 1
    if phase == "start":
        assert (
            repository.start_ingest(
                publication.upload_id,
                lease.fence.job_id,
                fence=lease.fence,
            )
            is None
        )
    elif phase in {"prepare", "prepare_retry"}:
        assert (
            repository.prepare_commit(ingest, verified, METADATA, EVIDENCE) == "SOURCE_UNAVAILABLE"
        )
    else:
        assert not repository.finalize_published(
            ingest,
            OpaqueStorageKey(verified.sha256.hex),
            METADATA,
            EVIDENCE,
            reused=False,
        )
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        assert source.state == "FAILED" and upload.state == "QUARANTINED"
        assert source.error_code == upload.error_code == "source_authorization_unavailable"
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 0
        assert (
            session.scalar(
                select(LibraryEntryRow.availability_status).where(
                    LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                )
            )
            != "VAULT"
        )


def test_real_handler_finishes_completed_source_with_one_atomic_ready_projection(
    publication: PublicationFixture,
    tmp_path: Path,
) -> None:
    harness = publication.harness
    storage = storage_for(publication, tmp_path)
    repository = TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(harness.sessions))
    with harness.sessions.begin() as session:
        assert (
            PostgresJobRepository(session).complete(publication.claim.fence)
            is LeaseTransition.APPLIED
        )
        lease = PostgresJobRepository(session).claim(
            worker_id="internet-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    handler = VaultIngestHandler(
        repository=repository,
        storage=storage,
        media=Media(),
        fingerprints=Fingerprints(),
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(harness.sessions),
        fence=lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    handler(context, lease)
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        assert source.state == "READY" and upload.state == "COMMITTED"
        assert source.audio_variant_id == upload.audio_variant_id
        assert (
            session.scalar(
                select(LibraryEntryRow.availability_status).where(
                    LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                )
            )
            == "VAULT"
        )
        events = session.scalar(select(func.count()).select_from(SyncEventRow))
    handler(context, lease)
    with harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SyncEventRow)) == events
    with harness.sessions.begin() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        entry = present(
            session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                )
            )
        )
        entry.removed_at = datetime.now(UTC)
        present(session.get(UserAccountRow, publication.actor.user_id)).authority_generation += 1
    # Completed replay still checks the ingest claim, but never restores a removed entry.
    handler(context, lease)
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        assert (
            session.scalar(
                select(LibraryEntryRow.removed_at).where(
                    LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                )
            )
            is not None
        )
        assert session.scalar(select(func.count()).select_from(SyncEventRow)) == events
    assert storage.inventory().staging_keys == ()


def test_library_projection_failure_rolls_back_vault_and_ready_then_retry_converges(
    publication: PublicationFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = publication.harness
    storage = storage_for(publication, tmp_path)
    repository = TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(harness.sessions))
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="internet-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    ingest = present(
        repository.start_ingest(
            publication.upload_id,
            lease.fence.job_id,
            fence=lease.fence,
        )
    )
    verified = storage.verify_staging(ingest.staging_key)
    assert repository.prepare_commit(ingest, verified, METADATA, EVIDENCE) == "PUBLISH"
    committed = storage.commit_staging(ingest.staging_key, verified)
    original: Callable[[Session, UUID, UUID, UUID], None] = MusicLibraryService._project_publication

    def fail_after_projection(session: Session, owner: UUID, ref: UUID, upload: UUID) -> None:
        original(session, owner, ref, upload)
        raise RuntimeError("test.abort_projection")

    with monkeypatch.context() as patch:
        patch.setattr(
            MusicLibraryService, "_project_publication", staticmethod(fail_after_projection)
        )
        with pytest.raises(RuntimeError, match=r"test\.abort_projection"):
            repository.finalize_published(
                ingest, committed.storage_key, METADATA, EVIDENCE, reused=False
            )
    with harness.sessions() as session:
        assert (
            present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id)).state
            == "PROCESSING"
        )
        assert (
            present(session.get(UploadSessionRow, publication.upload_id)).state == "COMMIT_PREPARED"
        )
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 0
        assert session.scalar(select(func.count()).select_from(RecordingCanonicalVariantRow)) == 0
        assert session.scalar(select(VaultObjectRow.commit_status)) == "STAGING"
        assert session.scalar(select(VaultReplicaRow.replica_status)) == "COPYING"
    assert repository.finalize_published(
        ingest, committed.storage_key, METADATA, EVIDENCE, reused=False
    )
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        assert source.state == "READY"
        # The owner resolver uses the same transaction and verifies the chosen servable variant.
        assert (
            PostgresVaultRuntime(session).resolve_owner_playback_variant(
                source.user_id,
                present(source.user_track_ref_id),
            )
            == source.audio_variant_id
        )


class Media:
    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        assert path.is_file()
        return METADATA


class Fingerprints:
    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        assert path.is_file()
        return EVIDENCE
