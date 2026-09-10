"""Event-loop isolation checks for the synchronous sync HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import time
from typing import cast
from uuid import UUID

from autplay.application.sync import SyncService
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.sync_http import create_sync_router
from fastapi import FastAPI, Request
from starlette.types import Message, Scope

_REQUEST_ID = UUID("018f47bc-2f9d-7cc2-8e39-01b4ce17cc88")
_PRINCIPAL = Principal(
    user_id=UUID("018f47bc-2f9d-7cc2-8e39-01b4ce17cc81"),
    device_id=UUID("018f47bc-2f9d-7cc2-8e39-01b4ce17cc82"),
    session_id=UUID("018f47bc-2f9d-7cc2-8e39-01b4ce17cc83"),
    role=AccountRole.USER,
)


class _SlowSyncService:
    def push(
        self,
        principal: Principal,
        body: dict[str, object],
        request_id: UUID,
    ) -> dict[str, object]:
        assert principal == _PRINCIPAL
        assert body == {"protocol_version": 1, "events": []}
        assert request_id == _REQUEST_ID
        time.sleep(0.25)
        return {"acks": []}


def test_blocking_sync_io_does_not_stall_the_event_loop() -> None:
    service = cast(SyncService, _SlowSyncService())
    app = FastAPI()

    def authenticated(request: Request) -> None:
        request.state.principal = _PRINCIPAL
        request.state.request_id = str(_REQUEST_ID)

    app.include_router(create_sync_router(service, authenticated=authenticated), prefix="/api/v1")
    heartbeat_delay, total_delay, messages = asyncio.run(_exercise_push_with_heartbeat(app))

    response_start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 200
    assert heartbeat_delay < 0.15
    assert total_delay >= 0.20


async def _exercise_push_with_heartbeat(app: FastAPI) -> tuple[float, float, list[Message]]:
    document = json.dumps({"protocol_version": 1, "events": []}).encode("utf-8")
    messages: list[Message] = []
    received = False
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/sync/push",
            "raw_path": b"/api/v1/sync/push",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(document)).encode("ascii")),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        },
    )

    async def receive() -> Message:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": document, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    started = asyncio.get_running_loop().time()
    request = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.05)
    heartbeat_delay = asyncio.get_running_loop().time() - started
    await request
    total_delay = asyncio.get_running_loop().time() - started
    return heartbeat_delay, total_delay, messages
