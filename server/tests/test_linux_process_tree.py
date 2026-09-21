"""Actual delegated cgroup v2 proof; no process-group or fake-filesystem substitute."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import Any, cast

import pytest
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess, VaultProcessSupervisor
from autplay.adapters.linux_process_tree import LinuxCgroupTree
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ExecutionStatus, ExitKind
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline
from process_tree_support import provider_ticket, tree_child_launch, wait_marker, wait_tree_exit

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="actual Linux cgroup v2 proof")


@pytest.fixture
def root() -> Path:
    value = os.environ.get("AUTPLAY_TEST_CGROUP_ROOT")
    if value is None:
        pytest.skip("explicit delegated test cgroup root is required")
    return Path(value)


def test_root_exit_and_detached_session_do_not_prove_descendant_exit(
    root: Path, tmp_path: Path
) -> None:
    tree = LinuxCgroupTree(root)
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
        assert tree.seal_if_empty() is None and child.exit_evidence(identity) is None
        assert supervisor.snapshot() == (child,)
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, replace(running, state=ExecutionState.ABSENT))
        child.request_stop()
        proof = wait_tree_exit(child, identity)
        assert proof.kind == ExitKind.PROCESS_EXIT and proof.exit_code == 0
        assert child.exit_evidence(identity) == proof
        assert (root / tree._name).is_dir()
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            child.close_tree_after_acknowledgement()
        supervisor.forget(child, replace(running, state=ExecutionState.CLOSED))
        assert not supervisor.snapshot() and not (root / tree._name).exists()
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


@pytest.mark.parametrize("stop_at", ["popen", "attach", "tree_factory"])
def test_stop_during_creation_prevents_late_go(
    root: Path, monkeypatch: pytest.MonkeyPatch, stop_at: str
) -> None:
    tree = LinuxCgroupTree(root)
    entered, resume = Event(), Event()
    original_popen, original_attach = subprocess.Popen, tree.attach

    def barrier() -> None:
        entered.set()
        assert resume.wait(timeout=5)

    def pending_popen(arguments: list[str], **options: Any) -> subprocess.Popen[bytes]:
        barrier()
        return original_popen(arguments, **options)

    def pending_attach(process: subprocess.Popen[bytes]) -> None:
        barrier()
        original_attach(process)

    def factory() -> LinuxCgroupTree:
        if stop_at == "tree_factory":
            barrier()
        return tree

    child = RetainedVaultProcess(
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
        pending = pool.submit(child.spawn)
        try:
            assert entered.wait(timeout=5)
            child.request_stop()
            assert child.exit_evidence(None) is None and not child.seal_without_spawn()
            resume.set()
            with pytest.raises(ResourceAdmissionError):
                pending.result(timeout=5)
            proof = wait_tree_exit(child, None)
            assert proof.kind == (
                ExitKind.NOT_STARTED if stop_at == "tree_factory" else ExitKind.SUPERVISOR_EXIT
            )
            with pytest.raises(IoStopped):
                child.go({"marker": "must-never-be-written"})
        finally:
            resume.set()
            child.request_stop()
            with suppress(ResourceAdmissionError):
                pending.result(timeout=5)
            wait_tree_exit(child, None)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


@pytest.mark.parametrize("action", ["stop", "seal"])
def test_stopped_or_sealed_empty_cgroup_never_accepts_a_process(root: Path, action: str) -> None:
    tree = LinuxCgroupTree(root)
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, env=environment
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


@pytest.mark.parametrize("fault", ["kill", "events_error", "events_invalid"])
def test_failed_control_or_observation_does_not_prove_exit(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    tree = LinuxCgroupTree(root)
    child = RetainedVaultProcess(
        provider_ticket(),
        ResourceIoDeadline(monotonic()),
        tree_factory=lambda: tree,
        launch=tree_child_launch,
    )
    identity = None
    try:
        identity = child.spawn()
        child.allow_go(ExecutionStatus(child.ticket, ExecutionState.RUNNING, None, identity, None))
        marker = tmp_path / "descendant"
        child.go({"marker": str(marker)})
        child.read_result()
        assert child._process is not None and child._process.wait(timeout=5) == 0
        wait_marker(marker)
        with monkeypatch.context() as patch:
            if fault == "kill":

                def denied(name: str, payload: bytes) -> None:
                    raise OSError("synthetic cgroup control unavailable")

                patch.setattr(tree, "_write", denied)
            else:

                def unreadable(name: str) -> bytes:
                    if fault == "events_error":
                        raise OSError("synthetic cgroup observation unavailable")
                    return b"populated 0\npopulated 1\n"

                patch.setattr(tree, "_read", unreadable)
            child.request_stop()
            assert child.exit_evidence(identity) is None
            assert (root / tree._name).is_dir()
    finally:
        child.request_stop()
        wait_tree_exit(child, identity)
        child.close_pipes_after_worker_exit()
        child.close_tree_after_acknowledgement()


def test_ordinary_directory_cannot_impersonate_kernel_cgroup(tmp_path: Path) -> None:
    with pytest.raises(ResourceAdmissionError, match="resource_process_tree_unavailable"):
        LinuxCgroupTree(tmp_path)
    assert not tuple(tmp_path.iterdir())


def test_symlink_delegation_is_rejected(root: Path, tmp_path: Path) -> None:
    link = tmp_path / "delegation"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        LinuxCgroupTree(link)


def test_replaced_cgroup_identity_cannot_supply_empty_evidence(root: Path) -> None:
    tree = LinuxCgroupTree(root)
    path = root / tree._name
    path.rmdir()
    path.mkdir()
    try:
        with pytest.raises(OSError, match="resource_process_tree_identity"):
            tree.seal_if_empty()
        with pytest.raises(OSError, match="resource_process_tree_identity"):
            tree.request_stop()
    finally:
        path.rmdir()
        tree._close_descriptors()


def test_missing_kill_control_fails_before_any_spawn(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open
    before = set(root.iterdir())
    descriptor_count = len(os.listdir("/proc/self/fd"))

    def denied(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "cgroup.kill":
            raise PermissionError("synthetic missing cgroup.kill")
        return original_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", denied)
        with pytest.raises(PermissionError):
            LinuxCgroupTree(root)
    assert set(root.iterdir()) == before
    assert len(os.listdir("/proc/self/fd")) == descriptor_count


def wait_unreaped(process: subprocess.Popen[bytes]) -> None:
    until = monotonic() + 5
    linux = cast(Any, os)
    while (
        linux.waitid(linux.P_PID, process.pid, linux.WEXITED | linux.WNOHANG | linux.WNOWAIT)
        is None
    ):
        assert monotonic() < until
        sleep(0.01)


@pytest.mark.parametrize("reaped", [False, True])
def test_exited_root_is_rejected_without_reaping_it(root: Path, reaped: bool) -> None:
    tree = LinuxCgroupTree(root)
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
    try:
        if reaped:
            assert process.wait(timeout=5) == 0
        else:
            wait_unreaped(process)
        with pytest.raises(ResourceAdmissionError, match="resource_process_tree_identity"):
            tree.attach(process)
        if not reaped:
            assert process.returncode is None
            # The adapter's waitid did not consume the root's wait status.
            wait_unreaped(process)
    finally:
        process.wait(timeout=5)
        assert tree.seal_if_empty() is not None
        tree.close_after_exit()


def test_busy_reaper_cannot_deadlock_attach_or_stop(root: Path) -> None:
    tree = LinuxCgroupTree(root)
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, env=environment
    )
    try:
        with cast(Any, process)._waitpid_lock:
            with pytest.raises(ResourceAdmissionError, match="resource_process_tree_busy"):
                tree.attach(process)
            tree.request_stop()
    finally:
        process.kill()
        process.wait(timeout=5)
        assert process.stdin is not None
        process.stdin.close()
        assert tree.seal_if_empty() is not None
        tree.close_after_exit()


def test_attachment_keeps_exited_pid_unreaped_until_numeric_write_finishes(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = LinuxCgroupTree(root)
    arguments, environment = tree_child_launch()
    process = subprocess.Popen(
        arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, env=environment
    )
    entered, resume, waiter_started = Event(), Event(), Event()
    original_write = tree._write

    def blocked_write(name: str, payload: bytes) -> None:
        entered.set()
        assert resume.wait(timeout=5)
        # Both competing Popen reapers still leave this exact PID unreaped at
        # the numeric write boundary, even though the process has exited.
        assert process.returncode is None
        wait_unreaped(process)
        original_write(name, payload)

    def wait() -> int:
        waiter_started.set()
        return process.wait(timeout=5)

    monkeypatch.setattr(tree, "_write", blocked_write)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            attaching = pool.submit(tree.attach, process)
            try:
                assert entered.wait(timeout=5)
                process.kill()
                wait_unreaped(process)
                reaper = pool.submit(wait)
                assert waiter_started.wait(timeout=5)
                assert process.poll() is None and not reaper.done()
                resume.set()
                # Kernels may accept an unreaped exiting task or reject its
                # migration. Neither outcome permits PID reuse during attach.
                with suppress(ProcessLookupError):
                    attaching.result(timeout=5)
                assert reaper.result(timeout=5) < 0
            finally:
                resume.set()
    finally:
        process.kill()
        process.wait(timeout=5)
        assert process.stdin is not None
        process.stdin.close()
        assert tree.seal_if_empty() is not None
        tree.close_after_exit()
