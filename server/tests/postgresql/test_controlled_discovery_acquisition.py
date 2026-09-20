"""Real A1 JobWorker, contained Jamendo child and atomic PostgreSQL handoff."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.controlled_discovery import PostgresControlledDiscoveryRepository
from autplay.adapters.postgresql.discovery_runtime import (
    DISCOVERY_ACQUIRE_JOB,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    DiscoveryCandidateRow,
    JobRow,
    ProviderStagingRow,
    UploadSessionRow,
)
from autplay.adapters.postgresql.models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoExecutionRow,
)
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.application.discovery_acquisition import ControlledDiscoveryAcquisitionHandler
from autplay.application.job_worker import JobHandlerRegistry, JobWorker, WorkerOutcome
from autplay.application.provider_cleanup import ProviderCleanupService
from autplay.domain.vault import VaultLimits
from autplay.runtime.discovery_io import DiscoveryIoExecutor
from autplay.runtime.vault_io import VaultIoCoordinator
from discovery_download_support import CLIENT_ID, PAYLOAD, LocalDiscovery, local_discovery
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .test_discovery_runtime import _seed_import, _track
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import internet
from .test_vault_io_coordinator import eventually
from .test_worker_resource_wait import due

__all__ = ["admission"]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows provider containment")


@dataclass(frozen=True)
class Queued:
    candidate: UUID
    attempt: UUID
    job: UUID


def enqueue(harness: AdmissionHarness) -> Queued:
    with harness.sessions.begin() as session:
        owner, _ = _seed_import(session, "Controlled A1 worker")
    with harness.sessions.begin() as session:
        PostgresBulkDiscoveryRepository(session).start_search_acquisition(
            owner_user_id=owner, operation_id=uuid4(), evidence=_track()
        )
        candidate = present(session.scalar(select(DiscoveryCandidateRow)))
        return Queued(
            candidate.candidate_id,
            present(candidate.current_acquisition_attempt_id),
            present(candidate.job_id),
        )


@contextmanager
def controlled(
    harness: AdmissionHarness,
    root: Path,
    mode: str,
    *,
    limits: VaultLimits | None = None,
    max_download_bytes: int = 150 * 1024**2,
) -> Iterator[tuple[JobWorker, VaultIoCoordinator, LocalDiscovery]]:
    limits = limits or VaultLimits()
    FilesystemVaultStorage(root)
    coordinator = VaultIoCoordinator(harness.service, maximum=1)
    coordinator.start()
    try:
        with local_discovery(mode=mode) as remote:
            handler = ControlledDiscoveryAcquisitionHandler(
                PostgresControlledDiscoveryRepository(harness.sessions, limits=limits),
                harness.service,
                DiscoveryIoExecutor(
                    coordinator,
                    root=root,
                    limits=limits,
                    tree_factory=WindowsJobTree,
                    client_id=CLIENT_ID,
                    max_download_bytes=max_download_bytes,
                    launch=remote.launch,
                ),
            )
            worker = JobWorker(
                uow_factory=SqlAlchemyJobUnitOfWorkFactory(harness.sessions),
                worker_id="controlled-discovery",
                registry=JobHandlerRegistry({DISCOVERY_ACQUIRE_JOB: handler}),
            )
            yield worker, coordinator, remote
    finally:
        assert not asyncio.run(coordinator.shutdown())


def settle(coordinator: VaultIoCoordinator) -> None:
    asyncio.run(eventually(lambda: not coordinator.pending()))
    assert not coordinator.supervisor.snapshot()


@pytest.mark.parametrize("failure", ["none", "http", "control", "worker", "record_db", "long"])
def test_retry_rebinds_one_operation_and_hands_off_once(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    admission.budget(transfers=1)
    queued = enqueue(admission)
    mode = "http" if failure == "record_db" else failure
    with controlled(admission, tmp_path, mode) as (worker, coordinator, remote):
        original_operation = None
        if failure in {"http", "control", "worker", "record_db"}:
            original_start = threading.Thread.start

            def start(thread: threading.Thread) -> None:
                if thread.name == f"vault-io-{failure}":
                    raise RuntimeError("synthetic unavailable thread")
                original_start(thread)

            with monkeypatch.context() as patch:
                patch.setattr(threading.Thread, "start", start)
                if failure == "record_db":

                    def fail(*_args: object, **_kwargs: object) -> None:
                        raise SQLAlchemyError("synthetic unavailable failure write")

                    patch.setattr(PostgresControlledDiscoveryRepository, "fail", fail)
                assert worker.run_once().outcome is WorkerOutcome.RETRY_SCHEDULED
            if failure in {"control", "worker"}:
                assert not remote.requested.is_set() and not remote.lookups
            with admission.sessions() as session:
                job = present(session.get(JobRow, queued.job))
                candidate = present(session.get(DiscoveryCandidateRow, queued.candidate))
                attempt = present(session.get(AcquisitionAttemptRow, queued.attempt))
                assert job.state == "RETRY_WAIT" and not job.resource_waiting
                if failure == "record_db":
                    assert candidate.acquisition_state == "ACQUIRING" and attempt.state == "RUNNING"
                    assert job.error_code == "database_unavailable"
                else:
                    assert candidate.acquisition_state == "RETRY_WAIT" and attempt.state == "QUEUED"
                    assert job.error_code == (
                        "discovery_acquisition_failed"
                        if failure == "http"
                        else "resource_execution_busy"
                    )
                operation = present(session.scalar(select(ResourceAdmissionRow)))
                assert operation.state != "RELEASED"
                original_operation = operation.operation_id
            settle(coordinator)
            due(admission, queued.job)
        started = monotonic()
        assert worker.run_once().outcome is WorkerOutcome.COMPLETED
        if failure == "long":
            assert monotonic() - started >= 32
        assert remote.requested.is_set()
        with admission.sessions() as session:
            candidate = present(session.get(DiscoveryCandidateRow, queued.candidate))
            assert candidate.acquisition_state == "INGESTING"
            assert candidate.current_acquisition_attempt_id == queued.attempt
            upload = present(session.scalar(select(UploadSessionRow)))
            assert upload.source_acquisition_attempt_id == queued.attempt
            assert upload.expected_size == len(PAYLOAD)
            assert (tmp_path / "staging" / upload.staging_key).read_bytes() == PAYLOAD
            receipts = session.scalars(select(ProviderStagingRow)).all()
            assert len(receipts) == (2 if failure in {"http", "worker", "record_db"} else 1)
            assert all(receipt.closed_at is not None for receipt in receipts)
            assert sum(receipt.state == "HANDED_OFF" for receipt in receipts) == 1
            # close_io() retires short-lived accounting; the durable receipt keeps proof.
            assert session.scalar(select(func.count()).select_from(ResourceIoExecutionRow)) == 0
            handed_off = next(receipt for receipt in receipts if receipt.state == "HANDED_OFF")
            assert handed_off.child_pid is not None and handed_off.exit_code == 0
            assert handed_off.closure_kind in {"PROCESS_EXIT", "SUPERVISOR_EXIT"}
            assert len(present(handed_off.child_identity_sha256)) == 32
            assert len(present(handed_off.closure_evidence_sha256)) == 32
            operation = present(session.scalar(select(ResourceAdmissionRow)))
            assert operation.state == "RELEASED"
            if original_operation is not None:
                assert operation.operation_id == original_operation and operation.generation == 2
            assert session.scalar(select(func.count()).select_from(ResourceAdmissionRow)) == 1
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
        settle(coordinator)


@pytest.mark.parametrize("mode", ["artist_before", "artist_after", "oversize"])
def test_invalid_provider_result_is_terminal_without_upload(
    admission: AdmissionHarness, tmp_path: Path, mode: str
) -> None:
    admission.budget(transfers=1)
    queued = enqueue(admission)
    limits = (
        VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
        if mode == "oversize"
        else VaultLimits()
    )
    with controlled(admission, tmp_path, mode, limits=limits) as (worker, coordinator, remote):
        assert worker.run_once().outcome is WorkerOutcome.FAILED
        assert remote.requested.is_set() is (mode != "artist_before")
        settle(coordinator)
        with admission.sessions() as session:
            candidate = present(session.get(DiscoveryCandidateRow, queued.candidate))
            attempt = present(session.get(AcquisitionAttemptRow, queued.attempt))
            job = present(session.get(JobRow, queued.job))
            assert candidate.acquisition_state == "FAILED_TERMINAL"
            assert attempt.state == job.state == "FAILED"
            assert (
                job.error_code
                == candidate.error_code
                == (
                    "discovery_response_too_large"
                    if mode == "oversize"
                    else "discovery_not_eligible"
                )
            )
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
            receipt = present(session.scalar(select(ProviderStagingRow)))
            assert receipt.state == "EXITED" and receipt.closed_at is not None
            execution_id = receipt.execution_id
        if mode == "artist_after":
            assert (tmp_path / "provider-work" / execution_id.hex / "audio.mp3").exists()
        cleanup = ProviderCleanupService(
            PostgresProviderCleanupRepository(admission.sessions),
            FilesystemProviderStorage(tmp_path),
        )
        assert cleanup.cleanup(execution_id) and cleanup.cleanup(execution_id)


def test_quota_wait_never_starts_provider_or_spends_retry_budget(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    held = fence(admission.service.acquire_worker(holder))
    queued = enqueue(admission)
    with controlled(admission, tmp_path, "none") as (worker, coordinator, remote):
        for _ in range(3):
            due(admission, queued.job)
            assert worker.run_once().outcome is WorkerOutcome.RESOURCE_WAIT
        assert not remote.requested.is_set() and not remote.lookups and not coordinator.pending()
        with admission.sessions() as session:
            job = present(session.get(JobRow, queued.job))
            assert job.resource_waiting and job.resource_wait_count == job.attempt_count == 3
            assert job.lease_owner is None and job.lease_deadline is None
            assert session.scalar(select(func.count()).select_from(ProviderStagingRow)) == 0
            operation = present(
                session.scalar(
                    select(ResourceAdmissionRow).where(
                        ResourceAdmissionRow.resource_id == queued.attempt
                    )
                )
            )
            operation_id, enqueued_at = operation.operation_id, operation.enqueued_at
        admission.service.release(holder, held)
        due(admission, queued.job)
        assert worker.run_once().outcome is WorkerOutcome.COMPLETED
        with admission.sessions() as session:
            operation = present(session.get(ResourceAdmissionRow, operation_id))
            assert operation.enqueued_at == enqueued_at and operation.state == "RELEASED"
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
        settle(coordinator)


def test_incomplete_audio_is_retained_and_never_handed_off(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget(transfers=1)
    queued = enqueue(admission)
    with controlled(admission, tmp_path, "short") as (worker, coordinator, remote):
        assert worker.run_once().outcome is WorkerOutcome.RETRY_SCHEDULED
        assert remote.requested.is_set()
        settle(coordinator)
        with admission.sessions() as session:
            job = present(session.get(JobRow, queued.job))
            assert job.error_code == "discovery_acquisition_failed"
            receipt = present(session.scalar(select(ProviderStagingRow)))
            execution_id = receipt.execution_id
            assert receipt.closed_at is not None and receipt.state == "EXITED"
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
        assert (tmp_path / "provider-work" / execution_id.hex / "audio.mp3").read_bytes() == PAYLOAD
        assert not tuple((tmp_path / "staging").iterdir())
        cleanup = ProviderCleanupService(
            PostgresProviderCleanupRepository(admission.sessions),
            FilesystemProviderStorage(tmp_path),
        )
        assert cleanup.cleanup(execution_id)
        assert not (tmp_path / "provider-work" / execution_id.hex).exists()


def test_provider_limit_below_vault_ceiling_prevents_handoff(
    admission: AdmissionHarness,
    tmp_path: Path,
) -> None:
    admission.budget(transfers=1)
    queued = enqueue(admission)
    with controlled(admission, tmp_path, "none", max_download_bytes=len(PAYLOAD) - 1) as (
        worker,
        coordinator,
        remote,
    ):
        assert worker.run_once().outcome is WorkerOutcome.FAILED
        settle(coordinator)
        assert remote.requested.is_set()
        with admission.sessions() as session:
            assert (
                present(session.get(JobRow, queued.job)).error_code
                == "discovery_response_too_large"
            )
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
            receipt = present(session.scalar(select(ProviderStagingRow)))
            assert receipt.closed_at is not None
