"""Real JobWorker retries, retained HTTP provider and PostgreSQL handoff."""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.internet_acquisition import PostgresInternetAcquisitionRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    JobRow,
    ProviderStagingRow,
    UploadSessionRow,
)
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.application.internet_acquisition import ControlledInternetAcquisitionHandler
from autplay.application.internet_music import INTERNET_ACQUIRE_JOB, InternetMusicService
from autplay.application.job_worker import JobHandlerRegistry, JobWorker, WorkerOutcome
from autplay.domain.vault import VaultLimits
from autplay.runtime.provider_io import ProviderIoExecutor
from autplay.runtime.vault_io import VaultIoCoordinator
from provider_download_support import local_provider
from sqlalchemy import func, select

from .test_acquisition_authority import Provider
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows provider containment")


@pytest.mark.parametrize("failure", ["none", "http", "control", "worker", "long"])
def test_job_download_handoff_and_retry_keep_one_operation(
    admission: AdmissionHarness,
    tmp_path: Path,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    service = InternetMusicService(admission.sessions, object(), Provider())
    search = UUID(service.search(actor, "synthetic acquisition", uuid4())["search_id"])
    acquisition_id = UUID(service.select(actor, search, "candidate00")["acquisition_id"])
    FilesystemVaultStorage(tmp_path)
    payload = b"provider HTTP data" * 4096
    coordinator = VaultIoCoordinator(admission.service, maximum=1)
    coordinator.start()
    retrying = failure in {"http", "control", "worker"}
    try:
        with local_provider(
            payload,
            fail_first=failure == "http",
            duration_seconds=32 if failure == "long" else 0,
        ) as provider:
            handler = ControlledInternetAcquisitionHandler(
                PostgresInternetAcquisitionRepository(admission.sessions),
                admission.service,
                ProviderIoExecutor(
                    coordinator,
                    root=tmp_path,
                    limits=VaultLimits(),
                    tree_factory=WindowsJobTree,
                    launch=provider.launch,
                ),
            )
            worker = JobWorker(
                uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
                worker_id="controlled-internet",
                registry=JobHandlerRegistry({INTERNET_ACQUIRE_JOB: handler}),
            )
            original_operation = None
            if retrying:
                original_start = threading.Thread.start

                def start(thread: threading.Thread) -> None:
                    if thread.name == f"vault-io-{failure}":
                        raise RuntimeError("synthetic unavailable thread")
                    original_start(thread)

                with monkeypatch.context() as patch:
                    patch.setattr(threading.Thread, "start", start)
                    assert worker.run_once().outcome is WorkerOutcome.RETRY_SCHEDULED
                if failure in {"control", "worker"}:
                    assert not provider.requested.is_set()
                with admission.sessions.begin() as session:
                    job = present(session.scalar(select(JobRow)))
                    assert job.state == "RETRY_WAIT" and not job.resource_waiting
                    assert job.error_code == (
                        "resource_provider_failed"
                        if failure == "http"
                        else "resource_execution_busy"
                    )
                    source = present(session.get(InternetAcquisitionRow, acquisition_id))
                    assert source.error_code == job.error_code
                    job.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
                    operation = present(session.scalar(select(ResourceAdmissionRow)))
                    assert operation.state != "RELEASED"
                    original_operation = operation.operation_id

                # The successor must rebind the same operation after cleanup.
                # Shutdown is not needed: normal ownership cleanup must settle.
                async def settle() -> None:
                    from .test_vault_io_coordinator import eventually

                    await eventually(lambda: not coordinator.pending())

                asyncio.run(settle())
            started = monotonic()
            assert worker.run_once().outcome is WorkerOutcome.COMPLETED
            if failure == "long":
                # Cross both the 5-second IO TTL and the 30-second operation TTL.
                assert monotonic() - started >= 32
            assert provider.requested.is_set()
        with admission.sessions() as session:
            source = present(session.get(InternetAcquisitionRow, acquisition_id))
            assert source.error_code is None
            upload = present(session.get(UploadSessionRow, source.upload_id))
            assert upload.expected_size == len(payload)
            receipts = session.scalars(select(ProviderStagingRow)).all()
            assert len(receipts) == (2 if failure in {"http", "worker"} else 1)
            assert all(receipt.closed_at is not None for receipt in receipts)
            assert sum(receipt.state == "HANDED_OFF" for receipt in receipts) == 1
            operation = present(session.scalar(select(ResourceAdmissionRow)))
            assert operation.state == "RELEASED"
            if retrying:
                assert operation.operation_id == original_operation
                assert operation.generation == 2
            assert session.scalar(select(func.count()).select_from(ResourceAdmissionRow)) == 1
            assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
            assert (tmp_path / "staging" / upload.staging_key).read_bytes() == payload
        assert not coordinator.pending() and not coordinator.supervisor.snapshot()
    finally:
        assert not asyncio.run(coordinator.shutdown())
