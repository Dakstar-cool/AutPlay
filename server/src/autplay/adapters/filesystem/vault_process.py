"""Retained exact-process handles for Vault; blocking calls belong to an owned worker."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from contextlib import suppress
from typing import BinaryIO, cast
from uuid import UUID

from autplay.adapters.child_process import vault_child_launch
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExecutionStatus,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.runtime.resource_io_deadline import ResourceIoDeadline

from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


class RetainedVaultProcess:
    """One single-threaded pipe owner, with concurrent stop/poll from the supervisor.

    The supervisor retains this object BEFORE spawn may block. The caller must have
    already committed prepare_execution. Only an exact durable RUNNING result can
    enable GO, and the local deadline is checked again immediately before sending.
    """

    def __init__(self, ticket: ExecutionTicket, deadline: ResourceIoDeadline) -> None:
        self.ticket, self.deadline = ticket, deadline
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
        self.deadline.stop()
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
            arguments, environment = vault_child_launch()
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
            self.request_stop()
            raise
        finally:
            with self._lock:
                self._spawn_finished = True

    def allow_go(self, registered: ExecutionStatus) -> None:
        self.deadline.check()
        with self._lock:
            if (
                registered.ticket != self.ticket
                or registered.state != ExecutionState.RUNNING
                or self._identity is None
                or registered.child != self._identity
                or self._go_sent
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

    def read_result(self) -> tuple[bytes, bytes]:
        return self._read()

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
        self.deadline.stop()
        with self._lock:
            process = self._process
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
        evidence = encode_document(
            {
                "execution_id": str(self.ticket.execution_id),
                "owner_run_id": str(self.ticket.owner_run_id),
                "pid": None if process is None else process.pid,
                "kind": kind.value,
                "exit_code": exit_code,
                "identity": None if identity is None else identity.identity_sha256.hex(),
            }
        )
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


class VaultProcessSupervisor:
    """Own unresolved handles until pipe work and durable exit acknowledgement finish."""

    def __init__(self, *, maximum: int) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 1_000_000:
            raise ValueError("invalid process supervisor bound")
        self._maximum = maximum
        self._lock = threading.Lock()
        self._children: dict[UUID, RetainedVaultProcess] = {}

    def retain(self, ticket: ExecutionTicket, deadline: ResourceIoDeadline) -> RetainedVaultProcess:
        with self._lock:
            if ticket.execution_id in self._children:
                raise ResourceAdmissionError("resource_execution_conflict")
            if len(self._children) >= self._maximum:
                raise ResourceAdmissionError("resource_execution_busy")
            child = RetainedVaultProcess(ticket, deadline)
            self._children[ticket.execution_id] = child
            return child

    def snapshot(self) -> tuple[RetainedVaultProcess, ...]:
        with self._lock:
            return tuple(self._children.values())

    def stop_all(self) -> None:
        for child in self.snapshot():
            child.request_stop()

    def forget(self, child: RetainedVaultProcess, acknowledged: ExecutionStatus) -> None:
        if acknowledged.ticket != child.ticket or acknowledged.state not in {
            ExecutionState.CLOSED,
            ExecutionState.ABSENT,
        }:
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        # Missing DB accounting never substitutes for local process-exit proof.
        if child.exit_evidence(acknowledged.child) is None:
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        child.close_pipes_after_worker_exit()
        with self._lock:
            if self._children.get(child.ticket.execution_id) is not child:
                raise ResourceAdmissionError("resource_execution_stale")
            del self._children[child.ticket.execution_id]
