"""Retain contained ingest work across blocked pipes, lost leases and uncertain DB replies."""

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from autplay.adapters.child_process import ingest_child_launch
from autplay.adapters.filesystem.ingest_process import ProcessIngestStorage, ingest_reply
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.filesystem.vault_process import (
    ChildLaunch,
    ProcessTreeFactory,
    RetainedVaultProcess,
)
from autplay.domain.ingest_cleanup import IngestCleanupTicket
from autplay.domain.ingest_execution import (
    MAX_INGEST_COORDINATOR_ENTRIES,
    IngestExecutionStatus,
    IngestExecutionTicket,
)
from autplay.domain.metadata_execution import MetadataExecutionTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.runtime.resource_io_deadline import ResourceIoDeadline


class IngestExecutionRepository[
    Ticket: IngestExecutionTicket
    | IngestCleanupTicket
    | MetadataExecutionTicket = IngestExecutionTicket
](Protocol):
    def prepare(self, ticket: Ticket) -> IngestExecutionStatus[Ticket]: ...
    def start(self, ticket: Ticket, child: ProcessIdentity) -> IngestExecutionStatus[Ticket]: ...
    def renew(self, ticket: Ticket, child: ProcessIdentity) -> IngestExecutionStatus[Ticket]: ...
    def status(self, ticket: Ticket) -> IngestExecutionStatus[Ticket] | None: ...

    def reconcile(self, ticket: Ticket) -> IngestExecutionStatus[Ticket] | None: ...
    def confirm(
        self, ticket: Ticket, proof: ProcessExitEvidence
    ) -> IngestExecutionStatus[Ticket]: ...


@dataclass(frozen=True)
class IngestWork:
    storage: ProcessIngestStorage
    running: IngestExecutionStatus
    freeze_renewals: Callable[[], None]


@dataclass
class _Retained[Ticket: IngestExecutionTicket | IngestCleanupTicket | MetadataExecutionTicket]:
    child: RetainedVaultProcess[Ticket]
    action: Callable[
        [RetainedVaultProcess[Ticket], IngestExecutionStatus[Ticket], Callable[[], None]], object
    ]
    done: threading.Event = field(default_factory=threading.Event)
    launch_gate: threading.Event = field(default_factory=threading.Event)
    io_done: threading.Event = field(default_factory=threading.Event)
    freeze_requested: threading.Event = field(default_factory=threading.Event)
    renew_lock: threading.Lock = field(default_factory=threading.Lock)
    launch_permitted: bool = False
    result: object = None
    error: BaseException | None = None

    def freeze_renewals(self) -> None:
        # Stop NEW RPCs before waiting for an in-flight one. The independent
        # watchdog never takes this lock and continues to enforce the last grant.
        self.freeze_requested.set()
        with self.renew_lock:
            self.child.deadline.check()


