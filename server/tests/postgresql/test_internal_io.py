"""Real shared-capacity races, measurement review and production worker composition."""

import hashlib
import io
import json
import math
import os
import signal
import struct
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.ingest_cleanup import PostgresIngestCleanupRepository
from autplay.adapters.postgresql.ingest_execution import PostgresIngestExecutionRepository
from autplay.adapters.postgresql.internal_io import PostgresInternalIoBudget, internal_io_usage
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import JobRow, ProviderMaintenanceRow, UploadSessionRow
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.ingest_cleanup import IngestCleanupClaimRow
from autplay.adapters.postgresql.models.internal_io import InternalIoPolicyRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.system import Uuid7Generator
from autplay.application.job_worker import JobResourceWait, JobWorker, WorkerOutcome, WorkerTick
from autplay.domain.internal_io import (
    INTERNAL_IO_PATHS,
    METADATA_IO_PATHS,
    TRAINING_IO_PATHS,
    InitializeInternalIoBudget,
    InternalIoMeasurement,
)
from autplay.domain.jobs import JobError, RetryPolicy
from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ExitKind, ProcessExitEvidence
from autplay.entrypoints import worker_cpu
from autplay.entrypoints.ingest_composition import IngestWorkerRuntime
from autplay.runtime.settings import WorkerSettings
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .conftest import DatabaseHarness
from .test_discovery_authority_clock import _blocked_by
from .test_ingest_cleanup import IDENTITY, cleanup_ticket, finalized
from .test_ingest_execution import ticket_for
from .test_resource_admission_runtime import present
from .test_resource_measurements import document
from .test_resource_policy import PolicyHarness, policy
from .test_vault_ingest_fence import IngestFixture, ingest

__all__ = ["ingest", "policy"]


def measured(
    harness: PolicyHarness, *, tested_audio: int = 16, version: int = 1
) -> InitializeInternalIoBudget:
    audio = document(harness.actor.server_instance_id)
    audio["simultaneous_playbacks"] = audio["simultaneous_transfers"] = tested_audio
    payload = json.dumps(
        {
            "version": version,
            "worst_permitted_mix": True,
            "resource_measurement": audio,
            "simultaneous_internal_io": 4,
            "workload_paths": sorted(
                {1: INTERNAL_IO_PATHS, 2: METADATA_IO_PATHS, 3: TRAINING_IO_PATHS}[version]
            ),
            "internal_mib_per_second": 2,
            "minimum_internal_mib_per_second": 1,
            "successful_internal_operations": 10,
            "minimum_successful_internal_operations": 5,
        }
    ).encode()
    report = InternalIoMeasurement.parse(payload)
    return InitializeInternalIoBudget(
        uuid4(), 1, report, hashlib.sha256(payload).hexdigest(), "a" * 64, 2, 3
    )


def inventory_ticket() -> MaintenanceTicket:
    execution = uuid4()
    return MaintenanceTicket(execution, uuid4(), None, execution, MaintenanceAction.INVENTORY)


def test_metadata_workload_coverage_cannot_be_downgraded(policy: PolicyHarness) -> None:
    policy.admissions.budget(playbacks=2, transfers=2)
    repository = PostgresInternalIoBudget(policy.sessions)
    command = measured(policy, version=2)
    repository.initialize(command)
    with pytest.raises(ResourceAdmissionError, match="internal_io_workload_downgrade"):
        repository.initialize(replace(measured(policy), expected_revision=2))
    with (
        pytest.raises(DBAPIError, match="internal_io_workload_downgrade"),
        policy.sessions.begin() as session,
    ):
        present(session.get(InternalIoPolicyRow, 1)).workload_version = 1


def test_training_workload_requires_exact_joint_paths_and_cannot_downgrade(
    policy: PolicyHarness,
) -> None:
    policy.admissions.budget(playbacks=2, transfers=2)
    repository = PostgresInternalIoBudget(policy.sessions)
    command = measured(policy, version=3)
    assert repository.initialize(command)["active_limit"] == 2
    with policy.sessions() as session:
        assert present(session.get(InternalIoPolicyRow, 1)).workload_version == 3
    with pytest.raises(ResourceAdmissionError, match="internal_io_workload_downgrade"):
        repository.initialize(replace(measured(policy, version=2), expected_revision=2))
    payload = {
        "version": 3,
        "worst_permitted_mix": True,
        "resource_measurement": document(policy.actor.server_instance_id),
        "simultaneous_internal_io": 4,
        "workload_paths": sorted(TRAINING_IO_PATHS - {"TRAINING_ROOT_CLEANUP"}),
        "internal_mib_per_second": 2,
        "minimum_internal_mib_per_second": 1,
        "successful_internal_operations": 10,
        "minimum_successful_internal_operations": 5,
    }
    with pytest.raises(ResourceAdmissionError, match="internal_io_measurement_invalid"):
        InternalIoMeasurement.parse(json.dumps(payload).encode())


