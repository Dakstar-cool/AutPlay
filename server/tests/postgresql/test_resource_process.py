"""Actual retained child handles joined to durable PostgreSQL execution accounting."""

from __future__ import annotations

import hashlib
import subprocess
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
)
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess, VaultProcessSupervisor
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ProcessExitEvidence, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline

from .test_resource_admission_runtime import AdmissionHarness, admission
from .test_resource_execution import upload_ticket

__all__ = ["admission"]


def wait_exit(child: RetainedVaultProcess, identity: ProcessIdentity | None) -> ProcessExitEvidence:
    until = monotonic() + 5
    while (proof := child.exit_evidence(identity)) is None:
        if monotonic() >= until:
            pytest.fail("owned test child did not exit")
        sleep(0.01)
    return proof


def test_durable_registration_then_go_and_confirmed_exit_control_charge(
    admission: AdmissionHarness,
    tmp_path: Path,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=2)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    identity = child.spawn()
    try:
        storage = FilesystemVaultStorage(tmp_path)
        key = OpaqueStorageKey(ticket.actual_target_id.hex)
        storage.create_staging(key)
        document: dict[str, object] = {
            "version": 1,
            "kind": "UPLOAD",
            "root": str(tmp_path),
            "key": key.value,
            "max_object_bytes": 1024 * 1024,
            "max_chunk_bytes": 1024 * 1024,
            "max_chunks": 4096,
            "io_block_bytes": 128 * 1024,
            "committed_size": 0,
            "offset": 0,
            "payload_sha256": hashlib.sha256(b"hello").hexdigest(),
        }
        with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
            child.go(document, b"hello")
        registered = admission.service.start_execution(actor, ticket, identity)
        child.allow_go(registered)
        child.go(document, b"hello")
        tag, payload = child.read_result()
        assert tag == b"R" and decode_document(payload) == {"next_offset": 5}
        assert storage.staging_path_for_media(key).read_bytes() == b"hello"
        proof = wait_exit(child, identity)
        admission.service.release(actor, ticket.permit.fence)
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, registered)
        assert supervisor.snapshot() == (child,)
        closed = admission.service.confirm_execution_exit(ticket, proof)
        admission.service.close_io(ticket.permit)
        supervisor.forget(child, closed)
        assert not supervisor.snapshot()
        assert admission.service.poll(actor, ticket.permit.fence.operation_id).usage.server == 0
    finally:
        child.request_stop()
        wait_exit(child, identity)
        child.close_pipes_after_worker_exit()


def test_kill_request_without_exit_keeps_handle_and_slot(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    child.spawn()
    try:

        def deferred_kill(process: subprocess.Popen[bytes]) -> None:
            del process

        with monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, "kill", deferred_kill)
            child.request_stop()
            assert child.exit_evidence(None) is None
            admission.service.release(actor, ticket.permit.fence)
            with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
                admission.service.close_io(ticket.permit)
            assert admission.service.poll(actor, ticket.permit.fence.operation_id).usage.server == 1
            assert supervisor.snapshot() == (child,)
        child.request_stop()
        proof = wait_exit(child, None)
        assert proof.kind == "SUPERVISOR_EXIT"
        closed = admission.service.confirm_execution_exit(ticket, proof)
        supervisor.forget(child, closed)
        admission.service.close_io(ticket.permit)
    finally:
        child.request_stop()
        wait_exit(child, None)
        child.close_pipes_after_worker_exit()


def test_late_start_ack_never_enables_go_and_absent_accounting_needs_local_exit(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    prepared = admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    local_now = [monotonic()]
    guard = ResourceIoDeadline(local_now[0], clock=lambda: local_now[0])
    child = supervisor.retain(ticket, guard)
    identity = child.spawn()
    try:
        registered = admission.service.start_execution(actor, ticket, identity)
        local_now[0] += 6
        with pytest.raises(IoStopped):
            child.allow_go(registered)
        absent = replace(prepared, state=ExecutionState.ABSENT, heartbeat_at=None)
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, absent)
        child.request_stop()
        proof = wait_exit(child, identity)
        closed = admission.service.confirm_execution_exit(ticket, proof)
        supervisor.forget(child, closed)
    finally:
        child.request_stop()
        wait_exit(child, identity)
        child.close_pipes_after_worker_exit()


def test_ambiguous_popen_failure_never_claims_child_was_not_started(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    original = subprocess.Popen
    created: list[subprocess.Popen[bytes]] = []

    def interrupted(arguments: list[str], **options: Any) -> subprocess.Popen[bytes]:
        created.append(original(arguments, **options))
        raise OSError("synthetic interruption after OS creation")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", interrupted)
            with pytest.raises(OSError):
                child.spawn()
        assert created and created[0].poll() is None
        assert child.exit_evidence(None) is None
        admission.service.release(actor, ticket.permit.fence)
        assert admission.service.poll(actor, ticket.permit.fence.operation_id).usage.server == 1
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            admission.service.close_io(ticket.permit)
    finally:
        # The test independently retained the real handle; application state did not.
        for process in created:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
    assert child.exit_evidence(None) is None


def test_mismatched_hello_pid_requires_independent_reconciliation(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    prepared = admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    original = RetainedVaultProcess._read

    def mismatched(instance: RetainedVaultProcess) -> tuple[bytes, bytes]:
        tag, payload = original(instance)
        hello = decode_document(payload)
        assert isinstance(hello["pid"], int)
        hello["pid"] += 1
        return tag, encode_document(hello)

    with monkeypatch.context() as patch:
        patch.setattr(RetainedVaultProcess, "_read", mismatched)
        with pytest.raises(ChildProtocolError):
            child.spawn()
    try:
        child.request_stop()
        until = monotonic() + 5
        while True:
            try:
                child.close_pipes_after_worker_exit()
                break
            except ResourceAdmissionError:
                if monotonic() >= until:
                    pytest.fail("owned test handle did not exit")
                sleep(0.01)
        assert child.exit_evidence(None) is None
        absent = replace(prepared, state=ExecutionState.ABSENT, heartbeat_at=None)
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, absent)
        assert supervisor.snapshot() == (child,)
    finally:
        child.request_stop()


def test_buffered_broken_stdin_does_not_skip_stdout_close(admission: AdmissionHarness) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    child.spawn()
    source, destination = child._pipes()
    try:
        source.write(b"unflushed")
        child.request_stop()
        proof = wait_exit(child, None)
        child.close_pipes_after_worker_exit()
        assert source.closed and destination.closed
        child.close_pipes_after_worker_exit()
        closed = admission.service.confirm_execution_exit(ticket, proof)
        supervisor.forget(child, closed)
    finally:
        child.request_stop()
        wait_exit(child, None)
        child.close_pipes_after_worker_exit()
