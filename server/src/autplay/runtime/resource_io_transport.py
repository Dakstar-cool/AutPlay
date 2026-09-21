"""Pinned Uvicorn h11 seam for exact-request transport abort without fallback writes."""

from __future__ import annotations

import asyncio
from typing import Any

from uvicorn._types import ASGIReceiveCallable, ASGISendCallable, Scope
from uvicorn.config import Config
from uvicorn.protocols.http.h11_impl import H11Protocol, RequestResponseCycle
from uvicorn.server import ServerState

from autplay.runtime.resource_io_scope import TRANSPORT_EXTENSION


class ResourceIoH11Protocol(H11Protocol):
    """Use explicitly, never auto-select a backend with different cycle ownership."""

    def __init__(
        self,
        config: Config,
        server_state: ServerState,
        app_state: dict[str, Any],
        _loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__(config, server_state, app_state, _loop)
        self._disconnected = asyncio.Event()
        application = self.app

        async def admitted_transport(
            scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
        ) -> None:
            if scope["type"] == "http":
                cycle = getattr(receive, "__self__", None)
                if not isinstance(cycle, RequestResponseCycle) or cycle.scope is not scope:
                    raise RuntimeError("resource I/O protocol cycle mismatch")

                def abort() -> None:
                    # connection_lost is asynchronous. Mark this exact cycle first,
                    # so even Uvicorn's own exception/empty-return fallback cannot write.
                    cycle.disconnected = True
                    cycle.keep_alive = False
                    cycle.message_event.set()
                    # A completed response may already have yielded this keepalive
                    # connection to another request. Fence only the old cycle then.
                    if not cycle.response_complete:
                        cycle.flow.resume_writing()
                        cycle.transport.abort()

                scope.setdefault("extensions", {})[TRANSPORT_EXTENSION] = {
                    "abort": abort,
                    "disconnected": self._disconnected,
                }
            await application(scope, receive, send)

        self.app = admitted_transport

    def connection_lost(self, exc: Exception | None) -> None:
        self._disconnected.set()
        super().connection_lost(exc)