def test_unconfigured_capacity_yields_job_without_changing_upload(ingest: IngestFixture) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    with pytest.raises(ResourceAdmissionError, match="internal_io_budget_unconfigured"):
        repository.prepare(ticket_for(ingest))
    with pytest.raises(JobResourceWait):
        repository.defer(ingest.lease.fence, ingest.upload_id, ())
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 0
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "SEALED"
        job = present(session.get(JobRow, ingest.lease.fence.job_id))
        assert job.resource_waiting and job.resource_wait_count == job.attempt_count == 1


def test_joint_measurement_uses_audio_ceilings_and_exact_receipt(policy: PolicyHarness) -> None:
    policy.admissions.budget(playbacks=2, transfers=2)
    repository = PostgresInternalIoBudget(policy.sessions)
    with pytest.raises(ResourceAdmissionError, match="internal_io_joint_budget_exceeded"):
        repository.initialize(measured(policy, tested_audio=2))
    command = measured(policy)
    original = repository.initialize(command)
    assert original["active_limit"] == 2 and original["revision"] == 2
    changed = repository.set_limit(uuid4(), 2, 1)
    assert changed["active_limit"] == 1
    assert repository.initialize(command) == original
    with pytest.raises(ResourceAdmissionError, match="resource_operation_conflict"):
        repository.initialize(replace(command, limit=1))
    with policy.sessions() as session:
        assert present(session.get(InternalIoPolicyRow, 1)).active_limit == 1


@pytest.mark.parametrize("failure", ["missing_path", "idle", "boolean", "duplicate", "failure"])
def test_review_cannot_accept_incomplete_or_idle_internal_work(
    policy: PolicyHarness, failure: str
) -> None:
    command = measured(policy)
    base = document(policy.actor.server_instance_id)
    data: dict[str, object] = {
        "version": 1,
        "worst_permitted_mix": True,
        "resource_measurement": base,
        "simultaneous_internal_io": 4,
        "workload_paths": sorted(INTERNAL_IO_PATHS),
        "internal_mib_per_second": 2,
        "minimum_internal_mib_per_second": 1,
        "successful_internal_operations": 10,
        "minimum_successful_internal_operations": 5,
    }
    if failure == "missing_path":
        data["workload_paths"] = sorted(INTERNAL_IO_PATHS)[:-1]
    elif failure == "idle":
        data["internal_mib_per_second"] = 0
    elif failure == "boolean":
        data["simultaneous_internal_io"] = True
    elif failure == "failure":
        base["metrics"]["failed_operations"] = 1
    payload = json.dumps(data).encode()
    if failure == "duplicate":
        payload = payload.replace(b'"version": 1', b'"version": 1, "version": 1', 1)
    with pytest.raises(ResourceAdmissionError):
        replace(command, report=InternalIoMeasurement.parse(payload))


@pytest.mark.usefixtures("internal_io_budget")
def test_competing_work_and_inventory_share_one_global_slot(ingest: IngestFixture) -> None:
    budget = PostgresInternalIoBudget(ingest.sessions)
    budget.set_limit(uuid4(), 2, 1)
    work, maintenance = (
        PostgresIngestExecutionRepository(ingest.sessions),
        PostgresProviderMaintenanceRepository(ingest.sessions),
    )
    barrier = Barrier(2)

    def enter(kind: str) -> str:
        barrier.wait(timeout=5)
        try:
            if kind == "work":
                work.prepare(ticket_for(ingest))
            else:
                maintenance.prepare(inventory_ticket())
        except ResourceAdmissionError as error:
            return error.code
        return "ADMITTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(enter, ("work", "inventory")))
    assert sorted(results) == ["ADMITTED", "internal_io_busy"]
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 1


@pytest.mark.usefixtures("internal_io_budget")
def test_lowering_preserves_live_work_and_closed_receipts_release_exact_capacity(
    ingest: IngestFixture,
) -> None:
    budget = PostgresInternalIoBudget(ingest.sessions)
    budget.set_limit(uuid4(), 2, 2)
    work, maintenance = (
        PostgresIngestExecutionRepository(ingest.sessions),
        PostgresProviderMaintenanceRepository(ingest.sessions),
    )
    ticket, scanner = ticket_for(ingest), inventory_ticket()
    work.prepare(ticket)
    work.start(ticket, IDENTITY)
    maintenance.prepare(scanner)
    budget.set_limit(uuid4(), 3, 1)
    assert work.renew(ticket, IDENTITY).state == ExecutionState.RUNNING
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 2
    maintenance.confirm(scanner, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(ResourceAdmissionError, match="internal_io_busy"):
        maintenance.prepare(inventory_ticket())
    work.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY))
    assert maintenance.prepare(inventory_ticket()).state == ExecutionState.PREPARED


