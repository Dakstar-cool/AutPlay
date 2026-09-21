"""Own the entire admitted ASGI call, including framework error responses."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.resource_io_deadline import IoStopped

if TYPE_CHECKING:
    from autplay.runtime.vault_io import VaultIoSession

T = TypeVar("T")
_SCOPE_KEY = "autplay.resource_io_scope"
TRANSPORT_EXTENSION = "autplay.resource_io_transport"
_ERROR_BYTES = 4096
_ERROR_HEADERS = 8192


def resource_io_scope(scope: Scope) -> ResourceIoScope:
    value = scope.get(_SCOPE_KEY)
    if not isinstance(value, ResourceIoScope):
        raise RuntimeError("admitted routes require the resource I/O ASGI boundary")
    return value


class ResourceIoScope:
    """One raw receive owner; after the body only that owner watches disconnect.

    Before admission upload receive remains demand driven. The protocol extension
    observes a disconnect without consuming unauthorized bytes or sending 100 Continue.
    A completed body allows a background receive even during admission/worker waits.
    """

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scope, self._receive, self._send = scope, receive, send
        self.io: VaultIoSession | None = None
        self._bound = asyncio.Event()
        self._abort_signal = asyncio.Event()
        self._receive_lock = asyncio.Lock()
        self._body_complete = False
        self._response_started = self._response_complete = False
        self._diagnostic = False
        self._finished = False
        self._listener: asyncio.Task[None] | None = None
        self._transport_listener: asyncio.Task[None] | None = None
        self._settling: set[asyncio.Task[object]] = set()

    def bind(self, io: VaultIoSession) -> None:
        if self.io is not None or self._finished or self._abort_signal.is_set():
            raise IoStopped()
        extension = self.scope.get("extensions", {}).get(TRANSPORT_EXTENSION, {})
        if not callable(extension.get("abort")):
            raise ResourceAdmissionError("resource_service_unavailable")
        self.io = io
        self._bound.set()
        self._listen_after_body()
        disconnected = extension.get("disconnected")
        if isinstance(disconnected, asyncio.Event):

            async def listen_transport() -> None:
                await disconnected.wait()
                if not self._response_complete:
                    self.abort()

            self._transport_listener = asyncio.create_task(listen_transport())
            if disconnected.is_set():
                self.abort()
                raise IoStopped()

    def abort(self) -> None:
        if self._abort_signal.is_set():
            return
        self._abort_signal.set()
        if self.io is not None:
            self.io.deadline.stop()
            self.io.finish()
        extension = self.scope.get("extensions", {}).get(TRANSPORT_EXTENSION, {})
        abort = extension.get("abort")
        if callable(abort):
            abort()

    def _remaining(self, *, diagnostic: bool = False) -> float:
        if self._abort_signal.is_set() or self._finished:
            raise IoStopped()
        if self.io is None:
            return 1.0
        remaining = (
            self.io.deadline.response_remaining() if diagnostic else self.io.deadline.remaining()
        )
        if remaining <= 0:
            # A control/worker failure stops body/work immediately but can leave
            # a frozen diagnostic window. A losing receive must not erase it.
            if diagnostic or self.io.deadline.response_remaining() <= 0:
                self.abort()
            raise IoStopped()
        return remaining

    def _cancel(self, task: asyncio.Task[object]) -> None:
        if not task.done():
            task.cancel()
        self._settling.add(task)

        def settled(done: asyncio.Task[object]) -> None:
            self._settling.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(settled)

    async def _bounded(self, action: Callable[[], Awaitable[T]], *, diagnostic: bool = False) -> T:
        self._remaining(diagnostic=diagnostic)

        async def invoke() -> T:
            self._remaining(diagnostic=diagnostic)
            return await action()

        task = asyncio.create_task(invoke())
        try:
            while True:
                remaining = self._remaining(diagnostic=diagnostic)
                done, _ = await asyncio.wait((task,), timeout=min(0.05, remaining))
                self._remaining(diagnostic=diagnostic)
                if done:
                    return task.result()
        finally:
            self._cancel(task)  # Never wait for transport/application cleanup to settle.

    def _listen_after_body(self) -> None:
        if self.io is not None and self._body_complete and self._listener is None:
            self._listener = asyncio.create_task(self._listen_disconnect())

    async def _listen_disconnect(self) -> None:
        try:
            async with self._receive_lock:
                if self.io is None or self.io.deadline.stopped():
                    return
                message = await self._receive()
            if self._response_complete or self._finished:
                return
            # No request frame is valid after the terminal body frame.
            if message["type"] != "http.disconnect":
                raise RuntimeError("unexpected ASGI message after request body")
            self.abort()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.abort()

    async def receive(self) -> Message:
        if self.io is not None and self._body_complete:
            # Starlette < ASGI 2.4 may itself listen; never give it the raw channel.
            await self._abort_signal.wait()
            return {"type": "http.disconnect"}

        async def read() -> Message:
            async with self._receive_lock:
                if self.io is not None:
                    self._remaining()
                message = await self._receive()
                if message["type"] == "http.disconnect":
                    self.abort()
                elif message["type"] == "http.request" and not message.get("more_body", False):
                    self._body_complete = True
                    self._listen_after_body()
                return message

        if self.io is None:
            return await read()
        return await self._bounded(read)

    async def send(self, message: Message) -> None:
        if self.io is None:
            await self._send(message)
            return
        if self._response_complete:
            self.abort()
            raise IoStopped()
        if message["type"] == "http.response.start":
            if self._response_started:
                self.abort()
                raise IoStopped()
            self._diagnostic = 400 <= int(message["status"]) <= 599
            if self._diagnostic:
                headers = message.get("headers", [])
                if sum(len(key) + len(value) for key, value in headers) > _ERROR_HEADERS:
                    self.abort()
                    raise IoStopped()
                self.io.deadline.freeze_error()
                self.io.finish()
            self._response_started = True
        elif (
            message["type"] != "http.response.body"
            or not self._response_started
            or (
                self._diagnostic
                and (
                    message.get("more_body", False) or len(message.get("body", b"")) > _ERROR_BYTES
                )
            )
        ):
            self.abort()
            raise IoStopped()

        async def deliver() -> None:
            await self._send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self._response_complete = True

        try:
            await self._bounded(deliver, diagnostic=self._diagnostic)
        except OSError, asyncio.CancelledError:
            self.abort()
            raise

    async def run(self, app: ASGIApp) -> None:
        self.scope[_SCOPE_KEY] = self

        async def invoke() -> None:
            await app(self.scope, self.receive, self.send)

        running = asyncio.create_task(invoke())
        binding = asyncio.create_task(self._bound.wait())
        try:
            await asyncio.wait((running, binding), return_when=asyncio.FIRST_COMPLETED)
            while not running.done():
                remaining = self._remaining(diagnostic=True)
                await asyncio.wait((running,), timeout=min(0.05, remaining))
            running.result()
            if self.io is not None and not self._response_complete:
                self.abort()
        except asyncio.CancelledError:
            self.abort()
            raise
        except BaseException:
            if self.io is None:
                raise
            if not self._response_complete:
                self.abort()
            # The exact protocol cycle was aborted synchronously. Returning cannot
            # invite Uvicorn's fallback500 or permit a delayed send to write bytes.
        finally:
            self._finished = True
            for task in (running, binding, self._listener, self._transport_listener):
                if task is not None:
                    self._cancel(task)
            if self.io is not None:
                self.io.finish()


class ResourceIoFastAPI(FastAPI):
    """Place the deadline boundary outside FastAPI's complete middleware stack."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await ResourceIoScope(scope, receive, send).run(super().__call__)
        else:
            await super().__call__(scope, receive, send)
