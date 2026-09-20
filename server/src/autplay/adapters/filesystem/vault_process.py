"""Retained exact-process handles for Vault; blocking calls belong to an owned worker."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import BinaryIO, cast
from uuid import UUID

from autplay.adapters.child_process import vault_child_launch
from autplay.adapters.process_tree import ProcessTree
from autplay.domain.ingest_cleanup import IngestCleanupTicket
from autplay.domain.ingest_execution import IngestExecutionStatus, IngestExecutionTicket
from autplay.domain.metadata_execution import MetadataExecutionTicket
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionState,
    ExecutionStatus,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.training_execution import TrainingExecutionTicket
from autplay.runtime.resource_io_deadline import ResourceIoDeadline

from .ingest_protocol import MAX_INGEST_REPLY
from .inventory_protocol import MAX_INVENTORY_REPLY_BYTES
from .metadata_protocol import read_packet, write_packet
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)

type ChildLaunch = Callable[[], tuple[list[str], dict[str, str]]]
type ProcessTreeFactory = Callable[[], ProcessTree]


class RetainedVaultProcess[
    Ticket: ExecutionTicket
    | MaintenanceTicket
    | IngestExecutionTicket
    | IngestCleanupTicket
    | MetadataExecutionTicket
    | TrainingExecutionTicket = ExecutionTicket
]:
    """One single-threaded pipe owner, with concurrent stop/poll from the supervisor.

    The supervisor retains this object BEFORE spawn may block. The caller must have
    already committed prepare_execution. Only an exact durable RUNNING result can
    enable GO, and the local deadline is checked again immediately before sending.
    """

    def __init__(
        self,
        ticket: Ticket,
        deadline: ResourceIoDeadline,
        *,
        tree_factory: ProcessTreeFactory | None = None,
        launch: ChildLaunch | None = None,
    ) -> None:
        if ticket.kind in {
            ExecutionKind.PROVIDER,
            "VAULT_INGEST",
            "VAULT_INGEST_CLEANUP",
            "TRACK_METADATA",
            "SHARED_TRAINING",
        } and (tree_factory is None or launch is None):
            raise ResourceAdmissionError("resource_process_tree_unavailable")
        self.ticket, self.deadline = ticket, deadline
        self._tree_factory, self._launch = tree_factory, launch or vault_child_launch
        self._tree: ProcessTree | None = None
        self._tree_attached = False
        self._lock = threading.Lock()
        self._pipe_lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._spawn_started = False
        self._spawn_attempted = False
        self._spawn_finished = False
        self._sealed_without_spawn = False
        self._identity_mismatch = False
        self._identity: ProcessIdentity | None = None
        self._allowed = False
        self._go_sent = False
        self._pipes_closed = False

    def seal_without_spawn(self) -> bool:
        """Atomically forbid a pending spawn; true is local proof it never began."""
        self.deadline.stop(preserve_error=True)
        with self._lock:
            if self._spawn_started:
                return self._sealed_without_spawn
            self._spawn_started = self._spawn_finished = self._sealed_without_spawn = True
            return True

    def spawn(self) -> ProcessIdentity:
        with self._lock:
            if self._spawn_started:
                raise ResourceAdmissionError("resource_execution_conflict")
            self._spawn_started = True
        try:
            self.deadline.check()
            if self._tree_factory is not None:
                tree = self._tree_factory()
                with self._lock:
                    self._tree = tree
                self.deadline.check()
            arguments, environment = self._launch()
            with self._lock:
                self._spawn_attempted = True
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self._lock:
                self._process = process
            self.deadline.check()
            if self._tree is not None:
                self._tree.attach(process)
                with self._lock:
                    self._tree_attached = True
                self.deadline.check()
            tag, payload = self._read()
            hello = decode_document(payload)
            if tag != b"H" or set(hello) != {"pid", "nonce"} or type(hello["pid"]) is not int:
                raise ChildProtocolError()
            nonce = hello["nonce"]
            if hello["pid"] != process.pid:
                with self._lock:
                    self._identity_mismatch = True
                raise ChildProtocolError()
            if not isinstance(nonce, str):
                raise ChildProtocolError()
            if len(nonce) != 32 or UUID(hex=nonce).hex != nonce:
                raise ChildProtocolError()
            identity = ProcessIdentity(process.pid, hashlib.sha256(payload).digest())
            with self._lock:
                self._identity = identity
            return identity
        except BaseException:
            self.deadline.freeze_error()
            self.request_stop()
            raise
        finally:
            with self._lock:
                self._spawn_finished = True

    def allow_go[
        IngestTicket: IngestExecutionTicket
        | IngestCleanupTicket
        | MetadataExecutionTicket
        | TrainingExecutionTicket
    ](
        self,
        registered: ExecutionStatus | MaintenanceStatus | IngestExecutionStatus[IngestTicket],
    ) -> None:
        self.deadline.check()
        with self._lock:
            if (
                registered.ticket != self.ticket
                or registered.state != ExecutionState.RUNNING
                or self._identity is None
                or registered.child != self._identity
                or self._go_sent
                or (
                    self.ticket.kind
                    in {
                        ExecutionKind.PROVIDER,
                        "VAULT_INGEST",
                        "VAULT_INGEST_CLEANUP",
                        "TRACK_METADATA",
                        "SHARED_TRAINING",
                    }
                    and not self._tree_attached
                )
            ):
                raise ResourceAdmissionError("resource_execution_stale")
            self._allowed = True

    def go(self, command: dict[str, object], payload: bytes | None = None) -> None:
        """One worker owns this method and every subsequent read; never the event loop."""
        self.deadline.check()
        encoded = encode_document(command)
        with self._lock:
            if not self._allowed or self._go_sent:
                raise ResourceAdmissionError("resource_execution_stale")
            self._go_sent = True
        self._write(b"G", encoded)
        if payload is not None:
            self._write(b"D", payload)

    def next_block(self) -> tuple[bytes, bytes]:
        self._write(b"N", b"")
        return self._read()

    def inventory_page(self, command: dict[str, object]) -> tuple[bytes, bytes]:
        """One page exchange under the sole pipe lock, after durable scanner GO."""
        encoded = encode_document(command)
        with self._pipe_lock:
            self.deadline.check()
            with self._lock:
                if (
                    not isinstance(self.ticket, MaintenanceTicket)
                    or self.ticket.action != MaintenanceAction.INVENTORY
                    or not self._allowed
                    or not self._go_sent
                ):
                    raise ResourceAdmissionError("resource_execution_stale")
            source, destination = self._pipes()
            write_frame(source, b"N", encoded)
            self.deadline.check()
            result = read_frame(destination, maximum=MAX_INVENTORY_REPLY_BYTES)
            self.deadline.check()
            return result

    def read_result(self) -> tuple[bytes, bytes]:
        return self._read()

    def ingest_exchange(self, action: str) -> tuple[bytes, bytes]:
        """One bounded phase, owned by the ingest pipe worker after durable GO."""
        encoded = encode_document({"action": action})
        with self._pipe_lock:
            self.deadline.check()
            with self._lock:
                if (
                    not isinstance(self.ticket, (IngestExecutionTicket, IngestCleanupTicket))
                    or not self._allowed
                    or not self._go_sent
                    or not self._tree_attached
                ):
                    raise ResourceAdmissionError("ingest_execution_stale")
            source, destination = self._pipes()
            write_frame(source, b"N", encoded)
            self.deadline.check()
            result = read_frame(destination, maximum=MAX_INGEST_REPLY)
            self.deadline.check()
            return result

    def metadata_exchange(
        self,
        command: dict[str, object],
        payload: bytes | None,
        *,
        begin_request: Callable[[UUID], None],
        end_request: Callable[[UUID], None],
    ) -> tuple[bytes, dict[str, object], bytes | None]:
        """The sole pipe owner grants each network request through durable pacing."""
        with self._pipe_lock:
            self.deadline.check()
            with self._lock:
                if (
                    not isinstance(self.ticket, MetadataExecutionTicket)
                    or not self._allowed
                    or not self._go_sent
                    or not self._tree_attached
                ):
                    raise ResourceAdmissionError("metadata_execution_stale")
            source, destination = self._pipes()
            write_packet(source, b"N", command, payload)
            active: UUID | None = None
            while True:
                self.deadline.check()
                tag, envelope = read_frame(destination, maximum=65536)
                if tag in {b"B", b"C"}:
                    value = decode_document(envelope)
                    if set(value) != {"request_id"} or not isinstance(value["request_id"], str):
                        raise ChildProtocolError()
                    request = UUID(value["request_id"])
                    if tag == b"B" and active is None:
                        begin_request(request)
                        active = request
                    elif tag == b"C" and active == request:
                        end_request(request)
                        active = None
                    else:
                        raise ChildProtocolError()
                    self.deadline.check()
                    write_frame(source, b"A", envelope)
                    continue
                if tag not in {b"R", b"E"} or active is not None:
                    raise ChildProtocolError()
                document, data = read_packet(destination, envelope)
                self.deadline.check()
                return tag, document, data

    def training_exchange(
        self,
        handle_request: Callable[[bytes, dict[str, object]], dict[str, object]],
    ) -> tuple[bytes, dict[str, object]]:
        """Serve bounded current-authority RPCs while the isolated trainer owns payload I/O."""

        with self._pipe_lock:
            self.deadline.check()
            with self._lock:
                if (
                    not isinstance(self.ticket, TrainingExecutionTicket)
                    or not self._allowed
                    or not self._go_sent
                    or not self._tree_attached
                ):
                    raise ResourceAdmissionError("training_execution_stale")
            source, destination = self._pipes()
            while True:
                self.deadline.check()
                tag, payload = read_frame(destination)
                if tag in {b"A", b"C", b"S"}:
                    request = decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES)
                    response = handle_request(tag, request)
                    encoded = encode_document(response, maximum=MAX_INVENTORY_REPLY_BYTES)
                    self.deadline.check()
                    write_frame(
                        source,
                        b"K",
                        encoded,
                    )
                    self.deadline.check()
                    continue
                if tag not in {b"R", b"E"}:
                    raise ChildProtocolError()
                return tag, decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES)

    def _pipes(self) -> tuple[BinaryIO, BinaryIO]:
        with self._lock:
            process = self._process
            if (
                process is None
                or self._pipes_closed
                or process.stdin is None
                or process.stdout is None
            ):
                raise ChildProtocolError()
            return cast(BinaryIO, process.stdin), cast(BinaryIO, process.stdout)

    def _write(self, tag: bytes, payload: bytes) -> None:
        with self._pipe_lock:
            self.deadline.check()
            source, _ = self._pipes()
            write_frame(source, tag, payload)
            self.deadline.check()

    def _read(self) -> tuple[bytes, bytes]:
        with self._pipe_lock:
            self.deadline.check()
            _, destination = self._pipes()
            result = read_frame(destination)
            self.deadline.check()
            return result

    def request_stop(self) -> None:
        """Request termination of the exact retained child; this is never exit proof."""
        self.deadline.stop(preserve_error=True)
        with self._lock:
            process = self._process
            tree = self._tree
        if tree is not None:
            # Descendants may still be active after the exact root has exited.
            with suppress(OSError):
                tree.request_stop()
        if process is not None and process.poll() is None:
            # Keep ownership and durable charge on failed/delayed termination.
            with suppress(OSError):
                process.kill()

    def exit_evidence(self, persisted_child: ProcessIdentity | None) -> ProcessExitEvidence | None:
        """Poll exact handle without waiting. The caller supplies reconciled DB identity."""
        with self._lock:
            if not self._spawn_finished:
                return None
            process, identity = self._process, self._identity
            tree = self._tree
            if self._identity_mismatch or (self._spawn_attempted and process is None):
                # Creation may have succeeded before Popen raised. A mismatched
                # HELLO likewise disproves that this handle covers the worker.
                return None
        if persisted_child is not None and persisted_child != identity:
            raise ResourceAdmissionError("resource_execution_stale")
        if process is None:
            kind, exit_code = ExitKind.NOT_STARTED, None
        else:
            exit_code = process.poll()
            if exit_code is None:
                return None
            kind = (
                ExitKind.PROCESS_EXIT if persisted_child is not None else ExitKind.SUPERVISOR_EXIT
            )
        tree_evidence: bytes | None = None
        if tree is not None:
            try:
                tree_evidence = tree.seal_if_empty()
            except OSError, ResourceAdmissionError:
                return None
            if tree_evidence is None:
                return None
        document: dict[str, object] = {
            "execution_id": str(self.ticket.execution_id),
            "owner_run_id": str(self.ticket.owner_run_id),
            "pid": None if process is None else process.pid,
            "kind": kind.value,
            "exit_code": exit_code,
            "identity": None if identity is None else identity.identity_sha256.hex(),
        }
        if tree_evidence is not None:
            document["process_tree"] = tree_evidence.hex()
        evidence = encode_document(document)
        return ProcessExitEvidence(
            kind, hashlib.sha256(evidence).digest(), exit_code, persisted_child
        )

    def close_pipes_after_worker_exit(self) -> None:
        """Only the single pipe owner calls this, after its last pipe call has returned."""
        if not self._pipe_lock.acquire(blocking=False):
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        try:
            self._close_pipes()
        finally:
            self._pipe_lock.release()

    def _close_pipes(self) -> None:
        with self._lock:
            process = self._process
            if not self._spawn_finished or (process is not None and process.poll() is None):
                raise ResourceAdmissionError("resource_execution_unconfirmed")
            if self._pipes_closed:
                return
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            if any(
                stream is not None and not stream.closed
                for stream in (process.stdin, process.stdout)
            ):
                raise ResourceAdmissionError("resource_execution_unconfirmed")
        with self._lock:
            self._pipes_closed = True

    def close_tree_after_acknowledgement(self) -> None:
        """The supervisor calls this only after root/tree exit and durable acknowledgement."""
        with self._lock:
            if not self._pipes_closed:
                raise ResourceAdmissionError("resource_execution_unconfirmed")
            tree = self._tree
        if tree is not None:
            tree.close_after_exit()


class VaultProcessSupervisor[
    Ticket: ExecutionTicket | IngestExecutionTicket | TrainingExecutionTicket = ExecutionTicket
]:
    """Own unresolved handles until pipe work and durable exit acknowledgement finish."""

    def __init__(self, *, maximum: int) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 1_000_000:
            raise ValueError("invalid process supervisor bound")
        self._maximum = maximum
        self._lock = threading.Lock()
        self._children: dict[UUID, RetainedVaultProcess[Ticket]] = {}

    def retain(
        self,
        ticket: Ticket,
        deadline: ResourceIoDeadline,
        *,
        tree_factory: ProcessTreeFactory | None = None,
        launch: ChildLaunch | None = None,
    ) -> RetainedVaultProcess[Ticket]:
        with self._lock:
            if ticket.execution_id in self._children:
                raise ResourceAdmissionError("resource_execution_conflict")
            if len(self._children) >= self._maximum:
                raise ResourceAdmissionError("resource_execution_busy")
            child = RetainedVaultProcess(ticket, deadline, tree_factory=tree_factory, launch=launch)
            self._children[ticket.execution_id] = child
            return child

    def snapshot(self) -> tuple[RetainedVaultProcess[Ticket], ...]:
        with self._lock:
            return tuple(self._children.values())

    def stop_all(self) -> None:
        for child in self.snapshot():
            child.request_stop()

    def forget(
        self,
        child: RetainedVaultProcess[Ticket],
        acknowledged: ExecutionStatus | IngestExecutionStatus,
    ) -> None:
        if acknowledged.ticket != child.ticket or acknowledged.state not in {
            ExecutionState.CLOSED,
            ExecutionState.ABSENT,
        }:
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        # Missing DB accounting never substitutes for local process-exit proof.
        if child.exit_evidence(acknowledged.child) is None:
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        child.close_pipes_after_worker_exit()
        child.close_tree_after_acknowledgement()
        with self._lock:
            if self._children.get(child.ticket.execution_id) is not child:
                raise ResourceAdmissionError("resource_execution_stale")
            del self._children[child.ticket.execution_id]
