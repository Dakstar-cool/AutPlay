"""Real job recovery cannot let the replaced ingest attempt mutate its successor."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    JobRow,
    UploadSessionRow,
    VaultObjectRow,
)
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.job_worker import JobLeaseLost
from autplay.application.vault_ingest import IngestSession
from autplay.domain.jobs import JobKey, JobLease, RetryPolicy
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
    VerifiedStagedFile,
)
from psycopg import Connection
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .test_resource_admission_runtime import present
from .test_vault_runtime import _seed_processing_uploads

INGEST = JobKey("vault.ingest", 1)
METADATA = AudioTechnicalMetadata("flac", "flac", 48_000, 2, 1_000, None, 16)
EVIDENCE = ChromaprintEvidence("chromaprint", "1.6.1", 1_000, b"fence-evidence")


@dataclass(frozen=True)
class IngestFixture:
    sessions: sessionmaker[Session]
    repository: TransactionalIngestRepository
    storage: FilesystemVaultStorage
    upload_id: UUID
    lease: JobLease
    verified: VerifiedStagedFile

    def start(self, lease: JobLease) -> IngestSession:
        return present(
            self.repository.start_ingest(self.upload_id, lease.fence.job_id, fence=lease.fence)
        )

    def replace_worker(self) -> JobLease:
        with self.sessions.begin() as session:
            present(session.get(JobRow, self.lease.fence.job_id)).lease_deadline = datetime.now(
                UTC
            ) - timedelta(seconds=1)
        with self.sessions.begin() as session:
            recovered = PostgresJobRepository(session).recover_expired(
                supported=(INGEST,), limit=1, policy=RetryPolicy()
            )
            assert len(recovered) == 1
        with self.sessions.begin() as session:
            present(session.get(JobRow, self.lease.fence.job_id)).scheduled_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
        with self.sessions.begin() as session:
            lease = PostgresJobRepository(session).claim(
                worker_id="ingest-successor",
                supported=(INGEST,),
                lease_interval=timedelta(minutes=2),
                limit=1,
            )[0]
            assert lease.fence.attempt_no == self.lease.fence.attempt_no + 1
            return lease


@pytest.fixture
def ingest(
    database_connection: Connection[Any], database_url: str, tmp_path: Path
) -> Iterator[IngestFixture]:
    payload = b"synthetic ingest fencing evidence"
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    _, uploads = _seed_processing_uploads(
        database_connection,
        count=1,
        expected_size=len(payload),
        declared_sha256=digest.value,
        sealed=True,
    )
    database_connection.commit()
    upload_id, job_id, raw_key = uploads[0]
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    key = OpaqueStorageKey(raw_key)
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=digest)
    try:
        with sessions.begin() as session:
            present(session.get(JobRow, job_id)).payload = {"upload_session_id": str(upload_id)}
        with sessions.begin() as session:
            lease = PostgresJobRepository(session).claim(
                worker_id="ingest-original",
                supported=(INGEST,),
                lease_interval=timedelta(minutes=2),
                limit=1,
            )[0]
        yield IngestFixture(
            sessions,
            TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions)),
            storage,
            upload_id,
            lease,
            storage.verify_staging(key),
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("boundary", ["start", "prepare", "finalize", "quarantine"])
def test_replaced_ingest_is_rejected_before_any_upload_mutation(
    ingest: IngestFixture, boundary: str
) -> None:
    original = ingest.start(ingest.lease)
    if boundary == "finalize":
        assert (
            ingest.repository.prepare_commit(original, ingest.verified, METADATA, EVIDENCE)
            == "PUBLISH"
        )
        ingest.storage.commit_staging(original.staging_key, ingest.verified)
    replacement = ingest.replace_worker()
    successor = ingest.start(replacement)
    with ingest.sessions() as session:
        before = present(session.get(UploadSessionRow, ingest.upload_id)).state
    with pytest.raises(JobLeaseLost):
        match boundary:
            case "start":
                ingest.start(ingest.lease)
            case "prepare":
                ingest.repository.prepare_commit(original, ingest.verified, METADATA, EVIDENCE)
            case "finalize":
                ingest.repository.finalize_published(
                    original,
                    OpaqueStorageKey(ingest.verified.sha256.hex),
                    METADATA,
                    EVIDENCE,
                    reused=False,
                )
            case "quarantine":
                ingest.repository.quarantine(original, "vault.integrity_mismatch")
    with ingest.sessions() as session:
        row = present(session.get(UploadSessionRow, ingest.upload_id))
        assert row.state == before and row.error_code is None
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 0
    assert (
        ingest.repository.prepare_commit(successor, ingest.verified, METADATA, EVIDENCE)
        == "PUBLISH"
    )
    committed = ingest.storage.commit_staging(successor.staging_key, ingest.verified)
    assert ingest.repository.finalize_published(
        successor, committed.storage_key, METADATA, EVIDENCE, reused=False
    )
    with pytest.raises(JobLeaseLost):
        ingest.repository.quarantine(original, "vault.integrity_mismatch")
    with ingest.sessions() as session:
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "COMMITTED"
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 1


@pytest.mark.parametrize("invalid", ["cancel", "expired", "payload", "owner", "type"])
def test_live_fence_also_requires_uncancelled_job_and_exact_upload_identity(
    ingest: IngestFixture, invalid: str
) -> None:
    with ingest.sessions.begin() as session:
        job = present(session.get(JobRow, ingest.lease.fence.job_id))
        match invalid:
            case "cancel":
                job.cancel_requested_at = datetime.now(UTC)
            case "expired":
                job.lease_deadline = datetime.now(UTC) - timedelta(seconds=1)
            case "payload":
                job.payload = {"upload_session_id": str(uuid4())}
            case "owner":
                job.user_id = None
            case "type":
                job.job_type = "other.test"
    with pytest.raises(JobLeaseLost):
        ingest.start(ingest.lease)
    with ingest.sessions() as session:
        row = present(session.get(UploadSessionRow, ingest.upload_id))
        assert row.state == "SEALED" and row.error_code is None


@pytest.mark.parametrize("boundary", ["start", "prepare", "finalize", "quarantine"])
def test_lease_expiry_while_waiting_for_upload_lock_rolls_back_whole_transition(
    ingest: IngestFixture, boundary: str
) -> None:
    current = None if boundary == "start" else ingest.start(ingest.lease)
    if boundary == "finalize":
        assert current is not None
        assert (
            ingest.repository.prepare_commit(current, ingest.verified, METADATA, EVIDENCE)
            == "PUBLISH"
        )
        ingest.storage.commit_staging(current.staging_key, ingest.verified)
    with ingest.sessions() as session:
        before = present(session.get(UploadSessionRow, ingest.upload_id)).state
        objects = session.scalar(select(func.count()).select_from(VaultObjectRow))

    def blocked_transition() -> None:
        with pytest.raises(JobLeaseLost):
            match boundary:
                case "start":
                    ingest.start(ingest.lease)
                case "prepare":
                    ingest.repository.prepare_commit(
                        present(current), ingest.verified, METADATA, EVIDENCE
                    )
                case "finalize":
                    ingest.repository.finalize_published(
                        present(current),
                        OpaqueStorageKey(ingest.verified.sha256.hex),
                        METADATA,
                        EVIDENCE,
                        reused=False,
                    )
                case "quarantine":
                    ingest.repository.quarantine(present(current), "vault.integrity_mismatch")

    with ingest.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker_id = blocker.scalar(select(func.pg_backend_pid()))
        blocker.execute(
            select(UploadSessionRow)
            .where(UploadSessionRow.upload_session_id == ingest.upload_id)
            .with_for_update()
        )
        with ingest.sessions.begin() as session:
            expires = datetime.now(UTC) + timedelta(seconds=2)
            present(session.get(JobRow, ingest.lease.fence.job_id)).lease_deadline = expires
        pending = executor.submit(blocked_transition)
        try:
            deadline = time.monotonic() + 5
            with ingest.sessions() as observer:
                while not observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"blocker": blocker_id},
                ):
                    assert not pending.done(), "transition did not reach the held upload lock"
                    assert time.monotonic() < deadline, "worker did not wait on upload lock"
                    time.sleep(0.01)
                while present(observer.scalar(select(func.clock_timestamp()))) <= expires:
                    time.sleep(0.02)
        finally:
            blocker.rollback()
        pending.result(timeout=10)
    with ingest.sessions() as session:
        row = present(session.get(UploadSessionRow, ingest.upload_id))
        assert row.state == before and row.error_code is None
        assert session.scalar(select(func.count()).select_from(VaultObjectRow)) == objects
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 0
