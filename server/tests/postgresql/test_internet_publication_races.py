"""Real row contention and repair cannot advertise an unservable Internet result."""

from __future__ import annotations

import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    InternetAcquisitionRow,
    RecordingCanonicalVariantRow,
    SyncEventRow,
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.vault_ingest import IngestSession
from autplay.application.vault_reconciliation import ReconcileMode
from autplay.domain.jobs import JobError, JobKey, LeaseTransition, RetryableJobError
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest
from autplay.entrypoints.vault_reconciliation import build_vault_reconciliation_service
from sqlalchemy import func, select

from .test_discovery_authority_clock import _blocked_by
from .test_internet_ingest_authority import PAYLOAD, PublicationFixture, publication
from .test_internet_vault_publication import EVIDENCE, METADATA, storage_for
from .test_resource_admission_runtime import admission, present

__all__ = ["admission", "publication"]


@dataclass(frozen=True)
class Prepared:
    repository: TransactionalIngestRepository
    ingest: IngestSession
    key: OpaqueStorageKey
    reused: bool = False

    def finalize(self) -> bool:
        return self.repository.finalize_published(
            self.ingest, self.key, METADATA, EVIDENCE, reused=self.reused
        )


def prepare(
    publication: PublicationFixture, storage: FilesystemVaultStorage, *, reused: bool = False
) -> Prepared:
    harness = publication.harness
    repository = TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(harness.sessions))
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="canonical-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    ingest = present(
        repository.start_ingest(publication.upload_id, lease.fence.job_id, fence=lease.fence)
    )
    verified = storage.verify_staging(ingest.staging_key)
    assert repository.prepare_commit(ingest, verified, METADATA, EVIDENCE) == (
        "REUSED" if reused else "PUBLISH"
    )
    if reused:
        return Prepared(repository, ingest, OpaqueStorageKey(verified.sha256.hex), reused=True)
    return Prepared(
        repository, ingest, storage.commit_staging(ingest.staging_key, verified).storage_key
    )


def seed_canonical(
    publication: PublicationFixture,
    storage: FilesystemVaultStorage,
    *,
    payload: bytes = b"existing canonical bytes",
) -> UUID:
    key = OpaqueStorageKey("existing-canonical-stage")
    storage.create_staging(key)
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=digest)
    committed = storage.commit_staging(key, storage.verify_staging(key))
    storage.cleanup_staging(key)
    with publication.harness.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        obj = VaultObjectRow(
            sha256=digest.value,
            byte_size=len(payload),
            detected_mime_type="audio/flac",
            commit_status="COMMITTED",
            committed_at=datetime.now(UTC),
        )
        session.add(obj)
        session.flush()
        variant = AudioVariantRow(
            recording_id=upload.target_recording_id,
            vault_object_id=obj.vault_object_id,
            codec="flac",
            container="flac",
            sample_rate_hz=48_000,
            channels=2,
            duration_ms=1_000,
            validation_status="VALID",
        )
        session.add(variant)
        session.flush()
        session.add(
            RecordingCanonicalVariantRow(
                recording_id=upload.target_recording_id,
                audio_variant_id=variant.audio_variant_id,
                policy_version="test-existing-canonical-v1",
            )
        )
        session.add(
            VaultReplicaRow(
                vault_object_id=obj.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=committed.storage_key.value,
                replica_status="AVAILABLE",
                verified_at=datetime.now(UTC),
            )
        )
        return variant.audio_variant_id


