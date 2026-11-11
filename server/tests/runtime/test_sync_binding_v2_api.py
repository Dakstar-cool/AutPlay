"""UNEXECUTED B1 HTTP gates. Real database/authentication cases live in postgresql.

No hashes, signatures, imports, HTTP calls or validators were run while authoring.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.domain.auth import AccessTokenClaims, AccountRole, InvalidAccessTokenError, Principal
from autplay.entrypoints.sync_binding_v2_http import (
    SyncBindingV2IngressMiddleware,
    create_sync_binding_v2_router,
)
from autplay.runtime.http import (
    MAX_REQUEST_BODY_FRAMES,
    RequestRuntimeMiddleware,
    install_error_handlers,
)
from autplay.runtime.metrics import RuntimeMetrics
from autplay.runtime.settings import load_api_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from starlette.types import Message

PREFIX = "/api/v1/sync-binding/v2"


def app() -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    application.add_middleware(SyncBindingV2IngressMiddleware)

    def authenticate(token: str) -> tuple[Principal, AccessTokenClaims]:
        if token != "synthetic-entry-valid":
            raise InvalidAccessTokenError()
        now = datetime.now(UTC)
        actor = Principal(UUID(int=1), UUID(int=2), UUID(int=3), AccountRole.USER)
        return actor, AccessTokenClaims(
            actor.user_id,
            actor.device_id,
            actor.session_id,
            uuid4(),
            now,
            now,
            now + timedelta(seconds=60),
        )

    application.include_router(
        create_sync_binding_v2_router(None, authenticate=authenticate), prefix="/api/v1"
    )
    return application


def assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    detail = response.json()["error"]
    assert set(detail) == {"code", "message", "retryable", "request_id"}
    assert detail["code"] == code
    assert detail["message"] == (
        "Authentication is required."
        if status == 401
        else "The sync binding request could not be applied."
    )
    assert detail["retryable"] is (status == 503)
    assert (
        str(UUID(detail["request_id"])) == detail["request_id"] == response.headers["X-Request-ID"]
    )
    assert (
        response.headers["Cache-Control"] == "no-store" and response.headers["Pragma"] == "no-cache"
    )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Basic x")],
        [("Authorization", "Bearer")],
        [("Authorization", "Bearer invalid")],
        [("Authorization", "Bearer  x")],
        [
            ("Authorization", "Bearer synthetic-entry-valid"),
            ("Authorization", "Bearer synthetic-entry-valid"),
        ],
    ],
)
def test_actual_router_authentication_precedes_closed_json(headers: list[tuple[str, str]]) -> None:
    with TestClient(app()) as client:
        for method, operation in (("GET", "capabilities"), ("POST", "bind")):
            response = client.request(
                method,
                PREFIX + "/" + operation,
                content=b"{" if method == "POST" else b"",
                headers=[("Content-Type", "application/json"), *headers],
            )
            assert_error(response, 401, "authentication_required")
            assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b"{}",
        b"\xff",
        b'{"schema_version":2,"schema_version":2}',
        b'{"schema_version":true}',
        b'{"schema_version":2.0}',
        b'{"schema_version":2e0}',
        b'{"nested":{"x":1,"x":1}}',
        b'{"x":[[[[1]]]]}',
        b"{}{}",
    ],
)
def test_strict_model_precedes_unconfigured_service(raw: bytes) -> None:
    with TestClient(app()) as client:
        assert_error(
            client.post(
                PREFIX + "/bind",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer synthetic-entry-valid",
                },
            ),
            400,
            "sync_binding_invalid",
        )


def test_raw_media_shape_auth_and_route_precedence() -> None:
    with TestClient(app(), follow_redirects=False) as client:
        assert_error(
            client.post(
                PREFIX + "/bind?x=1", content=b"x" * 16385, headers={"Content-Type": "text/plain"}
            ),
            413,
            "request_body_too_large",
        )
        assert_error(
            client.post(
                PREFIX + "/bind?x=1", content=b"{}", headers={"Content-Type": "text/plain"}
            ),
            415,
            "sync_binding_media_type_invalid",
        )
        assert_error(
            client.post(
                PREFIX + "/bind?x=1", content=b"{}", headers={"Content-Type": "application/json"}
            ),
            400,
            "sync_binding_invalid",
        )
        for headers in (
            [("Content-Type", "application/json"), ("Content-Type", "application/json")],
            [("Content-Type", "application/json"), ("Content-Encoding", "identity")],
        ):
            assert_error(
                client.post(PREFIX + "/bind", content=b"{}", headers=headers),
                415,
                "sync_binding_media_type_invalid",
            )
        for operation in ("discovery", "capabilities"):
            assert_error(
                client.request("GET", PREFIX + "/" + operation, content=b"x"),
                400,
                "sync_binding_invalid",
            )
            wrong = client.post(PREFIX + "/" + operation, content=b"x" * 16385)
            assert wrong.status_code == 405 and wrong.headers["Allow"] == "GET"
        for path in ("", "/bind/", "/discovery/", "/arbitrary", "/bind/arbitrary"):
            response = client.post(PREFIX + path, content=b"x" * 16385)
            assert response.status_code == 404 and "location" not in response.headers
            assert response.headers["Cache-Control"] == "no-store"


def test_raw_asgi_frame_bound_precedes_media_and_auth() -> None:
    async def exercise() -> list[dict[str, object]]:
        sent: list[dict[str, object]] = []
        count = 0

        async def receive() -> dict[str, object]:
            nonlocal count
            count += 1
            return {
                "type": "http.request",
                "body": b"",
                "more_body": count <= MAX_REQUEST_BODY_FRAMES,
            }

        async def send(message: Message) -> None:
            sent.append(dict(message))

        await app()(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": PREFIX + "/bind",
                "raw_path": (PREFIX + "/bind").encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "client": ("127.0.0.1", 42),
                "server": ("test.invalid", 443),
            },
            receive,
            send,
        )
        return sent

    messages = asyncio.run(exercise())
    assert (
        next(message["status"] for message in messages if message["type"] == "http.response.start")
        == 413
    )


def test_setting_defaults_off_and_accepts_explicit_environment_without_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from device_qualification import set_device_qualification

    set_device_qualification(monkeypatch)
    required = {
        "database_url": "postgresql+psycopg://autplay:synthetic@127.0.0.1:5432/autplay",
        "auth_signing_secret": "b1-synthetic-signing-secret-at-least-thirty-two-bytes",
        "public_access_source_hmac_secret": "b1-synthetic-source-secret-at-least-thirty-two-bytes",
    }
    assert load_api_settings(overrides=required, environ={}).sync_binding_v2_enabled is False
    assert (
        load_api_settings(
            overrides=required, environ={"AUTPLAY_SYNC_BINDING_V2_ENABLED": "true"}
        ).sync_binding_v2_enabled
        is True
    )
    from autplay.domain.device_capability_state import DeviceCapabilityPolicy
    from autplay.domain.device_compatibility import NegotiationPolicy

    assert (
        DeviceCapabilityPolicy(
            NegotiationPolicy(enabled=True), binding_enabled=True
        ).binding_available()
        is False
    )
