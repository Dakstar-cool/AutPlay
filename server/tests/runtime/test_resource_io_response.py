"""The full ASGI response, including send backpressure, stays inside I/O authority."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

import pytest
from starlette.responses import StreamingResponse
from starlette.types import Message, Scope

from autplay.entrypoints.resource_io_http import IoScopedResponse
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline
from autplay.runtime.vault_io import VaultIoSession


@dataclass
class Io:
    deadline: ResourceIoDeadline
    finishes: int = 0

    def finish(self) -> None:
        self.finishes += 1
        self.deadline.stop()


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
def test_normal_stream_completion_cancels_its_disconnect_wait_without_revoking_early(
    spec_version: str,
) -> None:
    async def scenario() -> None:
        io = Io(ResourceIoDeadline(0, clock=lambda: 0))
        disconnected = asyncio.Event()
        sent: list[Message] = []

        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            yield b"second"

        async def receive() -> Message:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                disconnected.set()

        response = IoScopedResponse(StreamingResponse(body()), cast(VaultIoSession, io))
        scope: Scope = {"type": "http", "asgi": {"spec_version": spec_version}}
        await response(scope, receive, send)
        assert b"".join(message.get("body", b"") for message in sent) == b"firstsecond"
        assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
        assert io.finishes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
def test_stalled_send_cannot_hold_http_open_or_trigger_more_stream_reads_after_deadline(
    spec_version: str,
) -> None:
    async def scenario() -> None:
        now = [0.0]
        io = Io(ResourceIoDeadline(0, clock=lambda: now[0]))
        blocked, release = asyncio.Event(), asyncio.Event()
        generated: list[bytes] = []
        attempted: list[bytes] = []
        cancelled = asyncio.Event()

        async def body() -> AsyncIterator[bytes]:
            for payload in (b"first", b"second", b"third"):
                generated.append(payload)
                yield payload

        async def receive() -> Message:
            await release.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            payload = message.get("body", b"")
            if payload:
                attempted.append(payload)
                blocked.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    # Simulate transport cleanup that does not finish promptly.
                    await release.wait()

        response = IoScopedResponse(StreamingResponse(body()), cast(VaultIoSession, io))
        scope: Scope = {"type": "http", "asgi": {"spec_version": spec_version}}
        running = asyncio.create_task(response(scope, receive, send))
        try:
            await asyncio.wait_for(blocked.wait(), timeout=2)
            now[0] = 6
            with pytest.raises(IoStopped):
                await asyncio.wait_for(running, timeout=2)
            assert io.finishes == 1
            assert attempted == generated == [b"first"]
            await asyncio.wait_for(cancelled.wait(), timeout=2)
        finally:
            release.set()
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