@pytest.mark.parametrize("locked", ["canonical", "variant", "object", "replica"])
def test_busy_canonical_rolls_back_then_publishes_actual_selected_variant(
    publication: PublicationFixture, tmp_path: Path, locked: str
) -> None:
    storage = storage_for(publication, tmp_path)
    prepared = prepare(publication, storage)
    canonical = seed_canonical(publication, storage)
    harness = publication.harness
    with harness.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        variant = present(blocker.get(AudioVariantRow, canonical))
        match locked:
            case "canonical":
                statement = select(RecordingCanonicalVariantRow.audio_variant_id).where(
                    RecordingCanonicalVariantRow.audio_variant_id == canonical
                )
            case "variant":
                statement = select(AudioVariantRow.audio_variant_id).where(
                    AudioVariantRow.audio_variant_id == canonical
                )
            case "object":
                statement = select(VaultObjectRow.vault_object_id).where(
                    VaultObjectRow.vault_object_id == variant.vault_object_id
                )
            case _:
                statement = select(VaultReplicaRow.vault_replica_id).where(
                    VaultReplicaRow.vault_object_id == variant.vault_object_id
                )
        blocker.execute(statement.with_for_update())
        pending = executor.submit(prepared.finalize)
        try:
            with pytest.raises(RetryableJobError, match=r"vault\.canonical_busy"):
                pending.result(timeout=5)
        finally:
            blocker.rollback()
    with harness.sessions() as session:
        assert (
            present(session.get(UploadSessionRow, publication.upload_id)).state == "COMMIT_PREPARED"
        )
        assert (
            present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id)).state
            == "PROCESSING"
        )
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 1
    assert prepared.finalize()
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        assert source.state == "READY" and upload.state == "COMMITTED"
        assert source.audio_variant_id == canonical != upload.audio_variant_id


def test_unavailable_canonical_never_becomes_ready(
    publication: PublicationFixture, tmp_path: Path
) -> None:
    storage = storage_for(publication, tmp_path)
    prepared = prepare(publication, storage)
    canonical = seed_canonical(publication, storage)
    with publication.harness.sessions.begin() as session:
        obj_id = present(session.get(AudioVariantRow, canonical)).vault_object_id
        present(
            session.scalar(select(VaultReplicaRow).where(VaultReplicaRow.vault_object_id == obj_id))
        ).replica_status = "MISSING"
        events = session.scalar(select(func.count()).select_from(SyncEventRow))
    with pytest.raises(RetryableJobError, match=r"vault\.canonical_unavailable"):
        prepared.finalize()
    with publication.harness.sessions() as session:
        assert (
            present(session.get(UploadSessionRow, publication.upload_id)).state == "COMMIT_PREPARED"
        )
        assert (
            present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id)).state
            == "PROCESSING"
        )
        assert session.scalar(select(func.count()).select_from(SyncEventRow)) == events
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 1