class _IngestCoordinator[
    Ticket: IngestExecutionTicket | IngestCleanupTicket | MetadataExecutionTicket
]:
    """Owned worker and independent watchdog; registry entries are not CPU admission.

    The caller supplies an exact fresh ticket and metadata-only callback. All
    byte/media calls use work.storage. Before terminal metadata, the callback
    MUST call freeze_renewals; finalization and FINISH share the last short grant.
    Production composition additionally requires measured internal admission and
    durable cleanup intent. This coordinator never invents a TRANSFER permit.
    """

    def __init__(
        self,
        repository: IngestExecutionRepository[Ticket],
        settings: IngestChildSettings,
        *,
        tree_factory: ProcessTreeFactory,
        maximum: int = 1,
        launch: ChildLaunch = ingest_child_launch,
        on_drained: Callable[[], None] | None = None,
        error_prefix: str = "ingest",
    ) -> None:
        if type(maximum) is not int or not 1 <= maximum <= MAX_INGEST_COORDINATOR_ENTRIES:
            raise ValueError("ingest_supervisor_bound_invalid")
        if error_prefix not in {"ingest", "metadata"}:
            raise ValueError("internal_execution_prefix_invalid")
        self._error_prefix = error_prefix
        self._repository, self._settings = repository, settings
        self._tree_factory, self._launch, self._maximum = tree_factory, launch, maximum
        self._on_drained = on_drained
        self._lock = threading.Lock()
        self._entries: dict[UUID, _Retained[Ticket]] = {}
        self._closing = False

    def _failure(self, suffix: str) -> ResourceAdmissionError:
        return ResourceAdmissionError(f"{self._error_prefix}_{suffix}")

    def pending(self) -> tuple[UUID, ...]:
        with self._lock:
            return tuple(self._entries)

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError("ingest_shutdown_invalid")
        with self._lock:
            self._closing = True
            entries = tuple(self._entries.values())
        for entry in entries:
            entry.freeze_requested.set()
            entry.child.request_stop()
        until = monotonic() + timeout
        for entry in entries:
            entry.done.wait(max(0, until - monotonic()))
        return self.pending()

    def _execute[T](
        self,
        ticket: Ticket,
        action: Callable[
            [RetainedVaultProcess[Ticket], IngestExecutionStatus[Ticket], Callable[[], None]], T
        ],
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> T:
        if check_cancelled is not None:
            check_cancelled()
        child = RetainedVaultProcess(
            ticket,
            ResourceIoDeadline(monotonic()),
            tree_factory=self._tree_factory,
            launch=self._launch,
        )
        entry = _Retained(child, action)
        with self._lock:
            if self._closing or len(self._entries) >= self._maximum:
                raise self._failure("execution_busy")
            if ticket.execution_id in self._entries:
                raise self._failure("execution_conflict")
            self._entries[ticket.execution_id] = entry
        try:
            try:
                for name, target in (
                    (f"{self._error_prefix}-watchdog", self._watch),
                    (f"{self._error_prefix}-work", self._work),
                ):
                    threading.Thread(target=target, args=(entry,), name=name, daemon=True).start()
            except BaseException:
                raise self._failure("execution_unavailable") from None
            entry.launch_permitted = True
            entry.launch_gate.set()
            while not entry.done.wait(min(0.05, child.deadline.response_remaining())):
                if check_cancelled is not None:
                    check_cancelled()
                if child.deadline.response_remaining() <= 0:
                    raise self._failure("io_deadline_expired")
            if entry.error is not None:
                raise entry.error
            if check_cancelled is not None:
                check_cancelled()
            child.deadline.check()
            return cast(T, entry.result)
        finally:
            if not entry.done.is_set():
                entry.freeze_requested.set()
                child.request_stop()
            if not entry.launch_permitted:
                child.seal_without_spawn()
                self._forget(entry)
            entry.launch_gate.set()

    def _watch(self, entry: _Retained[Ticket]) -> None:
        entry.launch_gate.wait()
        if not entry.launch_permitted:
            return
        while not entry.io_done.wait(0.025):
            if entry.child.deadline.stopped():
                entry.freeze_requested.set()
                entry.child.request_stop()
                return

    def _accept(
        self,
        entry: _Retained[Ticket],
        sequence: int,
        status: IngestExecutionStatus[Ticket],
        identity: ProcessIdentity,
    ) -> None:
        child = entry.child
        succeeded = (
            status.ticket == child.ticket
            and status.state == ExecutionState.RUNNING
            and status.child == identity
        )
        if not child.deadline.finish_renewal(
            sequence, succeeded=succeeded, authorized_seconds=status.authorized_seconds
        ):
            raise self._failure("execution_stale")

    def _renew(self, entry: _Retained[Ticket], identity: ProcessIdentity) -> None:
        while not entry.freeze_requested.wait(0.5):
            try:
                with entry.renew_lock:
                    if entry.freeze_requested.is_set():
                        return
                    sequence = entry.child.deadline.begin_renewal()
                    status = self._repository.renew(entry.child.ticket, identity)
                    self._accept(entry, sequence, status, identity)
            except BaseException:
                entry.freeze_requested.set()
                entry.child.deadline.freeze_error()
                entry.child.request_stop()
                return

    def _work(self, entry: _Retained[Ticket]) -> None:
        entry.launch_gate.wait()
        if not entry.launch_permitted:
            return
        child = entry.child
        success = False
        try:
            prepared = self._repository.prepare(child.ticket)
            if prepared.ticket != child.ticket or prepared.state != ExecutionState.PREPARED:
                raise self._failure("execution_stale")
            child.deadline.check()
            identity = child.spawn()
            sequence = child.deadline.begin_renewal()
            running = self._repository.start(child.ticket, identity)
            self._accept(entry, sequence, running, identity)
            child.allow_go(running)
            threading.Thread(
                target=self._renew, args=(entry, identity), name="ingest-renewal", daemon=True
            ).start()
            entry.result = entry.action(child, running, entry.freeze_renewals)
            entry.freeze_renewals()
            while (proof := child.exit_evidence(identity)) is None:
                child.deadline.check()
                sleep(0.01)
            if proof.exit_code != 0:
                raise self._failure("child_failed")
            success = True
        except BaseException as error:
            entry.error = (
                self._failure("execution_unavailable")
                if isinstance(error, SQLAlchemyError)
                else error
            )
        finally:
            entry.freeze_requested.set()
            if not success:
                child.deadline.freeze_error()
                child.seal_without_spawn()
                child.request_stop()
            self._drain(entry)

    def _drain(self, entry: _Retained[Ticket]) -> None:
        child = entry.child
        while True:
            # An unresolved renewal owns a DB transaction too. Do not acknowledge
            # or forget while it can still return. The watchdog remains independent.
            if entry.renew_lock.acquire(blocking=False):
                try:
                    status = self._repository.reconcile(child.ticket)
                    proof = child.exit_evidence(None if status is None else status.child)
                    if proof is not None:
                        entry.io_done.set()
                        if status is None:
                            if proof.kind != ExitKind.NOT_STARTED:
                                raise self._failure("execution_unconfirmed")
                        else:
                            acknowledged = self._repository.confirm(child.ticket, proof)
                            if (
                                acknowledged.ticket != child.ticket
                                or acknowledged.state != ExecutionState.CLOSED
                                or acknowledged.child != status.child
                            ):
                                raise self._failure("execution_unconfirmed")
                        child.close_pipes_after_worker_exit()
                        child.close_tree_after_acknowledgement()
                        self._forget(entry)
                        return
                except SQLAlchemyError, OSError, ResourceAdmissionError:
                    pass
                finally:
                    entry.renew_lock.release()
            sleep(0.1)

    def _forget(self, entry: _Retained[Ticket]) -> None:
        with self._lock:
            if self._entries.get(entry.child.ticket.execution_id) is entry:
                del self._entries[entry.child.ticket.execution_id]
        entry.io_done.set()
        entry.done.set()
        if self._on_drained is not None:
            self._on_drained()


class IngestProcessCoordinator(_IngestCoordinator[IngestExecutionTicket]):
    def run[T](
        self,
        ticket: IngestExecutionTicket,
        action: Callable[[IngestWork], T],
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> T:
        def phases(
            child: RetainedVaultProcess[IngestExecutionTicket],
            running: IngestExecutionStatus,
            freeze: Callable[[], None],
        ) -> T:
            storage = ProcessIngestStorage(child, self._settings)
            storage.begin()
            result = action(IngestWork(storage, running, freeze))
            freeze()
            storage.finish()
            return result

        return self._execute(ticket, phases, check_cancelled=check_cancelled)


class IngestCleanupCoordinator(_IngestCoordinator[IngestCleanupTicket]):
    def run(self, ticket: IngestCleanupTicket) -> UUID:
        def cleanup(
            child: RetainedVaultProcess[IngestCleanupTicket],
            running: IngestExecutionStatus[IngestCleanupTicket],
            freeze: Callable[[], None],
        ) -> UUID:
            del running
            child.go(
                {
                    "version": 1,
                    "execution_id": str(ticket.execution_id),
                    "mode": "CLEANUP",
                    "key": ticket.staging_key.value,
                    "settings": self._settings.document(),
                    "expected": {
                        "byte_size": ticket.expected.byte_size,
                        "sha256": ticket.expected.sha256.hex,
                    },
                }
            )
            if ingest_reply(*child.read_result()) != {
                "execution_id": str(ticket.execution_id),
                "ready": True,
            }:
                raise ChildProtocolError()
            if ingest_reply(*child.ingest_exchange("CLEANUP")) != {
                "execution_id": str(ticket.execution_id),
                "cleaned": True,
            }:
                raise ChildProtocolError()
            freeze()
            return ticket.execution_id

        return self._execute(ticket, cleanup)
