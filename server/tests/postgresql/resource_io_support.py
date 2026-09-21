"""Explicit synthetic transport extension for in-process ASGI tests only."""

from autplay.runtime.resource_io_scope import TRANSPORT_EXTENSION
from starlette.types import ASGIApp, Receive, Scope, Send


class InProcessResourceTransport:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("extensions", {})[TRANSPORT_EXTENSION] = {"abort": lambda: None}
        await self.app(scope, receive, send)
