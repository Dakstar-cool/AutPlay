"""Actual maintenance children retain durable singleton capacity through failure."""

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.models import ProviderStagingRow
from autplay.adapters.postgresql.models.provider_maintenance import ProviderMaintenanceRow
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.provider_scratch import PostgresProviderScratchRepository
from autplay.adapters.postgresql.resource_limits import RESOURCE_ADMISSION_LOCK
from autplay.application.provider_cleanup import ProviderCleanupService
from autplay.application.provider_scratch import ProviderScratchService
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.runtime import provider_maintenance as maintenance_runtime
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from .conftest import DatabaseHarness
from .test_provider_scratch import handed_off
from .test_provider_staging import writer
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
@pytest.mark.usefixtures("internal_io_budget")
def test_offline_restore_drain_closes_absent_maintenance_process(
    admission: AdmissionHarness,
) -> None:
    execution_id = uuid4()
    ticket = MaintenanceTicket(
        execution_id,
        uuid4(),
        None,
        execution_id,
        MaintenanceAction.INVENTORY,
    )
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    repository.prepare(ticket)
    repository.start(ticket, ProcessIdentity(4_000_000_000, b"o" * 32))

    report = PostgresOfflineExecutionDrain(
        admission.sessions,
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()

    assert report.maintenance_executions == report.checked_pids == 1
    status = present(repository.status(ticket))
    assert status.state.value == "CLOSED"
    with admission.sessions() as session:
        row = present(session.get(ProviderMaintenanceRow, execution_id))
        assert row.closure_kind == "SUPERVISOR_EXIT" and row.exit_code == 137


@pytest.mark.parametrize("provider", ["internet", "discovery"])
def test_actual_scratch_child_finishes_before_item_completion(
    admission: AdmissionHarness, tmp_path: Path, provider: str
) -> None:
    owned = handed_off(admission, tmp_path, provider)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    try:
        service = ProviderScratchService(
            PostgresProviderScratchRepository(admission.sessions), storage
        )
        assert service.retire(owned.execution_id)
        assert service.retire(owned.execution_id)
        with admission.sessions() as session:
            runs = list(session.scalars(select(ProviderMaintenanceRow)))
            assert len(runs) == 1
            run = runs[0]
            item = present(session.get(ProviderStagingRow, owned.execution_id))
            assert run.state == "CLOSED" and run.exit_code == 0
            assert run.closure_kind == "PROCESS_EXIT" and run.child_pid is not None
            assert run.child_identity_sha256 is not None
            assert present(item.scratch_retired_at) >= present(run.closed_at)
        assert owned.staged.exists() and not owned.workspace.exists()
        assert not storage.pending()
    finally:
        assert not storage.shutdown()


def test_actual_abandoned_child_preserves_staging_and_scratch(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    owned = writer(admission)
    admission.service.confirm_execution_exit(owned.ticket, replace(owned.proof, exit_code=1))
    admission.service.close_io(owned.ticket.permit)
    FilesystemVaultStorage(tmp_path).create_staging(owned.key)
    workspace = FilesystemProviderStorage(tmp_path).create_workspace(owned.ticket.execution_id)
    (workspace / "partial").write_bytes(b"unfinished")
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    cleanup = PostgresProviderCleanupRepository(admission.sessions)
    try:
        assert ProviderCleanupService(cleanup, storage).cleanup(owned.ticket.execution_id)
        claim = present(cleanup.claim(owned.ticket.execution_id))
        assert claim.completed
        assert (tmp_path / "quarantine" / claim.quarantine_key.value).exists()
        assert (
            tmp_path / "provider-retired" / claim.claim_id.hex / "partial"
        ).read_bytes() == b"unfinished"
    finally:
        assert not storage.shutdown()


def test_prepared_orphan_blocks_successor_without_expiry_or_shared_process(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    owned = handed_off(admission, tmp_path)
    claim = present(PostgresProviderScratchRepository(admission.sessions).claim(owned.execution_id))
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), owned.execution_id, claim.claim_id, MaintenanceAction.SCRATCH
    )
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    assert repository.prepare(ticket) == repository.prepare(ticket)
    successor = replace(ticket, execution_id=uuid4(), owner_run_id=uuid4())
    with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
        PostgresProviderMaintenanceRepository(admission.sessions).prepare(successor)
    repository.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    assert repository.prepare(successor).ticket == successor


def test_committed_start_and_lost_close_reply_reconcile_exact_handle(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original = repository.confirm
    calls = 0

    def confirm(ticket: MaintenanceTicket, proof: ProcessExitEvidence) -> MaintenanceStatus:
        nonlocal calls
        calls += 1
        result = original(ticket, proof)
        if calls == 1:
            raise SQLAlchemyError("synthetic lost committed reply")
        return result

    monkeypatch.setattr(repository, "confirm", confirm)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    try:
        assert ProviderScratchService(
            PostgresProviderScratchRepository(admission.sessions), storage
        ).retire(owned.execution_id)
        assert calls == 2 and not storage.pending()
    finally:
        assert not storage.shutdown()


def test_stop_without_process_exit_keeps_global_slot_and_item_unfinished(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original = repository.start
    entered, release = Event(), Event()
    tickets: list[MaintenanceTicket] = []

    def delayed(ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        result = original(ticket, child)
        with admission.sessions.begin() as session:
            assert session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RESOURCE_ADMISSION_LOCK}
            )
        tickets.append(ticket)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(repository, "start", delayed)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    service = ProviderScratchService(PostgresProviderScratchRepository(admission.sessions), storage)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "kill", lambda _process: None)
            future = pool.submit(service.retire, owned.execution_id)
            assert entered.wait(5)
            assert storage.shutdown(timeout=0) == (tickets[0].execution_id,)
            release.set()
            with pytest.raises(ResourceAdmissionError, match="maintenance_deadline_expired"):
                future.result(timeout=2)
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(replace(tickets[0], execution_id=uuid4(), owner_run_id=uuid4()))
            with admission.sessions() as session:
                assert (
                    present(session.get(ProviderMaintenanceRow, tickets[0].execution_id)).closed_at
                    is None
                )
                assert (
                    present(session.get(ProviderStagingRow, owned.execution_id)).scratch_retired_at
                    is None
                )
            assert owned.workspace.exists()
    finally:
        release.set()
        assert not storage.shutdown()


def test_child_failure_releases_only_run_and_retry_preserves_item_claim(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    owned = handed_off(admission, tmp_path)
    displaced = tmp_path / "moved-for-test"
    owned.workspace.rename(displaced)
    repository = PostgresProviderScratchRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    service = ProviderScratchService(repository, storage)
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_storage_failed"):
            service.retire(owned.execution_id)
        first = present(repository.claim(owned.execution_id))
        assert not first.completed and not storage.pending()
        displaced.rename(owned.workspace)
        assert service.retire(owned.execution_id)
        assert present(repository.claim(owned.execution_id)).claim_id == first.claim_id
    finally:
        assert not storage.shutdown()


def test_run_identity_closure_and_downgrade_are_guarded(
    admission: AdmissionHarness,
    tmp_path: Path,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    owned = handed_off(admission, tmp_path)
    claim = present(PostgresProviderScratchRepository(admission.sessions).claim(owned.execution_id))
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), owned.execution_id, claim.claim_id, MaintenanceAction.SCRATCH
    )
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    repository.prepare(ticket)
    child = ProcessIdentity(12345, b"i" * 32)
    repository.start(ticket, child)
    with pytest.raises(ResourceAdmissionError):
        repository.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(ResourceAdmissionError):
        repository.start(replace(ticket, owner_run_id=uuid4()), child)
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        present(session.get(ProviderMaintenanceRow, ticket.execution_id)).claim_id = uuid4()
        session.flush()
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0041_provider_scratch")
    proof = ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 0, child)
    assert repository.confirm(ticket, proof) == repository.confirm(ticket, proof)
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        session.delete(present(session.get(ProviderMaintenanceRow, ticket.execution_id)))
        session.flush()