@pytest.mark.usefixtures("internal_io_budget")
def test_cleanup_charges_separate_capacity_and_epoch_change_cannot_release_it(
    ingest: IngestFixture,
) -> None:
    PostgresInternalIoBudget(ingest.sessions).set_limit(uuid4(), 2, 1)
    _, claim = finalized(ingest)
    cleanup = PostgresIngestCleanupRepository(ingest.sessions)
    ticket = cleanup_ticket(claim)
    cleanup.prepare(ticket)
    with pytest.raises(ResourceAdmissionError, match="internal_io_busy"):
        PostgresProviderMaintenanceRepository(ingest.sessions).prepare(inventory_ticket())
    with ingest.sessions.begin() as session:
        identity = present(session.scalar(select(ServerInstanceRow)))
        identity.identity_epoch += 1
    cleanup.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(ResourceAdmissionError, match="internal_io_budget_mismatch"):
        PostgresProviderMaintenanceRepository(ingest.sessions).prepare(inventory_ticket())
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_sql_guard_cannot_bypass_python_count(
    ingest: IngestFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    PostgresInternalIoBudget(ingest.sessions).set_limit(uuid4(), 2, 1)
    PostgresProviderMaintenanceRepository(ingest.sessions).prepare(inventory_ticket())
    monkeypatch.setattr(
        "autplay.adapters.postgresql.ingest_execution.require_internal_io_capacity",
        lambda session: None,
    )
    with pytest.raises(DBAPIError, match="internal_io_busy"):
        PostgresIngestExecutionRepository(ingest.sessions).prepare(ticket_for(ingest))
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 1


@pytest.mark.usefixtures("internal_io_budget")
def test_downgrade_cannot_discard_reviewed_budget(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with pytest.raises(DBAPIError, match="Refusing to discard internal I/O budget"):
        database_harness.downgrade(database_name, "0048_ingest_cleanup")


@pytest.mark.usefixtures("internal_io_budget")
@pytest.mark.skipif(
    os.name == "nt", reason="real pinned media executables are in the Linux proof image"
)
def test_actual_cpu_worker_once_uses_contained_work_and_cleanup(
    ingest: IngestFixture, tmp_path: Path, database_url: str
) -> None:
    source = io.BytesIO()
    with wave.open(source, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(16000 * math.sin(index * 440 * 2 * math.pi / 22050)))
                for index in range(22050 * 12)
            )
        )
    payload = source.getvalue()
    with ingest.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, ingest.upload_id))
        upload.expected_size = upload.received_size = upload.chunk_size = len(payload)
        upload.declared_sha256 = hashlib.sha256(payload).digest()
        key = upload.staging_key
        PostgresJobRepository(session).fail_retryable(
            ingest.lease.fence,
            JobError("test.prepare_worker", {}),
            RetryPolicy(base_delay=timedelta(microseconds=1)),
        )
    (tmp_path / "staging" / key).write_bytes(payload)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        AUTPLAY_DATABASE_URL=database_url,
        AUTPLAY_PROFILE="test",
        AUTPLAY_VAULT_ROOT=str(tmp_path),
        AUTPLAY_WORKER_CGROUP_ROOT=os.environ["AUTPLAY_TEST_CGROUP_ROOT"],
    )
    result = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.worker_cpu", "--once"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with ingest.sessions() as session:
        claim = present(session.get(IngestCleanupClaimRow, ingest.upload_id))
        assert claim.completed_at is not None
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "COMMITTED"
        assert internal_io_usage(session) == 0
    assert not (tmp_path / "staging" / key).exists()


@pytest.mark.usefixtures("internal_io_budget")
def test_uncertain_maintenance_prepare_absence_waits_for_commit(ingest: IngestFixture) -> None:
    repository = PostgresProviderMaintenanceRepository(ingest.sessions)
    ticket = inventory_ticket()
    with ingest.sessions() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.add(
            ProviderMaintenanceRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                claim_id=ticket.claim_id,
                action="INVENTORY",
                singleton_id=1,
                state="PREPARED",
                created_at=writer.scalar(select(func.clock_timestamp())),
            )
        )
        writer.flush()
        pid = present(writer.scalar(select(func.pg_backend_pid())))
        assert repository.status(ticket) is None
        future = pool.submit(repository.reconcile, ticket)
        try:
            with ingest.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert monotonic() < until
                    sleep(0.01)
            assert not future.done()
            writer.commit()
        finally:
            writer.rollback()
        assert present(future.result(timeout=5)).state == ExecutionState.PREPARED


