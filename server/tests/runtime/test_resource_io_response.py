"""Complete ASGI ownership, diagnostic sends and disconnect races."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

import pytest
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from autplay.runtime.resource_io_scope import TRANSPORT_EXTENSION, ResourceIoScope
from autplay.runtime.vault_io import VaultIoSession
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Message, Receive, Scope, Send


@dataclass
class Io:
    deadline: ResourceIoDeadline
    finishes: int = 0

    def finish(self) -> None:
        self.finishes += 1
        self.deadline.stop(preserve_error=True)


class Wire:
    def __init__(self, version: str) -> None:
        self.disconnected = asyncio.Event()
        self.aborted = False
        self.readers = self.maximum_readers = self.reads = 0
        self.sent: list[Message] = []
        self.scope: Scope = {
            "type": "http",
            "asgi": {"spec_version": version},
            "extensions": {TRANSPORT_EXTENSION: {"abort": self.abort}},
        }

    def abort(self) -> None:
        self.aborted = True

    async def receive(self) -> Message:
        self.readers += 1
        self.reads += 1
        self.maximum_readers = max(self.maximum_readers, self.readers)
        try:
            if self.reads == 1:
                return {"type": "http.request", "body": b"body", "more_body": False}
            await self.disconnected.wait()
            return {"type": "http.disconnect"}
        finally:
            self.readers -= 1

    async def send(self, message: Message) -> None:
        assert not self.aborted
        self.sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            self.disconnected.set()


@pytest.mark.parametrize("version", ["2.0", "2.4"])
def test_normal_stream_completion_does_not_abort_connection(version: str) -> None:
    async def scenario() -> None:
        wire = Wire(version)
        io = Io(ResourceIoDeadline(0, clock=lambda: 0))
        boundary = ResourceIoScope(wire.scope, wire.receive, wire.send)

        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            yield b"second"

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            boundary.bind(cast(VaultIoSession, io))
            await StreamingResponse(body())(scope, receive, send)

        await boundary.run(app)
        assert b"".join(item.get("body", b"") for item in wire.sent) == b"firstsecond"
        assert not wire.aborted and io.deadline.stopped()
        assert wire.maximum_readers == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("version", ["2.0", "2.4"])
@pytest.mark.parametrize("phase", ["open", "read", "upload_worker"])
def test_disconnect_stops_blocked_work_with_one_receive_owner(version: str, phase: str) -> None:
    async def scenario() -> None:
        wire = Wire(version)
        io = Io(ResourceIoDeadline(0, clock=lambda: 0))
        boundary = ResourceIoScope(wire.scope, wire.receive, wire.send)
        entered = asyncio.Event()

        async def blocked() -> None:
            entered.set()
            await asyncio.Event().wait()

        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            await blocked()
            yield b"forbidden"

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if phase != "upload_worker":
                await receive()
            boundary.bind(cast(VaultIoSession, io))
            if phase == "upload_worker":
                await receive()
            if phase != "read":
                await blocked()
            await StreamingResponse(body())(scope, receive, send)

        running = asyncio.create_task(boundary.run(app))
        await asyncio.wait_for(entered.wait(), 1)
        wire.disconnected.set()
        await asyncio.wait_for(running, 0.5)
        assert io.deadline.stopped() and wire.aborted
        assert wire.maximum_readers == 1
        assert b"forbidden" not in b"".join(item.get("body", b"") for item in wire.sent)

    asyncio.run(scenario())


@pytest.mark.parametrize("diagnostic", [False, True])
def test_stalled_send_and_delayed_cancellation_cannot_restart_output(diagnostic: bool) -> None:
    async def scenario() -> None:
        now = [0.0]
        wire = Wire("2.4")
        io = Io(ResourceIoDeadline(0, clock=lambda: now[0]))
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        attempted: list[Message] = []
        generated: list[bytes] = []

        async def stalled_send(message: Message) -> None:
            attempted.append(message)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()

        boundary = ResourceIoScope(wire.scope, wire.receive, stalled_send)

        async def body() -> AsyncIterator[bytes]:
            generated.append(b"payload")
            yield b"payload"

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            boundary.bind(cast(VaultIoSession, io))
            if diagnostic:
                io.deadline.freeze_error()
                io.finish()
                await JSONResponse({"error": "storage_unavailable"}, status_code=503)(
                    scope, receive, send
                )
            else:
                await StreamingResponse(body())(scope, receive, send)

        running = asyncio.create_task(boundary.run(app))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            now[0] = 6
            await asyncio.wait_for(running, 0.5)
            await asyncio.wait_for(cancelled.wait(), 0.5)
            assert wire.aborted and len(attempted) == 1 and not generated
        finally:
            release.set()
            await asyncio.sleep(0.05)
        assert len(attempted) == 1

    asyncio.run(scenario())


def test_early_error_uses_frozen_expiry_without_resuming_worker_or_receive() -> None:
    async def scenario() -> None:
        now = [2.0]
        wire = Wire("2.4")
        io = Io(ResourceIoDeadline(0, clock=lambda: now[0]))
        renewal = io.deadline.begin_renewal()
        boundary = ResourceIoScope(wire.scope, wire.receive, wire.send)

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            boundary.bind(cast(VaultIoSession, io))
            io.deadline.freeze_error()
            io.finish()
            assert not io.deadline.finish_renewal(renewal, succeeded=True)
            now[0] = 4
            assert io.deadline.remaining() == 0
            assert io.deadline.response_remaining() == 1
            await JSONResponse({"error": "chunk_invalid"}, status_code=422)(scope, receive, send)

        await boundary.run(app)
        assert wire.reads == 0 and not wire.aborted
        assert wire.sent[0]["status"] == 422

    asyncio.run(scenario())


def test_transport_disconnect_during_admission_never_reads_upload_body() -> None:
    async def scenario() -> None:
        wire = Wire("2.4")
        signal = asyncio.Event()
        wire.scope["extensions"][TRANSPORT_EXTENSION]["disconnected"] = signal
        io = Io(ResourceIoDeadline(0, clock=lambda: 0))
        boundary = ResourceIoScope(wire.scope, wire.receive, wire.send)
        entered = asyncio.Event()

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            boundary.bind(cast(VaultIoSession, io))
            entered.set()
            await asyncio.Event().wait()

        running = asyncio.create_task(boundary.run(app))
        await entered.wait()
        signal.set()
        await asyncio.wait_for(running, 0.5)
        assert wire.reads == 0 and not wire.sent and wire.aborted
        assert io.deadline.response_remaining() == 0

    asyncio.run(scenario())


def test_missing_transport_cannot_bind_admission() -> None:
    async def scenario() -> None:
        wire = Wire("2.4")
        wire.scope["extensions"] = {}
        boundary = ResourceIoScope(wire.scope, wire.receive, wire.send)
        with pytest.raises(ResourceAdmissionError, match="resource_service_unavailable"):
            boundary.bind(cast(VaultIoSession, Io(ResourceIoDeadline(0, clock=lambda: 0))))
        assert boundary.io is None and wire.reads == 0

    asyncio.run(scenario())


def test_concurrent_control_failure_preserves_diagnostic_when_body_receive_finishes() -> None:
    async def scenario() -> None:
        from autplay.runtime.resource_io_deadline import IoStopped

        wire = Wire("2.4")
        io = Io(ResourceIoDeadline(0, clock=lambda: 0))
        reads = 0

        async def receive_then_fail() -> Message:
            nonlocal reads
            reads += 1
            io.deadline.freeze_error()
            io.finish()
            return {"type": "http.request", "body": b"not-authorized", "more_body": False}

        boundary = ResourceIoScope(wire.scope, receive_then_fail, wire.send)

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            boundary.bind(cast(VaultIoSession, io))
            with pytest.raises(IoStopped):
                await receive()
            assert io.deadline.response_remaining() == 5
            await JSONResponse({"error": "unavailable"}, status_code=503)(scope, receive, send)

        await boundary.run(app)
        assert reads == 1  # The scheduled EOF listener cannot start another raw receive.
        assert wire.sent[0]["status"] == 503 and not wire.aborted

    asyncio.run(scenario())
