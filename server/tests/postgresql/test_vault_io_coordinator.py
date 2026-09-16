"""Actual PostgreSQL and child-process ownership across HTTP cancellation and lost replies."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process_upload import ProcessVaultChunkWriter
from autplay.adapters.postgresql.models import UploadSessionRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoPermitRow
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    ActivationFence,
    IoPermit,
    ResourceKind,
    ResourceRequest,
)
from autplay.domain.resource_execution import ExecutionStatus, ExecutionTicket, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey, VaultLimits
from autplay.runtime.resource_io_deadline import IoStopped
from autplay.runtime.vault_io import VaultIoCoordinator

from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_upload_process import Upload, append

__all__ = ["admission"]


def setup(harness: AdmissionHarness) -> tuple[VaultIoCoordinator, Principal, ActivationFence, UUID]:
    harness.budget()
    actor = harness.actor()
    upload_id = harness.upload(actor)
    active = harness.service.acquire(
        actor, ResourceRequest(uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", uuid4(), upload_id)
    )
    coordinator = VaultIoCoordinator(harness.service, maximum=1)
    coordinator.start()
    return coordinator, actor, fence(active), upload_id


async def eventually(condition: Callable[[], bool], *, timeout: float = 5) -> None:
    until = monotonic() + timeout
    while not condition():
        assert monotonic() < until, "owned coordinator did not settle in test bound"
        await asyncio.sleep(0.02)


def assert_closed(harness: AdmissionHarness, coordinator: VaultIoCoordinator) -> None:
    assert not coordinator.pending()
    assert not coordinator.supervisor.snapshot()
    with harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0


def test_coordinator_owns_real_upload_transaction_and_confirmed_cleanup(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(OpaqueStorageKey(upload_id.hex))

    async def scenario() -> None:
        try:
            io = await coordinator.open(
                actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
            )
            upload = Upload(
                actor,
                io.child.ticket,
                io.child,
                io.registered,
                coordinator.supervisor,
                storage,
                ProcessVaultChunkWriter(
                    io.child, io.registered, root=tmp_path, limits=VaultLimits()
                ),
            )
            assert not await io.perform(lambda: append(admission, upload))
            assert await io.perform(lambda: append(admission, upload))
            io.finish()
        finally:
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


def test_cancelled_http_waiter_cannot_release_a_still_running_upload_transaction(
    admission: AdmissionHarness,
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    entered, allow_exit = threading.Event(), threading.Event()

    async def scenario() -> None:
        try:
            io = await coordinator.open(
                actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
            )

            def held_transaction() -> None:
                with admission.sessions.begin() as session:
                    row = present(session.get(UploadSessionRow, upload_id, with_for_update=True))
                    row.received_size = 1
                    entered.set()
                    assert allow_exit.wait(timeout=10)
                    io.deadline.check()

            waiter = asyncio.create_task(io.perform(held_transaction))
            await eventually(entered.is_set)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert await coordinator.shutdown(timeout=0)
            admission.service.release(actor, active)
            assert admission.service.poll(actor, active.operation_id).usage.server == 1
            with pytest.raises(DBAPIError), admission.sessions.begin() as contender:
                contender.get(UploadSessionRow, upload_id, with_for_update={"nowait": True})
            assert coordinator.pending()
        finally:
            allow_exit.set()
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload_id)).received_size == 0


@pytest.mark.parametrize("phase", ["prepare", "start"])
def test_lost_registration_reply_stops_and_reconciles_without_go(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    original_prepare = ResourceAdmissionService.prepare_execution
    original_start = ResourceAdmissionService.start_execution

    def lost_prepare(
        service: ResourceAdmissionService, principal: Principal, ticket: ExecutionTicket
    ) -> ExecutionStatus:
        original_prepare(service, principal, ticket)
        raise ConnectionError("synthetic lost prepare reply")

    def lost_start(
        service: ResourceAdmissionService,
        principal: Principal,
        ticket: ExecutionTicket,
        identity: ProcessIdentity,
    ) -> ExecutionStatus:
        original_start(service, principal, ticket, identity)
        raise ConnectionError("synthetic lost start reply")

    if phase == "prepare":
        monkeypatch.setattr(ResourceAdmissionService, "prepare_execution", lost_prepare)
    else:
        monkeypatch.setattr(ResourceAdmissionService, "start_execution", lost_start)

    async def scenario() -> None:
        try:
            with pytest.raises(ConnectionError):
                await coordinator.open(
                    actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
                )
        finally:
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


def test_late_open_reply_stays_owned_after_http_timeout_and_local_shutdown(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, actor, active, upload_id = setup(admission)
    entered, allow_reply = threading.Event(), threading.Event()
    original = ResourceAdmissionService.open_io

    def delayed(
        service: ResourceAdmissionService,
        principal: Principal,
        activation: ActivationFence,
        target: UUID,
        *,
        resource_type: str | None = None,
    ) -> IoPermit:
        result = original(service, principal, activation, target, resource_type=resource_type)
        entered.set()
        assert allow_reply.wait(timeout=10)
        return result

    monkeypatch.setattr(ResourceAdmissionService, "open_io", delayed)

    async def scenario() -> None:
        try:
            opening = asyncio.create_task(
                coordinator.open(actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id)
            )
            await eventually(entered.is_set)
            reservation = coordinator.pending()[0]
            reservation.deadline.stop()
            with pytest.raises(IoStopped):
                await opening
            assert await coordinator.shutdown(timeout=0) == (reservation.identifier,)
            assert not coordinator.supervisor.snapshot()
        finally:
            allow_reply.set()
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)


def test_atomic_renewal_refreshes_permission_and_execution_heartbeat(
    admission: AdmissionHarness,
) -> None:
    coordinator, actor, active, upload_id = setup(admission)

    async def scenario() -> None:
        try:
            io = await coordinator.open(
                actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
            )
            initial = io.registered
            await asyncio.sleep(2.5)
            current = admission.service.inspect_execution(io.child.ticket)
            assert current.heartbeat_at is not None and initial.heartbeat_at is not None
            assert current.heartbeat_at > initial.heartbeat_at
            with admission.sessions() as session:
                permit = present(session.get(ResourceIoPermitRow, io.child.ticket.permit.permit_id))
                assert permit.expires_at > io.child.ticket.permit.expires_at
            assert not io.deadline.stopped()
        finally:
            assert not await coordinator.shutdown()

    asyncio.run(scenario())
    assert_closed(admission, coordinator)
