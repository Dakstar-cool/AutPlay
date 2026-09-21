"""Bounded ownership of HTTP-scoped Vault processes beyond request cancellation."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from queue import Empty, Queue
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.vault_process import (
    ChildLaunch,
    ProcessTreeFactory,
    RetainedVaultProcess,
    VaultProcessSupervisor,
)
from autplay.application.resource_admission import ResourceActor, ResourceAdmissionService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    AcquisitionClaim,
    ActivationFence,
    IoPermit,
    LocalBridgeClaim,
    ResourceAdmissionError,
)
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionState,
    ExecutionStatus,
    ExecutionTicket,
    ProcessIdentity,
)
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline


def _launch(target: Callable[[], None], name: str) -> None:
    """A failed Thread.start cannot grant work, even if an OS thread was created."""
    gate = threading.Event()
    granted = False

    def guarded() -> None:
        gate.wait()
        if granted:
            target()

    thread = threading.Thread(target=guarded, name=name, daemon=True)
    try:
        thread.start()
    except BaseException as error:
        gate.set()
        if isinstance(error, RuntimeError):
            raise ResourceAdmissionError("resource_execution_busy") from error
        raise
    granted = True
    gate.set()


async def _wait_retained[T](future: Future[T], deadline: ResourceIoDeadline) -> T:
    # Cancellation of an HTTP waiter must never cancel the owned worker Future.
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await deadline.run(lambda: asyncio.shield(wrapped))
    except IoStopped:
        if future.done() and not future.cancelled() and (error := future.exception()) is not None:
            raise error from None
        raise


@dataclass(frozen=True)
class _Command:
    action: Callable[[], object]
    result: Future[object]


class VaultIoSession:
    """One real pipe/transaction worker and one serial control worker per reservation.

    The request may perform one blocking operation at a time. Upload callers put
    their WHOLE unit of work in a single perform call. They must not open a DB
    transaction on the event loop or split it across calls. The control service
    must use a dedicated database pool, independent of retained upload sessions.
    """

    def __init__(
        self,
        coordinator: VaultIoCoordinator,
        actor: ResourceActor,
        fence: ActivationFence,
        resource_type: str,
        target_id: UUID,
        *,
        tree_factory: ProcessTreeFactory | None = None,
        launch: ChildLaunch | None = None,
    ) -> None:
        self.identifier = uuid4()
        self.deadline = ResourceIoDeadline(monotonic())
        self._coordinator, self._actor = coordinator, actor
        self._fence, self._resource_type, self._target_id = fence, resource_type, target_id
        self._tree_factory, self._launch = tree_factory, launch
        self._permit: IoPermit | None = None
        self._child: RetainedVaultProcess | None = None
        self._ready: Future[None] = Future()
        self._identity: Future[ProcessIdentity] = Future()
        self._registered: Future[ExecutionStatus] = Future()
        self._worker_done: Future[None] = Future()
        self._closed: Future[None] = Future()
        self._worker_started = False
        self._worker_error: BaseException | None = None
        self._stop = threading.Event()
        self._commands: Queue[_Command] = Queue(maxsize=1)
        self._submission_lock = threading.Lock()
        self._pending: Future[object] | None = None
        self._reconciliation_code: str | None = None

    @property
    def child(self) -> RetainedVaultProcess:
        if self._child is None:
            raise ResourceAdmissionError("resource_execution_stale")
        return self._child

    @property
    def registered(self) -> ExecutionStatus:
        if not self._registered.done():
            raise ResourceAdmissionError("resource_execution_stale")
        return self._registered.result()

    @property
    def actor(self) -> ResourceActor:
        """Authority retained with this execution; it is never derived from a caller payload."""
        return self._actor

    @property
    def reconciliation_code(self) -> str | None:
        """Stable diagnostic code only; no paths, principals or exception text."""
        return self._reconciliation_code

    async def perform[T](self, action: Callable[[], T]) -> T:
        self.deadline.check()
        with self._submission_lock:
            if self._pending is not None and not self._pending.done():
                raise ResourceAdmissionError("resource_io_busy")
            result: Future[object] = Future()
            self._commands.put_nowait(_Command(action, result))
            self._pending = result
        return cast(T, await _wait_retained(result, self.deadline))

    def finish(self) -> None:
        """End HTTP authority immediately; cleanup remains coordinator-owned."""
        self.deadline.stop(preserve_error=True)
        self._stop.set()
        if self._child is not None:
            self._child.request_stop()

    async def close(self, *, timeout: float = 5) -> None:
        """Stop caller authority and wait for durable child-exit reconciliation."""
        if not 0 < timeout <= 5:
            raise ValueError("invalid resource I/O close timeout")
        self.finish()
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(self._closed)), timeout=timeout
            )
        except TimeoutError:
            raise ResourceAdmissionError("resource_execution_unconfirmed") from None

    async def finish_provider(self, *, timeout: float = 5) -> None:
        """Accept success only after natural tree exit and durable acknowledgement.

        A provider result frame is not exit evidence. Unresolved descendants or
        cleanup stay retained after this bounded caller gives up.
        """
        if not isinstance(self._actor, AcquisitionClaim) or not 0 < timeout <= 5:
            raise ResourceAdmissionError("resource_request_invalid")
        until = monotonic() + timeout
        try:
            while True:
                self.deadline.check()
                proof = self.child.exit_evidence(self.registered.child)
                if proof is not None:
                    if proof.exit_code != 0:
                        raise ResourceAdmissionError("resource_provider_failed")
                    break
                if monotonic() >= until:
                    raise ResourceAdmissionError("resource_execution_unconfirmed")
                await asyncio.sleep(0.02)
        finally:
            self.finish()
        # Shield the retained future from cancellation or timeout of this waiter.
        await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(self._closed)), timeout)

    def _start(self) -> None:
        _launch(self._control, "vault-io-control")

    def _prepare(self) -> None:
        service = self._coordinator.service
        self.deadline.check()
        self._permit = service.open_io(
            self._actor, self._fence, self._target_id, resource_type=self._resource_type
        )
        self.deadline.check()
        ticket = ExecutionTicket(
            uuid4(),
            self._coordinator.owner_run_id,
            self._permit,
            ExecutionKind.PROVIDER
            if isinstance(self._actor, AcquisitionClaim)
            else (
                ExecutionKind.VAULT_UPLOAD
                if self._resource_type == "UPLOAD_INTENT"
                else ExecutionKind.VAULT_STREAM
            ),
            self._target_id,
        )
        self._child = self._coordinator.supervisor.retain(
            ticket, self.deadline, tree_factory=self._tree_factory, launch=self._launch
        )
        service.prepare_execution(self._actor, ticket)
        self.deadline.check()
        self._worker_started = True
        try:
            _launch(self._work, "vault-io-worker")
        except BaseException:
            # The launch gate proves no worker code can have run on this path.
            self.deadline.freeze_error()
            self._worker_started = False
            self.child.seal_without_spawn()
            raise

    def _work(self) -> None:
        try:
            self._identity.set_result(self.child.spawn())
            while not self._registered.done():
                self.deadline.check()
                self._stop.wait(0.05)
            self._registered.result()
            while not self.deadline.stopped():
                try:
                    command = self._commands.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    self.deadline.check()
                    command.result.set_result(command.action())
                except BaseException as error:
                    self.deadline.freeze_error()
                    command.result.set_exception(error)
                    raise
        except BaseException as error:
            self.deadline.freeze_error()
            self._worker_error = error
        finally:
            self.finish()
            with self._submission_lock:
                if self._pending is not None and not self._pending.done():
                    self._pending.set_exception(IoStopped())
            # The action has returned and its UoW has committed/rolled back here.
            # A cancelled asyncio wrapper cannot set this ownership milestone.
            self._worker_done.set_result(None)

    def _fail_ready(self, error: BaseException) -> None:
        # Only the control thread settles the request-facing startup Future.
        if not self._ready.done():
            self._ready.set_exception(error)

    def _control(self) -> None:
        try:
            self._prepare()
            renew_at = monotonic() + 2
            operation_renew_at = monotonic() + 10
            while not self.deadline.stopped():
                if self._identity.done() and not self._registered.done():
                    registered = self._coordinator.service.start_execution(
                        self._actor, self.child.ticket, self._identity.result()
                    )
                    self.deadline.check()
                    self.child.allow_go(registered)
                    self._registered.set_result(registered)
                    self._ready.set_result(None)
                if self._registered.done() and monotonic() >= renew_at:
                    sequence = self.deadline.begin_renewal()
                    try:
                        if (
                            isinstance(self._actor, (AcquisitionClaim, LocalBridgeClaim))
                            and monotonic() >= operation_renew_at
                        ):
                            self._coordinator.service.renew(self._actor, self._fence)
                            self.deadline.check()
                            operation_renew_at = monotonic() + 10
                        self._coordinator.service.renew_execution_io(self._actor, self.child.ticket)
                    except BaseException:
                        self.deadline.finish_renewal(sequence, succeeded=False)
                        raise
                    if not self.deadline.finish_renewal(sequence, succeeded=True):
                        raise IoStopped()
                    renew_at = monotonic() + 2
                self._stop.wait(0.05)
        except BaseException as error:
            self.deadline.freeze_error()
            self._fail_ready(error)
        finally:
            self.finish()
            self._fail_ready(self._worker_error or IoStopped())
        while True:
            try:
                if self._cleanup():
                    self._coordinator._forget(self)
                    return
                self._reconciliation_code = "resource_execution_unconfirmed"
            except ResourceAdmissionError as error:
                self._reconciliation_code = error.code
            except Exception:
                self._reconciliation_code = "resource_service_unavailable"
            # No new RPC while the preceding one is unresolved; all retries use
            # the same ticket. A stuck call consumes this existing reservation.
            threading.Event().wait(1)

    def _cleanup(self) -> bool:
        service = self._coordinator.service
        if self._permit is None:
            return True
        if self._child is None:
            service.close_io(self._permit)
            return True
        child = self._child
        sealed = child.seal_without_spawn()
        if not self._worker_started and sealed and not self._worker_done.done():
            self._worker_done.set_result(None)
        if sealed:
            # A failed prepare may have created no execution row. Closing its
            # permit serializes behind that RPC and refuses any unclosed row.
            try:
                service.close_io(self._permit)
            except ResourceAdmissionError as error:
                if error.code != "resource_execution_unconfirmed":
                    raise
        status = service.inspect_execution(child.ticket)
        if status.state in {ExecutionState.PREPARED, ExecutionState.RUNNING}:
            status = service.stop_execution(child.ticket)
        if not self._worker_done.done():
            return False
        if not self._ready.done():
            self._ready.set_exception(IoStopped())
        proof = child.exit_evidence(status.child)
        if proof is None:
            return False
        if status.state not in {ExecutionState.CLOSED, ExecutionState.ABSENT}:
            status = service.confirm_execution_exit(child.ticket, proof)
        service.close_io(self._permit)
        self._coordinator.supervisor.forget(child, status)
        return True


class VaultIoCoordinator:
    """Lifespan owner for bounded request-independent process and transaction cleanup.

    Each entry reserves two coordinator threads and one queued command BEFORE
    any admission RPC. The chunk writer additionally owns one bounded stop-watch
    thread during its call. Daemon threads do not claim bounded filesystem/process
    exit: abrupt interpreter termination leaves durable executions charged for
    verified supervisor reconciliation. Shutdown reports unresolved reservations.
    """

    def __init__(
        self,
        service: ResourceAdmissionService,
        *,
        maximum: int,
        on_drained: Callable[[], None] | None = None,
    ) -> None:
        self.service = service
        self.supervisor = VaultProcessSupervisor(maximum=maximum)
        self.owner_run_id = uuid4()
        self._maximum = maximum
        self._on_drained = on_drained
        self._lock = threading.Lock()
        self._entries: dict[UUID, VaultIoSession] = {}
        self._started = self._closing = False
        self._wake = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._started or self._closing:
                raise RuntimeError("Vault I/O coordinator cannot start twice")
            _launch(self._watch, "vault-io-watch")
            self._started = True

    async def open(
        self,
        actor: Principal,
        fence: ActivationFence,
        *,
        resource_type: str,
        target_id: UUID,
        on_bind: Callable[[VaultIoSession], None] | None = None,
    ) -> VaultIoSession:
        if resource_type not in {"UPLOAD_INTENT", "PLAY_INSTANCE", "DOWNLOAD_INTENT"}:
            raise ResourceAdmissionError("resource_request_invalid")
        return await self._open(
            VaultIoSession(self, actor, fence, resource_type, target_id), on_bind=on_bind
        )

    async def open_provider(
        self,
        claim: AcquisitionClaim,
        fence: ActivationFence,
        *,
        tree_factory: ProcessTreeFactory,
        launch: ChildLaunch = provider_child_launch,
        on_bind: Callable[[VaultIoSession], None] | None = None,
    ) -> VaultIoSession:
        """Internal worker authority only; never a device HTTP purpose extension."""
        return await self._open(
            VaultIoSession(
                self,
                claim,
                fence,
                claim.resource_type,
                claim.acquisition_id,
                tree_factory=tree_factory,
                launch=launch,
            ),
            on_bind=on_bind,
        )

    async def open_bridge(
        self,
        claim: LocalBridgeClaim,
        fence: ActivationFence,
        *,
        target_id: UUID,
        on_bind: Callable[[VaultIoSession], None] | None = None,
    ) -> VaultIoSession:
        """Local bridge authority is limited to retained upload processes."""
        return await self._open(
            VaultIoSession(self, claim, fence, "UPLOAD_INTENT", target_id), on_bind=on_bind
        )

    async def _open(
        self,
        entry: VaultIoSession,
        *,
        on_bind: Callable[[VaultIoSession], None] | None,
    ) -> VaultIoSession:
        with self._lock:
            if not self._started or self._closing or len(self._entries) >= self._maximum:
                raise ResourceAdmissionError("resource_execution_busy")
            self._entries[entry.identifier] = entry
        try:
            if on_bind is not None:
                on_bind(entry)
            entry._start()
        except BaseException:
            entry.deadline.freeze_error()
            entry.finish()
            # The denied launch gate proves no admission RPC or child can exist.
            self._forget(entry)
            raise
        try:
            await _wait_retained(entry._ready, entry.deadline)
            return entry
        except BaseException:
            entry.finish()
            raise

    def pending(self) -> tuple[VaultIoSession, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def request_stop(self) -> None:
        """Latch admission closed and interrupt bytes independently of the caller."""
        with self._lock:
            self._closing = True
        for entry in self.pending():
            entry.finish()
        self._wake.set()

    async def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not 0 <= timeout <= 5:
            raise ValueError("invalid shutdown reconciliation timeout")
        self.request_stop()
        until = monotonic() + timeout
        while self.pending() and monotonic() < until:
            await asyncio.sleep(0.05)
        self._wake.set()
        return tuple(entry.identifier for entry in self.pending())

    def _watch(self) -> None:
        while True:
            with self._lock:
                entries = tuple(self._entries.values())
                drained = self._closing and not entries
            if drained:
                if self._on_drained is not None:
                    self._on_drained()
                return
            for entry in entries:
                if entry.deadline.stopped():
                    entry.finish()
            self._wake.wait(0.2)
            self._wake.clear()

    def _forget(self, entry: VaultIoSession) -> None:
        with self._lock:
            if self._entries.get(entry.identifier) is not entry:
                raise ResourceAdmissionError("resource_execution_stale")
            del self._entries[entry.identifier]
            entry._closed.set_result(None)
