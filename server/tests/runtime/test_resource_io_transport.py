"""Real TCP regressions for Uvicorn fallback writes and preadmission disconnect."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import cast

import pytest
import uvicorn
from autplay.runtime.http import ApiError, RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from autplay.runtime.resource_io_scope import (
    TRANSPORT_EXTENSION,
    ResourceIoFastAPI,
    resource_io_scope,
)
from autplay.runtime.resource_io_transport import ResourceIoH11Protocol
from autplay.runtime.vault_io import VaultIoSession
from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .test_resource_io_response import Io


@asynccontextmanager
async def tcp_server(app: ASGIApp) -> AsyncIterator[tuple[str, int]]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    address = sock.getsockname()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            http=ResourceIoH11Protocol,
            lifespan="off",
            log_level="critical",
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=1,
        )
    )
    running = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(3):
            while not server.started:
                if running.done():
                    running.result()
                await asyncio.sleep(0.01)
        yield address[0], address[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(running, 3)
        sock.close()


async def closed_bytes(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.read(), 2)
    except ConnectionResetError:
        return b""


@pytest.mark.parametrize("ending", ["return", "exception", "cancel"])
def test_protocol_abort_suppresses_uvicorn_fallback500(ending: str) -> None:
    async def scenario() -> None:
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            scope["extensions"][TRANSPORT_EXTENSION]["abort"]()
            if ending == "exception":
                raise RuntimeError("synthetic failure")
            if ending == "cancel":
                raise asyncio.CancelledError

        async with tcp_server(app) as address:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert await closed_bytes(reader) == b""
            writer.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error_handler", "empty_return"])
def test_full_fastapi_deadline_prevents_late_diagnostic_and_fallback(failure: str) -> None:
    async def scenario() -> None:
        app = ResourceIoFastAPI()
        install_error_handlers(app)
        app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
        io = Io(ResourceIoDeadline(monotonic() - 4.8))
        attempted = asyncio.Event()

        @app.get("/resource")
        async def resource(request: Request) -> None:
            resource_io_scope(request.scope).bind(cast(VaultIoSession, io))
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                # Deliberately try to return through the normal FastAPI handlers.
                attempted.set()
                if failure == "error_handler":
                    raise ApiError("synthetic_unavailable", "Unavailable.", 503) from None

        async with tcp_server(app) as address:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"GET /resource HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert await closed_bytes(reader) == b""
            await asyncio.wait_for(attempted.wait(), 1)
            assert io.deadline.response_remaining() == 0
            writer.close()

    asyncio.run(scenario())


def test_early_typed_error_preserves_keepalive_and_unadmitted_requests() -> None:
    async def scenario() -> None:
        app = ResourceIoFastAPI()
        install_error_handlers(app)
        app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())

        @app.get("/resource")
        async def resource(request: Request) -> None:
            io = Io(ResourceIoDeadline(monotonic()))
            resource_io_scope(request.scope).bind(cast(VaultIoSession, io))
            io.deadline.freeze_error()
            io.finish()
            raise ApiError("synthetic_unavailable", "Unavailable.", 503)

        @app.get("/healthy")
        async def healthy() -> dict[str, bool]:
            return {"ok": True}

        async with tcp_server(app) as address:
            reader, writer = await asyncio.open_connection(*address)
            for path, status in (("/resource", b"503"), ("/healthy", b"200")):
                writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                await writer.drain()
                headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
                assert headers.split(b"\r\n", 1)[0].split()[1] == status
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.lower().split(b"\r\n")
                    if line.startswith(b"content-length:")
                )
                await asyncio.wait_for(reader.readexactly(length), 1)
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


def test_expect_continue_disconnect_before_admission_never_consumes_body() -> None:
    async def scenario() -> None:
        app = ResourceIoFastAPI()
        io = Io(ResourceIoDeadline(monotonic()))
        entered = asyncio.Event()

        @app.patch("/upload")
        async def upload(request: Request) -> None:
            resource_io_scope(request.scope).bind(cast(VaultIoSession, io))
            entered.set()
            await asyncio.Event().wait()

        async with tcp_server(app) as address:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(
                b"PATCH /upload HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 99\r\nExpect: 100-continue\r\n\r\n"
            )
            await writer.drain()
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(reader.read(1), 0.1)
            writer.close()
            await writer.wait_closed()
            async with asyncio.timeout(0.5):
                while not io.deadline.stopped():
                    await asyncio.sleep(0.01)
            assert io.deadline.response_remaining() == 0

    asyncio.run(scenario())


def test_completed_response_deadline_does_not_abort_next_keepalive_request() -> None:
    async def scenario() -> None:
        from starlette.background import BackgroundTask
        from starlette.responses import PlainTextResponse
        from uvicorn.protocols.http.h11_impl import RequestResponseCycle

        app = ResourceIoFastAPI()
        second_cycle: RequestResponseCycle | None = None
        paused = asyncio.Event()

        @app.get("/first")
        async def first(request: Request) -> PlainTextResponse:
            io = Io(ResourceIoDeadline(monotonic() - 4.8))
            resource_io_scope(request.scope).bind(cast(VaultIoSession, io))
            return PlainTextResponse("ok", background=BackgroundTask(asyncio.sleep, 0.5))

        @app.get("/second")
        async def second() -> PlainTextResponse:
            return PlainTextResponse("next")

        async def wire(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal second_cycle
            if scope["path"] == "/second":
                cycle = getattr(receive, "__self__", None)
                assert isinstance(cycle, RequestResponseCycle)
                second_cycle = cycle
                cycle.flow.pause_writing()
                paused.set()
            await app(scope, receive, send)

        async with tcp_server(wire) as address:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert b"200" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
            assert await reader.readexactly(2) == b"ok"
            writer.write(b"GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(paused.wait(), 1)
            with pytest.raises(TimeoutError):
                # Old response expiry must not abort OR resume this connection's
                # flow control, which now belongs to the second request.
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 0.35)
            assert second_cycle is not None
            second_cycle.flow.resume_writing()
            assert b"200" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
            assert await reader.readexactly(4) == b"next"
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_paused_uvicorn_send_cannot_write_after_deadline(phase: str) -> None:
    async def scenario() -> None:

        from starlette.responses import StreamingResponse
        from uvicorn.protocols.http.h11_impl import RequestResponseCycle

        app = ResourceIoFastAPI()
        paused = asyncio.Event()
        late_payload = b"LATE-SENTINEL"

        @app.get("/resource")
        async def resource(request: Request) -> StreamingResponse:
            io = Io(ResourceIoDeadline(monotonic() - 4.8))
            resource_io_scope(request.scope).bind(cast(VaultIoSession, io))

            async def body() -> AsyncIterator[bytes]:
                yield late_payload
                yield b"FORBIDDEN-NEXT"

            return StreamingResponse(body())

        async def wire(scope: Scope, receive: Receive, send: Send) -> None:
            cycle = getattr(receive, "__self__", None)
            assert isinstance(cycle, RequestResponseCycle)

            async def paused_send(message: Message) -> None:
                if (phase == "headers" and message["type"] == "http.response.start") or (
                    phase == "body" and message["type"] == "http.response.body"
                ):
                    cycle.flow.pause_writing()
                    paused.set()
                await send(message)

            await app(scope, receive, paused_send)

        async with tcp_server(wire) as address:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"GET /resource HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(paused.wait(), 1)
            received = await closed_bytes(reader)
            assert late_payload not in received and b"FORBIDDEN-NEXT" not in received
            assert b"500" not in received
            if phase == "headers":
                assert received == b""
            else:
                assert b"200" in received
            writer.close()

    asyncio.run(scenario())