@pytest.mark.parametrize("boundary", ["prepare", "start"])
def test_lost_committed_launch_response_never_sends_go_and_can_retry(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    prepare, start = repository.prepare, repository.start
    failed = False

    def lost_prepare(ticket: MaintenanceTicket) -> MaintenanceStatus:
        nonlocal failed
        result = prepare(ticket)
        if not failed:
            failed = True
            raise SQLAlchemyError("synthetic lost prepare response")
        return result

    def lost_start(ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        nonlocal failed
        result = start(ticket, child)
        if not failed:
            failed = True
            raise SQLAlchemyError("synthetic lost start response")
        return result

    monkeypatch.setattr(repository, boundary, lost_prepare if boundary == "prepare" else lost_start)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    scratch = PostgresProviderScratchRepository(admission.sessions)
    service = ProviderScratchService(scratch, storage)
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_unavailable"):
            service.retire(owned.execution_id)
        assert owned.workspace.exists() and not present(scratch.claim(owned.execution_id)).completed
        with admission.sessions() as session:
            run = present(session.scalar(select(ProviderMaintenanceRow)))
            assert run.state == "CLOSED"
            assert (run.closure_kind == "NOT_STARTED") == (boundary == "prepare")
        assert service.retire(owned.execution_id)
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("started", [False, True])
def test_thread_start_failure_cannot_begin_database_or_process_work(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: bool
) -> None:
    owned = handed_off(admission, tmp_path)
    original = threading.Thread.start
    threads: list[threading.Thread] = []

    def failed(thread: threading.Thread) -> None:
        if thread.name != "vault-maintenance":
            original(thread)
            return
        if started:
            original(thread)
            threads.append(thread)
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(threading.Thread, "start", failed)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_unavailable"):
            ProviderScratchService(
                PostgresProviderScratchRepository(admission.sessions), storage
            ).retire(owned.execution_id)
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert not storage.pending() and owned.workspace.exists()
        with admission.sessions() as session:
            assert session.scalar(select(ProviderMaintenanceRow)) is None
    finally:
        assert not storage.shutdown()


def test_interrupted_caller_requests_stop_and_keeps_live_child_charged(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    entered, release = Event(), Event()
    original_start = repository.start
    original_retained = maintenance_runtime._Retained
    tickets: list[MaintenanceTicket] = []
    stops: list[int] = []

    class InterruptedWait(Event):
        interrupted = False

        def wait(self, timeout: float | None = None) -> bool:
            if not self.interrupted:
                self.interrupted = True
                assert entered.wait(5)
                raise KeyboardInterrupt()
            return super().wait(timeout)

    def retain(child: RetainedVaultProcess[MaintenanceTicket]) -> maintenance_runtime._Retained:
        return original_retained(child, done=InterruptedWait())

    def delayed_start(ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        result = original_start(ticket, child)
        tickets.append(ticket)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(maintenance_runtime, "_Retained", retain)
    monkeypatch.setattr(repository, "start", delayed_start)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "kill", lambda process: stops.append(process.pid))
            with pytest.raises(KeyboardInterrupt):
                ProviderScratchService(
                    PostgresProviderScratchRepository(admission.sessions), storage
                ).retire(owned.execution_id)
            assert stops and storage.pending() == (tickets[0].execution_id,)
            with admission.sessions() as session:
                row = present(session.get(ProviderMaintenanceRow, tickets[0].execution_id))
                assert row.state == "RUNNING" and row.child_pid == stops[0]
                assert row.closed_at is None
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(replace(tickets[0], execution_id=uuid4(), owner_run_id=uuid4()))
            assert owned.workspace.exists()
            release.set()
    finally:
        release.set()
        assert not storage.shutdown()


pytestmark = pytest.mark.usefixtures("internal_io_budget")