def worker_settings(database_url: str, root: Path) -> WorkerSettings:
    delegation = os.environ.get("AUTPLAY_TEST_CGROUP_ROOT")
    return WorkerSettings(
        database_url=SecretStr(database_url),
        vault_root=root,
        worker_cgroup_root=Path(delegation) if delegation else None,
    )


@pytest.mark.usefixtures("internal_io_budget")
def test_poisoned_cleanup_does_not_prevent_job_poll(
    ingest: IngestFixture,
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, claim = finalized(ingest)
    # Lose the published bytes: cleanup must leave its canonical intent pending.
    digest = claim.expected.sha256.hex
    (tmp_path / "objects" / digest[:2] / digest[2:4] / digest).unlink()
    runtime = IngestWorkerRuntime(worker_settings(database_url, tmp_path), ingest.sessions)
    stop, polled = Event(), Event()

    def poll(worker: JobWorker) -> WorkerTick:
        del worker
        polled.set()
        stop.set()
        return WorkerTick(WorkerOutcome.IDLE, 0)

    monkeypatch.setattr(JobWorker, "run_once", poll)
    worker = worker_cpu.build_cpu_worker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(ingest.sessions),
        ids=Uuid7Generator(),
    )
    try:
        worker_cpu.run_cpu_worker(worker, stop, ingest_cleanup=runtime.drain_cleanup)
        assert polled.is_set()
        assert (
            present(runtime.cleanup_repository.get(ingest.upload_id)).completed_execution_id is None
        )
        assert runtime.drain_cleanup() == 0  # End-of-page rotation is still reachable.
        with ingest.sessions() as session:
            assert internal_io_usage(session) == 0
    finally:
        assert not runtime.shutdown()


@pytest.mark.usefixtures("internal_io_budget")
def test_actual_once_signal_interrupts_owned_runtime(
    ingest: IngestFixture,
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = Event()
    original = IngestWorkerRuntime.request_stop
    original_shutdown = IngestWorkerRuntime.shutdown

    def request_stop(runtime: IngestWorkerRuntime) -> None:
        original(runtime)
        stopped.set()

    def poll(worker: JobWorker) -> WorkerTick:
        del worker
        signal.raise_signal(signal.SIGTERM)
        assert stopped.wait(2), "main thread waiter prevented independent signal handling"
        return WorkerTick(WorkerOutcome.IDLE, 0)

    def shutdown(runtime: IngestWorkerRuntime, *, timeout: float = 5) -> tuple[UUID, ...]:
        signal.raise_signal(signal.SIGTERM)
        return original_shutdown(runtime, timeout=timeout)

    def outside_scope(signum: int, frame: object) -> None:
        del signum, frame
        pytest.fail("signal handler restored before retained drain")

    monkeypatch.setattr(
        worker_cpu, "load_worker_settings", lambda: worker_settings(database_url, tmp_path)
    )
    monkeypatch.setattr(IngestWorkerRuntime, "request_stop", request_stop)
    monkeypatch.setattr(IngestWorkerRuntime, "shutdown", shutdown)
    monkeypatch.setattr(JobWorker, "run_once", poll)
    previous = signal.signal(signal.SIGTERM, outside_scope)
    try:
        assert worker_cpu.main(["--once"]) == 0
        assert signal.getsignal(signal.SIGTERM) is outside_scope
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_actual_worker_refuses_unconfigured_capacity(
    ingest: IngestFixture,
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_cpu, "load_worker_settings", lambda: worker_settings(database_url, tmp_path)
    )
    assert worker_cpu.main(["--once"]) == 3
    with ingest.sessions() as session:
        assert internal_io_usage(session) == 0
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "SEALED"


@pytest.mark.parametrize("action", ["apply", "limit"])
def test_audit_failure_rolls_back_internal_policy(
    policy: PolicyHarness,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    policy.admissions.budget(playbacks=2, transfers=2)
    repository = PostgresInternalIoBudget(policy.sessions)
    command = measured(policy)
    if action == "limit":
        repository.initialize(command)
    operation = command.operation_id if action == "apply" else uuid4()

    def fail(session: Session, *args: object) -> dict[str, object]:
        session.flush()  # Policy SQL has executed, but the audit cannot be persisted.
        session.execute(text("SELECT 1/0"))
        raise AssertionError("database division unexpectedly succeeded")

    monkeypatch.setattr(repository, "_record", fail)
    with pytest.raises(DBAPIError):
        if action == "apply":
            repository.initialize(command)
        else:
            repository.set_limit(operation, 2, 1)
    with policy.sessions() as session:
        current = present(session.get(InternalIoPolicyRow, 1))
        assert (current.revision, current.active_limit) == (
            (1, None) if action == "apply" else (2, 2)
        )
        assert session.get(AuditEventRow, operation) is None
