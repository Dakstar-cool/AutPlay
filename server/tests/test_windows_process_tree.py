"""Real Windows Job Object containment and stop/attach races, without a database."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Any, cast

import pytest
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess, VaultProcessSupervisor
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ExecutionStatus, ExitKind
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline
from process_tree_support import (
    provider_ticket,
    tree_child_launch,
    wait_marker,
    wait_tree_exit,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows Job Object proof")


def test_root_exit_does_not_prove_descendant_exit_and_stop_terminates_tree(tmp_path: Path) -> None:
    tree = WindowsJobTree()
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=lambda: tree,
        launch=tree_child_launch,
    )
    identity = None
    try:
        identity = child.spawn()
        marker = tmp_path / "descendant"
        assert not marker.exists()
        running = ExecutionStatus(child.ticket, ExecutionState.RUNNING, None, identity, None)
        child.allow_go(running)
        child.go({"marker": str(marker)})
        assert child.read_result()[0] == b"R"
        assert child._process is not None and child._process.wait(timeout=5) == 0
        wait_marker(marker)
        assert tree.seal_if_empty() is None
        assert child.exit_evidence(identity) is None
        assert supervisor.snapshot() == (child,)
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, replace(running, state=ExecutionState.ABSENT))
        child.request_stop()  # Root is already dead; the job still must be terminated.
        proof = wait_tree_exit(child, identity)
        assert proof.kind == ExitKind.PROCESS_EXIT and proof.exit_code == 0
        assert child.exit_evidence(identity) == proof
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            child.close_tree_after_acknowledgement()
        supervisor.forget(child, replace(running, state=ExecutionState.CLOSED))
        assert not supervisor.snapshot()
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


@pytest.mark.parametrize("stop_at", ["popen", "attach", "tree_factory"])
def test_stop_while_creation_is_pending_never_allows_late_go(
    monkeypatch: pytest.MonkeyPatch,
    stop_at: str,
) -> None:
    tree = WindowsJobTree()
    arrived, resume = threading.Event(), threading.Event()
    original_popen, original_attach = subprocess.Popen, tree.attach

    def barrier() -> None:
        arrived.set()
        assert resume.wait(timeout=5)

    def pending_popen(arguments: list[str], **options: Any) -> subprocess.Popen[bytes]:
        barrier()
        return original_popen(arguments, **options)

    def pending_attach(process: subprocess.Popen[bytes]) -> None:
        barrier()
        original_attach(process)

    def factory() -> WindowsJobTree:
        if stop_at == "tree_factory":
            barrier()
        return tree

    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=factory,
        launch=tree_child_launch,
    )
    if stop_at == "popen":
        monkeypatch.setattr(subprocess, "Popen", pending_popen)
    elif stop_at == "attach":
        monkeypatch.setattr(tree, "attach", pending_attach)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(child.spawn)
        try:
            assert arrived.wait(timeout=5)
            child.request_stop()
            assert child.exit_evidence(None) is None
            assert not child.seal_without_spawn()
            assert supervisor.snapshot() == (child,)
            resume.set()
            with pytest.raises(ResourceAdmissionError):
                future.result(timeout=5)
            proof = wait_tree_exit(child, None)
            assert proof.kind == (
                ExitKind.NOT_STARTED if stop_at == "tree_factory" else ExitKind.SUPERVISOR_EXIT
            )
            with pytest.raises(IoStopped):
                child.go({"marker": "must-never-be-written"})
        finally:
            resume.set()
            child.request_stop()
            # Ensure the only spawn worker has settled before closing its pipes.
            with suppress(ResourceAdmissionError):
                future.result(timeout=5)
            wait_tree_exit(child, None)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


@pytest.mark.parametrize("action", ["stop", "seal"])
def test_empty_job_cannot_accept_process_after_stop_or_seal(action: str) -> None:
    tree = WindowsJobTree()
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        if action == "stop":
            tree.request_stop()
        else:
            assert tree.seal_if_empty() is not None
        with pytest.raises(ResourceAdmissionError, match="resource_process_tree_stopped"):
            tree.attach(process)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=5)
        assert process.stdin is not None
        process.stdin.close()
        assert tree.seal_if_empty() is not None
        tree.close_after_exit()


@pytest.mark.parametrize("fault", ["assign", "query", "terminate"])
def test_api_failure_never_proves_live_execution_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    tree = WindowsJobTree()
    child = RetainedVaultProcess(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=lambda: tree,
        launch=tree_child_launch,
    )
    identity = None
    try:
        if fault == "assign":
            with monkeypatch.context() as patch:
                patch.setattr(tree._api, "AssignProcessToJobObject", lambda *args: 0)
                with pytest.raises(OSError, match="resource_process_tree_unavailable"):
                    child.spawn()
            assert not child._go_sent
            assert wait_tree_exit(child, None).kind == ExitKind.SUPERVISOR_EXIT
        else:
            identity = child.spawn()
            running = ExecutionStatus(child.ticket, ExecutionState.RUNNING, None, identity, None)
            child.allow_go(running)
            marker = tmp_path / "descendant"
            child.go({"marker": str(marker)})
            assert child.read_result()[0] == b"R"
            assert child._process is not None
            child._process.wait(timeout=5)
            wait_marker(marker)
            with monkeypatch.context() as patch:
                api = "QueryInformationJobObject" if fault == "query" else "TerminateJobObject"
                patch.setattr(tree._api, api, lambda *args: 0)
                child.request_stop()
                assert child.exit_evidence(identity) is None
                with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
                    tree.close_after_exit()
    finally:
        child.request_stop()
        wait_tree_exit(child, identity)
        child.close_pipes_after_worker_exit()
        child.close_tree_after_acknowledgement()


def test_private_job_can_nest_under_existing_host_job() -> None:
    outer, inner = WindowsJobTree(), WindowsJobTree()
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        outer.attach(process)
        inner.attach(process)
        assert outer.seal_if_empty() is inner.seal_if_empty() is None
        inner.request_stop()
        process.wait(timeout=5)
    finally:
        outer.request_stop()
        process.wait(timeout=5)
        assert process.stdin is not None
        process.stdin.close()
        for tree in (inner, outer):
            until = monotonic() + 5
            while tree.seal_if_empty() is None:
                assert monotonic() < until
            tree.close_after_exit()


def test_exact_handle_identity_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = WindowsJobTree()
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        with monkeypatch.context() as patch:
            patch.setattr(process, "pid", process.pid + 1)
            with pytest.raises(ResourceAdmissionError, match="resource_process_tree_identity"):
                tree.attach(process)
        tree.attach(process)
        with pytest.raises(ResourceAdmissionError, match="resource_process_tree_stopped"):
            tree.attach(process)
    finally:
        tree.request_stop()
        process.wait(timeout=5)
        assert process.stdin is not None
        process.stdin.close()
        until = monotonic() + 5
        while tree.seal_if_empty() is None:
            assert monotonic() < until
        tree.close_after_exit()


def test_job_configuration_failure_is_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cast(Any, ctypes).WinDLL
    closed: list[int] = []

    def failed_configuration(*args: Any, **kwargs: Any) -> Any:
        api = original(*args, **kwargs)
        close = api.CloseHandle
        close.argtypes, close.restype = [ctypes.c_void_p], ctypes.c_int

        def close_handle(handle: int) -> int:
            closed.append(handle)
            return int(close(handle))

        monkeypatch.setattr(api, "SetInformationJobObject", lambda *args: 0)
        monkeypatch.setattr(api, "CloseHandle", close_handle)
        return api

    child = RetainedVaultProcess(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=WindowsJobTree,
        launch=tree_child_launch,
    )
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, "WinDLL", failed_configuration)
        with pytest.raises(OSError, match="resource_process_tree_unavailable"):
            child.spawn()
    assert len(closed) == 1 and not child._spawn_attempted
    assert wait_tree_exit(child, None).kind == ExitKind.NOT_STARTED
    child.close_pipes_after_worker_exit()


def test_provider_requires_explicit_containment_and_launcher() -> None:
    supervisor = VaultProcessSupervisor(maximum=1)
    with pytest.raises(ResourceAdmissionError, match="resource_process_tree_unavailable"):
        supervisor.retain(provider_ticket(), ResourceIoDeadline(monotonic()))
    assert not supervisor.snapshot()


def test_ambiguous_provider_creation_cannot_use_empty_job_as_exit_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = WindowsJobTree()
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=lambda: tree,
        launch=tree_child_launch,
    )
    original = subprocess.Popen
    created: list[subprocess.Popen[bytes]] = []

    def interrupted(arguments: list[str], **options: Any) -> subprocess.Popen[bytes]:
        created.append(original(arguments, **options))
        raise OSError("synthetic interruption after OS creation")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", interrupted)
            with pytest.raises(OSError, match="synthetic interruption"):
                child.spawn()
        assert tree.seal_if_empty() is not None
        assert created and created[0].poll() is None
        assert child.exit_evidence(None) is None
        assert supervisor.snapshot() == (child,)
    finally:
        # Only the test retained this handle; the application still has no proof.
        for process in created:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        tree.request_stop()
        assert tree.seal_if_empty() is not None
        tree.close_after_exit()
    assert child.exit_evidence(None) is None
    absent = ExecutionStatus(child.ticket, ExecutionState.ABSENT, None, None, None)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
        supervisor.forget(child, absent)
    assert supervisor.snapshot() == (child,)


def test_failed_job_close_keeps_supervisor_ownership_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = WindowsJobTree()
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=lambda: tree,
        launch=tree_child_launch,
    )
    identity = None
    try:
        identity = child.spawn()
        child.request_stop()
        proof = wait_tree_exit(child, identity)
        closed = ExecutionStatus(child.ticket, ExecutionState.CLOSED, None, identity, None)
        with monkeypatch.context() as patch:
            patch.setattr(tree._api, "CloseHandle", lambda *args: 0)
            with pytest.raises(OSError, match="resource_process_tree_unavailable"):
                supervisor.forget(child, closed)
            assert supervisor.snapshot() == (child,)
            assert child.exit_evidence(identity) == proof
        supervisor.forget(child, closed)
        assert not supervisor.snapshot()
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()
