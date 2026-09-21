"""Retain a singleton scanner, its cursor and exact process handle across pages."""

import math
import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from autplay.adapters.child_process import vault_inventory_child_launch
from autplay.adapters.filesystem.inventory_protocol import (
    MAX_INVENTORY_REPLY_BYTES,
    decode_page,
    page_command,
)
from autplay.adapters.filesystem.maintenance_protocol import result_identity
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import ChildLaunch, RetainedVaultProcess
from autplay.application.vault_inventory import MAX_INVENTORY_WORK, InventoryPage
from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ProcessIdentity
from autplay.runtime.provider_maintenance import MaintenanceRepository
from autplay.runtime.resource_io_deadline import ResourceIoDeadline


@dataclass
class _Request:
    maximum: int
    done: threading.Event = field(default_factory=threading.Event)
    page: InventoryPage | None = None
    error: ResourceAdmissionError | None = None


@dataclass
class _Scanner:
    child: RetainedVaultProcess[MaintenanceTicket]
    requests: queue.Queue[_Request] = field(default_factory=lambda: queue.Queue(maxsize=1))
    done: threading.Event = field(default_factory=threading.Event)
    launch_gate: threading.Event = field(default_factory=threading.Event)
    launch_permitted: bool = False


class ProcessVaultInventoryCursor:
    """One page caller and one pipe owner; capacity also stays charged while idle.

    The supervisor can stop a child while its sole worker is blocked in a pipe or
    database call. It never closes that worker's pipes or clears its reservation.
    A restart cannot adopt or expire this instance's durable ownership.
    """

    def __init__(
        self,
        repository: MaintenanceRepository,
        root: Path,
        *,
        launch: ChildLaunch = vault_inventory_child_launch,
    ) -> None:
        self._repository = repository
        self._root = Path(os.path.abspath(root))
        self._launch = launch
        self._owner = uuid4()
        self._lock = threading.Lock()
        self._scanner: _Scanner | None = None
        self._busy = False
        self._closing = False
        self._exhausted = False
        self._error: ResourceAdmissionError | None = None

    def next_page(self, *, maximum: int = MAX_INVENTORY_WORK) -> InventoryPage:
        if type(maximum) is not int or not 1 <= maximum <= MAX_INVENTORY_WORK:
            raise ValueError("vault_inventory_limit_invalid")
        until = monotonic() + 5
        request = _Request(maximum)
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._closing:
                raise ResourceAdmissionError("maintenance_closed")
            if self._busy:
                raise ResourceAdmissionError("maintenance_busy")
            if self._exhausted:
                return InventoryPage((), 0, True)
            self._busy = True
            scanner = self._scanner
            fresh = scanner is None
            if scanner is None:
                execution_id = uuid4()
                ticket = MaintenanceTicket(
                    execution_id, self._owner, None, execution_id, MaintenanceAction.INVENTORY
                )
                scanner = _Scanner(
                    RetainedVaultProcess(
                        ticket, ResourceIoDeadline(monotonic()), launch=self._launch
                    )
                )
                self._scanner = scanner
            scanner.requests.put_nowait(request)
        try:
            if fresh:
                self._start(scanner)
            while not request.done.wait(max(0.0, min(0.05, until - monotonic()))):
                if monotonic() >= until:
                    raise ResourceAdmissionError("maintenance_deadline_expired")
            if monotonic() >= until:
                raise ResourceAdmissionError("maintenance_deadline_expired")
            if request.error is not None:
                raise request.error
            if request.page is None:
                raise ResourceAdmissionError("maintenance_storage_failed")
            with self._lock:
                if self._closing:
                    raise ResourceAdmissionError("maintenance_closed")
                if not request.page.exhausted:
                    scanner.child.deadline.check()
                return request.page
        except BaseException:
            with self._lock:
                self._closing = True
            scanner.child.request_stop()
            raise
        finally:
            with self._lock:
                self._busy = False

    def pending(self) -> tuple[UUID, ...]:
        with self._lock:
            scanner = self._scanner
            return () if scanner is None else (scanner.child.ticket.execution_id,)

    def close(self) -> None:
        self.shutdown()

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError("maintenance_shutdown_invalid")
        with self._lock:
            self._closing = True
            scanner = self._scanner
        if scanner is not None:
            scanner.child.request_stop()
            scanner.done.wait(timeout)
        return self.pending()

    def _start(self, scanner: _Scanner) -> None:
        try:
            threading.Thread(
                target=self._work, args=(scanner,), name="vault-inventory", daemon=True
            ).start()
            threading.Thread(
                target=self._supervise, args=(scanner,), name="vault-inventory-stop", daemon=True
            ).start()
            scanner.launch_permitted = True
        except BaseException:
            scanner.child.seal_without_spawn()
            self._forget(scanner)
            raise ResourceAdmissionError("maintenance_unavailable") from None
        finally:
            scanner.launch_gate.set()

    @staticmethod
    def _supervise(scanner: _Scanner) -> None:
        scanner.launch_gate.wait()
        if not scanner.launch_permitted:
            return
        while not scanner.done.wait(0.05):
            if scanner.child.deadline.stopped():
                scanner.child.request_stop()
                return

    def _renew(self, scanner: _Scanner, identity: ProcessIdentity) -> None:
        child = scanner.child
        sequence = child.deadline.begin_renewal()
        status = self._repository.status(child.ticket)
        valid = (
            status is not None
            and status.ticket == child.ticket
            and status.state == ExecutionState.RUNNING
            and status.child == identity
        )
        if not child.deadline.finish_renewal(sequence, succeeded=valid):
            raise ResourceAdmissionError("maintenance_execution_stale")

    def _work(self, scanner: _Scanner) -> None:
        scanner.launch_gate.wait()
        if not scanner.launch_permitted:
            return
        child = scanner.child
        request: _Request | None = None
        terminal_page: InventoryPage | None = None
        error: ResourceAdmissionError | None = None
        try:
            prepared = self._repository.prepare(child.ticket)
            if prepared.ticket != child.ticket or prepared.state != ExecutionState.PREPARED:
                raise ResourceAdmissionError("maintenance_execution_stale")
            identity = child.spawn()
            child.allow_go(self._repository.start(child.ticket, identity))
            child.go({"version": 1, "root": str(self._root), **result_identity(child.ticket)})
            tag, payload = child.read_result()
            if tag != b"R" or decode_document(payload) != result_identity(child.ticket):
                raise ResourceAdmissionError("maintenance_storage_failed")
            sequence, renewed_at = 0, monotonic()
            while True:
                child.deadline.check()
                if monotonic() - renewed_at >= 1:
                    self._renew(scanner, identity)
                    renewed_at = monotonic()
                try:
                    request = scanner.requests.get(timeout=min(0.05, child.deadline.remaining()))
                except queue.Empty:
                    continue
                # Validate ownership anew before every page, including fast loops.
                self._renew(scanner, identity)
                renewed_at = monotonic()
                sequence += 1
                tag, payload = child.inventory_page(page_command(sequence, request.maximum))
                if tag != b"R":
                    raise ResourceAdmissionError("maintenance_storage_failed")
                page = decode_page(
                    decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES),
                    claim_id=child.ticket.claim_id,
                    sequence=sequence,
                    maximum=request.maximum,
                )
                if page.exhausted:
                    while (proof := child.exit_evidence(identity)) is None:
                        child.deadline.check()
                        sleep(0.01)
                    if proof.exit_code != 0:
                        raise ResourceAdmissionError("maintenance_storage_failed")
                    terminal_page = page
                    break
                with self._lock:
                    if self._closing:
                        raise ResourceAdmissionError("maintenance_closed")
                    child.deadline.check()
                    request.page = page
                    request.done.set()
                request = None
        except ResourceAdmissionError as failure:
            error = failure
        except SQLAlchemyError, OSError, ValueError, RuntimeError:
            error = ResourceAdmissionError("maintenance_unavailable")
        except BaseException:
            error = ResourceAdmissionError("maintenance_unavailable")
        finally:
            if terminal_page is None:
                child.deadline.freeze_error()
                child.seal_without_spawn()
                child.request_stop()
                with self._lock:
                    self._error = error or ResourceAdmissionError("maintenance_closed")
            self._drain(scanner)
            if terminal_page is not None:
                with self._lock:
                    self._exhausted = True
            if request is not None:
                request.page, request.error = terminal_page, error
                request.done.set()
            while True:
                try:
                    waiting = scanner.requests.get_nowait()
                except queue.Empty:
                    break
                waiting.error = error or ResourceAdmissionError("maintenance_closed")
                waiting.done.set()

    def _drain(self, scanner: _Scanner) -> None:
        child = scanner.child
        while True:
            try:
                status = self._repository.reconcile(child.ticket)
                proof = child.exit_evidence(None if status is None else status.child)
                if proof is not None:
                    if status is None:
                        if proof.kind != "NOT_STARTED":
                            raise ResourceAdmissionError("maintenance_execution_unconfirmed")
                    else:
                        confirmed = self._repository.confirm(child.ticket, proof)
                        if (
                            confirmed.ticket != child.ticket
                            or confirmed.state != ExecutionState.CLOSED
                        ):
                            raise ResourceAdmissionError("maintenance_execution_unconfirmed")
                    child.close_pipes_after_worker_exit()
                    child.close_tree_after_acknowledgement()
                    self._forget(scanner)
                    return
            except SQLAlchemyError, OSError, ResourceAdmissionError:
                pass
            sleep(0.1)

    def _forget(self, scanner: _Scanner) -> None:
        with self._lock:
            if self._scanner is scanner:
                self._scanner = None
        scanner.done.set()
