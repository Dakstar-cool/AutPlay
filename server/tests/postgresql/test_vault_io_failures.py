"""Launch gates and exact receipts under process-supervision failures."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

import pytest

from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.resource_admission import ActivationFence, IoPermit, ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionStatus,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline
from autplay.runtime.vault_io import VaultIoCoordinator

from .test_resource_admission_runtime import AdmissionHarness, admission
from .test_vault_io_coordinator import assert_closed, setup

__all__ = ["admission"]


@pytest.mark.parametrize("phase", ["watch", "control", "worker"])
@pytest.mark.parametrize("after_os_start", [False, True])
def test_failed_thread_launch_cannot_authorize_work_or_leak_a_reservation(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    after_os_start: bool,
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    original_start = threading.Thread.start
    affected: list[threading.Thread] = []

    def failed_start(thread: threading.Thread) -> None:
        if thread.name != f"vault-io-{phase}":
            original_start(thread)
            return
        if after_os_start:
            original_start(thread)
            affected.append(thread)
        raise RuntimeError("synthetic thread start failure")

    async def scenario() -> None:
        try:
            with monkeypatch.context() as patch:
                patch.setattr(threading.Thread, "start", failed_start)
                if phase == "watch":
                    other = VaultIoCoordinator(admission.service, maximum=1)
                    with pytest.raises(RuntimeError, match="synthetic thread start failure"):
                        other.start()
                    with pytest.raises(ResourceAdmissionError, match="resource_execution_busy"):
                        await other.open(
                            actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
                        )
                else:
                    with pytest.raises(RuntimeError, match="synthetic thread start failure"):
                        await coordinator.open(
                            actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
                        )
            if phase == "watch":
                other.start()
                assert not await other.shutdown()
        finally:
            assert not await coordinator.shutdown()
            for thread in affected:
                thread.join(timeout=1)
                assert not thread.is_alive()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


def test_pending_hello_cannot_extend_the_initial_five_second_startup_deadline(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    original_spawn = RetainedVaultProcess.spawn
    release = threading.Event()

    def delayed_hello(child: RetainedVaultProcess) -> ProcessIdentity:
        identity = original_spawn(child)
        assert release.wait(timeout=10)
        return identity

    monkeypatch.setattr(RetainedVaultProcess, "spawn", delayed_hello)

    async def scenario() -> None:
        began = monotonic()
        try:
            with pytest.raises(IoStopped):
                await coordinator.open(
                    actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
                )
            assert 4.5 <= monotonic() - began <= 6
            assert coordinator.pending()
        finally:
            release.set()
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


@pytest.mark.parametrize("phase", ["confirm", "close"])
def test_lost_cleanup_reply_reconciles_exact_commit_without_releasing_early(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    original_confirm = ResourceAdmissionService.confirm_execution_exit
    original_close = ResourceAdmissionService.close_io
    lost = False

    def lost_confirm(
        service: ResourceAdmissionService, ticket: ExecutionTicket, proof: ProcessExitEvidence
    ) -> ExecutionStatus:
        nonlocal lost
        result = original_confirm(service, ticket, proof)
        if not lost:
            lost = True
            raise ConnectionError("synthetic lost confirm reply")
        return result

    def lost_close(service: ResourceAdmissionService, permit: IoPermit) -> None:
        nonlocal lost
        original_close(service, permit)
        if not lost:
            lost = True
            raise ConnectionError("synthetic lost close reply")

    if phase == "confirm":
        monkeypatch.setattr(ResourceAdmissionService, "confirm_execution_exit", lost_confirm)
    else:
        monkeypatch.setattr(ResourceAdmissionService, "close_io", lost_close)

    async def scenario() -> None:
        try:
            io = await coordinator.open(
                actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
            )
            io.finish()
        finally:
            assert not await coordinator.shutdown()
        assert lost

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


def test_sealed_never_spawned_child_cannot_later_run_a_queued_spawn(
    admission: AdmissionHarness,
) -> None:
    coordinator = VaultIoCoordinator(admission.service, maximum=1)
    # No filesystem or database operation is involved in this atomic local seal.
    permit = IoPermit(uuid4(), ActivationFence(uuid4(), uuid4(), 1), uuid4(), datetime.now(UTC))
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.VAULT_UPLOAD, permit.target_id)
    child = coordinator.supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    assert child.seal_without_spawn()
    assert child.seal_without_spawn()
    with pytest.raises(ResourceAdmissionError, match="resource_execution_conflict"):
        child.spawn()
    proof = child.exit_evidence(None)
    assert proof is not None and proof.kind == ExitKind.NOT_STARTED
