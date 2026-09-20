"""Actual scanner processes keep singleton ownership across pages, stop and DB faults."""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import pytest
from autplay.adapters.child_process import vault_inventory_child_launch
from autplay.adapters.filesystem.inventory_protocol import decode_page
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_inventory import FilesystemVaultInventoryCursor
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.postgresql.models import ProviderMaintenanceRow
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.application.vault_inventory import InventoryEntry, InventoryPage
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ProcessExitEvidence, ProcessIdentity
from autplay.runtime import vault_inventory as inventory_runtime
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from autplay.runtime.vault_inventory import ProcessVaultInventoryCursor
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from test_vault_inventory import populate

from .conftest import DatabaseHarness
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


def new_ticket() -> MaintenanceTicket:
    execution_id = uuid4()
    return MaintenanceTicket(execution_id, uuid4(), None, execution_id, MaintenanceAction.INVENTORY)


def test_actual_pages_keep_singleton_and_never_scan_in_parent(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = populate(tmp_path, 137)

    def forbidden(_self: FilesystemVaultInventoryCursor, *, maximum: int) -> InventoryPage:
        pytest.fail("parent touched filesystem inventory")

    monkeypatch.setattr(FilesystemVaultInventoryCursor, "next_page", forbidden)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path)
    successor = ProcessVaultInventoryCursor(repository, tmp_path)
    try:
        first = cursor.next_page(maximum=10)
        assert not first.exhausted and cursor.pending()
        with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
            successor.next_page(maximum=1)
        observed: list[InventoryEntry] = list(first.entries)
        for _ in range(30):
            page = cursor.next_page(maximum=100)
            observed.extend(page.entries)
            if page.exhausted:
                break
        else:
            pytest.fail("scanner did not finish")
        assert set(observed) == expected and len(observed) == len(expected)
        assert not cursor.pending()
        assert cursor.next_page() == InventoryPage((), 0, True)
        with admission.sessions() as session:
            runs = list(session.scalars(select(ProviderMaintenanceRow)))
            assert len(runs) == 1
            run = runs[0]
            assert run.action == "INVENTORY" and run.claim_id == run.execution_id
            assert run.state == "CLOSED" and run.exit_code == 0 and run.child_pid is not None
            assert run.closure_kind == "PROCESS_EXIT"
    finally:
        assert not cursor.shutdown()
        assert not successor.shutdown()


def test_idle_stop_without_actual_exit_cannot_free_reservation(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(tmp_path, 3)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path)
    try:
        assert not cursor.next_page(maximum=1).exhausted
        execution = cursor.pending()[0]
        with monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "kill", lambda _process: None)
            assert cursor.shutdown(timeout=0) == (execution,)
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(new_ticket())
            with admission.sessions() as session:
                assert present(session.get(ProviderMaintenanceRow, execution)).closed_at is None
        assert not cursor.shutdown()
        assert repository.prepare(new_ticket()).state == "PREPARED"
    finally:
        assert not cursor.shutdown()


def test_idle_deadline_stops_child_while_status_is_blocked_and_late_reply_cannot_resume(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(tmp_path, 3)
    offset = 0.0
    monkeypatch.setattr(
        inventory_runtime,
        "ResourceIoDeadline",
        lambda started: ResourceIoDeadline(started, clock=lambda: monotonic() + offset),
    )
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original_status, original_kill = repository.status, subprocess.Popen.kill
    entered, release, stopped = Event(), Event(), Event()
    armed = False

    def status(ticket: MaintenanceTicket) -> MaintenanceStatus | None:
        result = original_status(ticket)
        if armed and not release.is_set():
            entered.set()
            assert release.wait(5)
        return result

    def kill(process: subprocess.Popen[bytes]) -> None:
        original_kill(process)
        stopped.set()

    monkeypatch.setattr(repository, "status", status)
    monkeypatch.setattr(subprocess.Popen, "kill", kill)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path)
    try:
        assert not cursor.next_page(maximum=1).exhausted
        armed = True
        # No page caller exists here: the idle renewal itself must be supervised.
        assert entered.wait(3)
        offset = 10
        assert stopped.wait(2)
        assert cursor.pending()
        with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
            repository.prepare(new_ticket())
        release.set()
        assert not cursor.shutdown()
        with pytest.raises(ResourceAdmissionError):
            cursor.next_page(maximum=1)
    finally:
        release.set()
        assert not cursor.shutdown()


def test_terminal_page_waits_for_exact_exit_ack_and_reconciles_lost_reply(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FilesystemVaultStorage(tmp_path)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original = repository.confirm
    entered, release = Event(), Event()
    calls = 0

    def confirm(ticket: MaintenanceTicket, proof: ProcessExitEvidence) -> MaintenanceStatus:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(3)
        result = original(ticket, proof)
        if calls == 1:
            raise SQLAlchemyError("synthetic lost close response")
        return result

    monkeypatch.setattr(repository, "confirm", confirm)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cursor.next_page)
            assert entered.wait(3) and not future.done() and cursor.pending()
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(new_ticket())
            release.set()
            assert future.result(timeout=3).exhausted
        assert calls == 2 and not cursor.pending()
    finally:
        release.set()
        assert not cursor.shutdown()


