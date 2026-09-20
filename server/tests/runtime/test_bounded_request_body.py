"""ASGI evidence that admission precedes upload bytes while wire limits still apply."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from autplay.runtime.http import (
    MAX_REQUEST_BODY_FRAMES,
    ApiError,
    RequestRuntimeMiddleware,
    install_error_handlers,
)
from autplay.runtime.metrics import RuntimeMetrics
from fastapi import Depends, FastAPI, Request
from starlette.responses import Response
from starlette.types import Message, Scope

UPLOAD_PATH = "/api/v1/vault/uploads/018f47bc-2f9d-7cc2-8e39-01b4ce17cc88"
RESOURCE_PATH = "/api/v1/account/resource-admissions"


def _probe(*, denied: bool = False) -> tuple[FastAPI, list[str]]:
    events: list[str] = []
    app = FastAPI()
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    install_error_handlers(app)

    def admission() -> None:
        events.append("admission")
        if denied:
            raise ApiError("resource_activation_stale", "The permit is stale.", 409)

    @app.patch(UPLOAD_PATH, dependencies=[Depends(admission)])
    async def upload(request: Request) -> Response:
        await request.body()
        events.append("append")
        return Response(status_code=204)

    @app.post(RESOURCE_PATH)
    async def resource(request: Request) -> Response:
        events.append("dispatch")
        await request.body()
        return Response(status_code=204)

    return app, events


def _send(
    app: FastAPI,
    events: list[str],
    chunks: tuple[bytes, ...],
    *,
    path: str = UPLOAD_PATH,
    method: str = "PATCH",
    headers: list[tuple[bytes, bytes]] | None = None,
    root_path: str = "",
) -> tuple[int, dict[str, object]]:
    async def run() -> list[Message]:
        replies: list[Message] = []
        pending: list[Message] = [
            {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        ]
        scope = cast(
            Scope,
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": root_path + path,
                "raw_path": (root_path + path).encode("ascii"),
                "query_string": b"",
                "root_path": root_path,
                "headers": headers or [],
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
                "state": {},
            },
        )

        async def receive() -> Message:
            events.append("receive")
            return pending.pop(0) if pending else {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            replies.append(message)

        await app(scope, receive, send)
        return replies

    replies = asyncio.run(run())
    start = next(item for item in replies if item["type"] == "http.response.start")
    assert b"x-request-id" in dict(start["headers"])
    payload = b"".join(item.get("body", b"") for item in replies)
    return start["status"], json.loads(payload) if payload else {}


@pytest.mark.parametrize("root_path", ["", "/autplay"])
def test_rejected_upload_does_not_read_any_body_frame(root_path: str) -> None:
    app, events = _probe(denied=True)
    status, _ = _send(app, events, (b"abc",), root_path=root_path)
    assert status == 409
    assert events == ["admission"]


def test_upload_is_read_only_after_admission_and_appends_once() -> None:
    app, events = _probe()
    status, _ = _send(app, events, (b"a", b"bc"))
    assert status == 204
    assert events == ["admission", "receive", "receive", "append"]


@pytest.mark.parametrize(
    "chunks",
    [
        (b"x" * 1_048_576, b"x"),
        (b"",) * (MAX_REQUEST_BODY_FRAMES + 1),
    ],
)
def test_upload_wire_overflow_cannot_reach_append(chunks: tuple[bytes, ...]) -> None:
    app, events = _probe()
    status, document = _send(app, events, chunks, headers=[(b"content-length", b"1")])
    assert status == 422
    assert cast(dict[str, object], document["error"])["code"] == "upload_limit_exceeded"
    assert events[0] == "admission"
    assert "append" not in events


def test_declared_upload_limit_is_rejected_without_receiving_body() -> None:
    app, events = _probe()
    status, _ = _send(app, events, (b"x",), headers=[(b"content-length", b"1048577")])
    assert status == 422
    assert events == []


@pytest.mark.parametrize("declared", [False, True])
def test_resource_envelope_is_bounded_at_four_kib_before_dispatch(declared: bool) -> None:
    app, events = _probe()
    status, document = _send(
        app,
        events,
        (b"x" * 4_096, b"x"),
        path=RESOURCE_PATH,
        method="POST",
        headers=[(b"content-length", b"4097")] if declared else [],
    )
    assert status == 400
    assert cast(dict[str, object], document["error"])["code"] == "resource_request_invalid"
    assert "dispatch" not in events
    if declared:
        assert events == []


def test_resource_envelope_at_exact_limit_reaches_dispatch() -> None:
    app, events = _probe()
    status, _ = _send(app, events, (b"x" * 4_096,), path=RESOURCE_PATH, method="POST")
    assert status == 204
    assert events == ["receive", "dispatch"]