@pytest.mark.usefixtures("internal_io_budget")
def test_reconciliation_preserves_selected_replica_during_publication(
    publication: PublicationFixture, tmp_path: Path
) -> None:
    storage = storage_for(publication, tmp_path)
    prepared = prepare(publication, storage)
    canonical = seed_canonical(publication, storage)
    harness = publication.harness

    def reconcile() -> None:
        report = build_vault_reconciliation_service(harness.sessions, tmp_path).run(
            mode=ReconcileMode.APPLY
        )
        assert report.quarantined == report.missing == 0

    with harness.sessions() as publisher, ThreadPoolExecutor(max_workers=1) as executor:
        assert PostgresVaultRuntime(publisher).finalize_published(
            prepared.ingest, prepared.key, METADATA, EVIDENCE, reused=False
        )
        publisher_id = present(publisher.scalar(select(func.pg_backend_pid())))
        pending = executor.submit(reconcile)
        try:
            # Maintenance admission serializes with the publisher's admission lock.
            # This is not permission to inspect or repair its registered replica.
            with harness.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, publisher_id):
                    assert not pending.done() and monotonic() < until
                    sleep(0.005)
            publisher.commit()
        finally:
            publisher.rollback()
        pending.result(timeout=10)
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        assert source.state == "READY" and source.audio_variant_id == canonical


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("prepared_first", [False, True])
@pytest.mark.usefixtures("internal_io_budget")
def test_reconciliation_preserves_terminal_job_bytes_during_and_after_source_contention(
    publication: PublicationFixture, tmp_path: Path, missing: bool, prepared_first: bool
) -> None:
    storage = storage_for(publication, tmp_path)
    harness = publication.harness
    if prepared_first:
        prepared = prepare(publication, storage)
        fence = present(prepared.ingest.lease_fence)
    else:
        with harness.sessions.begin() as session:
            fence = (
                PostgresJobRepository(session)
                .claim(
                    worker_id="repair-ingest",
                    supported=(JobKey("vault.ingest", 1),),
                    lease_interval=timedelta(minutes=2),
                    limit=1,
                )[0]
                .fence
            )
    with harness.sessions.begin() as session:
        assert (
            PostgresJobRepository(session).fail_terminal(
                fence, JobError("test.ingest_terminal", {})
            )
            is LeaseTransition.APPLIED
        )
        before = present(session.get(UploadSessionRow, publication.upload_id)).state
    if missing:
        storage.cleanup_staging(OpaqueStorageKey(publication.upload_id.hex))
    initial_inventory = storage.inventory()
    with harness.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker.execute(
            select(InternetAcquisitionRow)
            .where(InternetAcquisitionRow.acquisition_id == publication.claim.acquisition_id)
            .with_for_update()
        )

        def reconcile() -> None:
            report = build_vault_reconciliation_service(harness.sessions, tmp_path).run(
                mode=ReconcileMode.APPLY, limit=1
            )
            assert report.quarantined == report.missing == report.claimed == 0

        pending = executor.submit(reconcile)
        try:
            pending.result(timeout=5)
            assert storage.inventory() == initial_inventory
            with harness.sessions() as observer:
                upload = present(observer.get(UploadSessionRow, publication.upload_id))
                assert upload.state == before and upload.error_code is None
                if prepared_first:
                    assert upload.vault_object_id is not None
        finally:
            blocker.rollback()
    reconcile()
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        assert source.state == "PROCESSING" and upload.state == before
        assert source.error_code is None and upload.error_code is None
        assert (upload.vault_object_id is not None) == prepared_first
    assert storage.inventory() == initial_inventory


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.usefixtures("internal_io_budget")
def test_finalize_never_revives_a_rejected_replica(
    publication: PublicationFixture, tmp_path: Path, reused: bool
) -> None:
    storage = storage_for(publication, tmp_path)
    if reused:
        seed_canonical(publication, storage, payload=PAYLOAD)
    prepared = prepare(publication, storage, reused=reused)
    key = prepared.key.value
    if reused:
        storage.quarantine_object(prepared.key, OpaqueStorageKey("test-missing-final"))
    else:
        object_path = tmp_path / "objects" / key[:2] / key[2:4] / key
        if os.name != "nt":
            object_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        object_path.write_bytes(b"corrupt-current-bytes")
        if os.name != "nt":
            object_path.chmod(stat.S_IRUSR)
    report = build_vault_reconciliation_service(publication.harness.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY
    )
    assert report.quarantined == report.missing == 0
    with publication.harness.sessions.begin() as session:
        replica = present(
            session.scalar(select(VaultReplicaRow).where(VaultReplicaRow.storage_key == key))
        )
        assert replica.replica_status == ("AVAILABLE" if reused else "COPYING")
        # Fixture for an authoritative rejection, independent of inventory.
        # The finalizer consumes this state; it does not rehash raw bytes here.
        expected_replica = "MISSING" if reused else "QUARANTINED"
        replica.replica_status = expected_replica
    assert not prepared.finalize()
    with publication.harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, publication.upload_id))
        assert source.state == "FAILED" and upload.state == "QUARANTINED"
        assert source.error_code == upload.error_code == "vault.integrity_conflict"
        assert source.audio_variant_id is None and upload.audio_variant_id is None
        assert (
            session.scalar(
                select(VaultReplicaRow.replica_status).where(VaultReplicaRow.storage_key == key)
            )
            == expected_replica
        )
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == int(reused)
    assert (prepared.key in storage.inventory().object_keys) == (not reused)


@pytest.mark.usefixtures("internal_io_budget")
def test_reconciliation_never_regresses_a_ready_source(
    publication: PublicationFixture, tmp_path: Path
) -> None:
    storage = storage_for(publication, tmp_path)
    prepared = prepare(publication, storage)
    assert prepared.finalize()
    storage.cleanup_staging(prepared.ingest.staging_key)
    with publication.harness.sessions.begin() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        variant_id = source.audio_variant_id
    build_vault_reconciliation_service(publication.harness.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY
    )
    with publication.harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
        assert source.state == "READY" and source.audio_variant_id == variant_id