@pytest.mark.parametrize("boundary", ["prepare", "start"])
def test_committed_launch_reply_loss_never_enumerates(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original_prepare, original_start = repository.prepare, repository.start

    def prepare(ticket: MaintenanceTicket) -> MaintenanceStatus:
        original_prepare(ticket)
        raise SQLAlchemyError("synthetic lost prepare response")

    def start(ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        original_start(ticket, child)
        raise SQLAlchemyError("synthetic lost start response")

    monkeypatch.setattr(repository, boundary, prepare if boundary == "prepare" else start)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path / "absent")
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_unavailable"):
            cursor.next_page()
        assert not cursor.pending() and not (tmp_path / "absent").exists()
        with admission.sessions() as session:
            run = present(session.scalar(select(ProviderMaintenanceRow)))
            assert run.state == "CLOSED"
            assert (run.closure_kind == "NOT_STARTED") == (boundary == "prepare")
    finally:
        assert not cursor.shutdown()


@pytest.mark.parametrize("thread_name", ["vault-inventory", "vault-inventory-stop"])
def test_partial_thread_start_failure_never_prepares_or_spawns(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_name: str
) -> None:
    original = threading.Thread.start
    started: list[threading.Thread] = []

    def start(thread: threading.Thread) -> None:
        original(thread)
        started.append(thread)
        if thread.name == thread_name:
            raise RuntimeError("synthetic failure after starting a launch-gated thread")

    monkeypatch.setattr(threading.Thread, "start", start)
    cursor = ProcessVaultInventoryCursor(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_unavailable"):
            cursor.next_page()
        for thread in started:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert not cursor.pending()
        with admission.sessions() as session:
            assert session.scalar(select(ProviderMaintenanceRow)) is None
    finally:
        assert not cursor.shutdown()


def test_inventory_identity_and_downgrade_cannot_discard_ownership(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    ticket = new_ticket()
    repository.prepare(ticket)
    with pytest.raises(ValueError, match="maintenance_target_invalid"):
        replace(ticket, claim_id=uuid4())
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        present(session.get(ProviderMaintenanceRow, ticket.execution_id)).owner_run_id = uuid4()
        session.flush()
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0043_orphan_object_claim")


@pytest.mark.parametrize("boundary", ["pipe", "after_last_page"])
def test_blocked_page_or_last_reply_does_not_release_capacity_or_allow_second_caller(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    script = """
import os, sys, time
from uuid import uuid4
from autplay.adapters.filesystem.vault_child import (
    read_frame, write_frame, encode_document, decode_document,
)
source, destination = sys.stdin.buffer, sys.stdout.buffer
write_frame(destination, b'H', encode_document({'pid':os.getpid(), 'nonce':uuid4().hex}))
_, payload = read_frame(source)
command = decode_document(payload)
identity = {'action':'INVENTORY', 'claim_id':command['claim_id']}
write_frame(destination, b'R', encode_document(identity))
_, payload = read_frame(source)
request = decode_document(payload)
"""
    if boundary == "after_last_page":
        script += """
write_frame(destination, b'R', encode_document({**identity,
    'sequence':request['sequence'], 'entries':[], 'work_units':1, 'exhausted':True}))
"""
    script += "while True: time.sleep(1)\n"

    def launch() -> tuple[list[str], dict[str, str]]:
        arguments, environment = vault_inventory_child_launch()
        return [*arguments[:-2], "-c", script], environment

    entered = Event()
    original = RetainedVaultProcess.inventory_page

    def page(
        child: RetainedVaultProcess[MaintenanceTicket], document: dict[str, object]
    ) -> tuple[bytes, bytes]:
        entered.set()
        return original(child, document)

    monkeypatch.setattr(RetainedVaultProcess, "inventory_page", page)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    cursor = ProcessVaultInventoryCursor(repository, tmp_path, launch=launch)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "kill", lambda _process: None)
            future = pool.submit(cursor.next_page)
            assert entered.wait(3)
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                cursor.next_page(maximum=1)
            with pytest.raises(ResourceAdmissionError, match="maintenance_deadline_expired"):
                future.result(timeout=6)
            assert cursor.pending()
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(new_ticket())
            with admission.sessions() as session:
                run = present(session.scalar(select(ProviderMaintenanceRow)))
                assert run.closed_at is None and run.child_pid is not None
    finally:
        assert not cursor.shutdown()


def test_close_while_decoding_page_never_delivers_late_success(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(tmp_path, 1)
    original = decode_page
    entered, release = Event(), Event()

    def decode(
        document: dict[str, object], *, claim_id: UUID, sequence: int, maximum: int
    ) -> InventoryPage:
        page = original(document, claim_id=claim_id, sequence=sequence, maximum=maximum)
        entered.set()
        assert release.wait(3)
        return page

    monkeypatch.setattr(inventory_runtime, "decode_page", decode)
    cursor = ProcessVaultInventoryCursor(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cursor.next_page, maximum=1)
            assert entered.wait(3)
            assert cursor.shutdown(timeout=0)
            release.set()
            with pytest.raises(ResourceAdmissionError, match="maintenance_closed"):
                future.result(timeout=3)
    finally:
        release.set()
        assert not cursor.shutdown()


pytestmark = pytest.mark.usefixtures("internal_io_budget")
