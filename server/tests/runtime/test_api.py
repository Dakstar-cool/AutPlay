"""FastAPI factory, health, errors, request IDs, and metrics tests."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_public_access_service
from autplay.runtime.http import MAX_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_FRAMES
from autplay.runtime.settings import ApiSettings
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import create_engine
from starlette.testclient import TestClient
from starlette.types import Message, Scope

DATABASE_URL = "postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"
AUTH_SECRET = "runtime-test-signing-secret-at-least-32-bytes"


@dataclass(frozen=True)
class StaticProbe:
    result: ReadinessResult

    def check(self) -> ReadinessResult:
        return self.result


def _settings() -> ApiSettings:
    return ApiSettings(
        database_url=SecretStr(DATABASE_URL),
        auth_signing_secret=SecretStr(AUTH_SECRET),
        public_access_source_hmac_secret=SecretStr(
            "public-access-source-hmac-secret-at-least-32-bytes"
        ),
    )


def _admin_settings() -> ApiSettings:
    return ApiSettings(
        database_url=SecretStr(DATABASE_URL),
        auth_signing_secret=SecretStr(AUTH_SECRET),
        public_access_source_hmac_secret=SecretStr(
            "public-access-source-hmac-secret-at-least-32-bytes"
        ),
        admin_web_enabled=True,
        admin_web_origin="https://admin.test",
        admin_web_source_hmac_secret=SecretStr("source-secret-is-distinct-and-at-least-32-bytes"),
        admin_web_csrf_hmac_secret=SecretStr("csrf-secret-is-distinct-and-at-least-32-bytes"),
    )


def _app(result: ReadinessResult) -> FastAPI:
    return create_app(_settings(), readiness_probe=StaticProbe(result))


def test_public_access_composition_uses_the_dedicated_source_hmac_secret() -> None:
    settings = _settings()
    engine = create_engine(DATABASE_URL)
    try:
        service = build_public_access_service(settings, engine)
        assert service.source_hmac_secret == (
            settings.public_access_source_hmac_secret.get_secret_value().encode("utf-8")
        )
        assert service.source_hmac_secret != settings.auth_signing_secret.get_secret_value().encode(
            "utf-8"
        )
    finally:
        engine.dispose()


def test_liveness_does_not_touch_failed_database_readiness() -> None:
    app = _app(
        ReadinessResult(
            ready=False,
            component="postgresql",
            code="database_unavailable",
        )
    )
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live", "component": "api"}
    UUID(response.headers["x-request-id"])


def test_readiness_failure_has_stable_error_and_correlated_request_id() -> None:
    app = _app(
        ReadinessResult(
            ready=False,
            component="postgresql",
            code="database_unavailable",
        )
    )
    request_id = "018f47bc-2f9d-7cc2-8e39-01b4ce17cc88"
    with TestClient(app) as client:
        response = client.get("/health/ready", headers={"X-Request-ID": request_id})

    assert response.status_code == 503
    assert response.headers["x-request-id"] == request_id
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "A required service component is not ready.",
            "retryable": True,
            "request_id": request_id,
        }
    }


def test_readiness_success_and_metrics_are_exposed() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        metrics = client.get("/metrics")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "component": "api"}
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert 'autplay_readiness{component="postgresql"} 1.0' in metrics.text
    assert "autplay_http_requests_total" in metrics.text


def test_framework_errors_use_stable_envelope_and_replace_bad_request_id() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    with TestClient(app) as client:
        response = client.get("/missing", headers={"X-Request-ID": "not-a-uuid\nsecret"})

    body = response.json()
    generated = response.headers["x-request-id"]
    assert response.status_code == 404
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == generated
    assert str(UUID(generated)) == generated
    assert "secret" not in response.text


def test_duplicate_request_id_headers_are_replaced() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    supplied = (
        "018f47bc-2f9d-7cc2-8e39-01b4ce17cc81",
        "018f47bc-2f9d-7cc2-8e39-01b4ce17cc82",
    )

    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers=[("X-Request-ID", supplied[0]), ("X-Request-ID", supplied[1])],
        )

    generated = response.headers["x-request-id"]
    assert generated not in supplied
    assert str(UUID(generated)) == generated


def test_public_registration_login_and_bootstrap_routes_do_not_exist() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    forbidden_routes = (
        "/api/v1/register",
        "/api/v1/auth/login",
        "/api/v1/auth/bootstrap",
    )

    with TestClient(app) as client:
        responses = [client.post(route, json={}) for route in forbidden_routes]

    assert all(response.status_code == 404 for response in responses)
    assert all(response.json()["error"]["code"] == "not_found" for response in responses)


def test_guest_capability_header_is_rejected_by_every_non_guest_api_family() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    capability = "B" * 43
    room_id = "018f47bc-2f9d-7cc2-8e39-01b4ce17cc88"
    requests = (
        ("GET", "/api/v1/library/entries"),
        ("POST", "/api/v1/sync/push"),
        ("POST", "/api/v1/recommendations"),
        ("POST", "/api/v1/vault/uploads"),
        ("GET", "/api/v1/account/devices"),
        ("GET", "/api/v1/social/snapshot"),
        ("GET", f"/api/v1/wave/rooms/{room_id}/snapshot"),
        ("POST", "/api/v1/imports"),
        ("GET", "/api/v1/profile/capabilities"),
    )
    with TestClient(app) as client:
        responses = [
            client.request(
                method,
                path,
                headers={"X-AutPlay-Guest-Capability": capability},
                json={} if method == "POST" else None,
            )
            for method, path in requests
        ]

    assert all(response.status_code == 401 for response in responses)
    assert all(capability not in response.text for response in responses)


def test_admin_web_is_absent_when_disabled_and_bundled_when_enabled() -> None:
    disabled = _app(ReadinessResult(ready=True, component="postgresql"))
    enabled = create_app(
        _admin_settings(),
        readiness_probe=StaticProbe(ReadinessResult(ready=True, component="postgresql")),
    )
    with TestClient(disabled, base_url="https://admin.test") as client:
        missing = client.get("/admin/static/admin-v1.css")
    with TestClient(enabled, base_url="https://admin.test") as client:
        asset = client.get("/admin/static/admin-v1.css")
        missing_admin = client.get("/admin/not-a-real-surface/extra")

    assert missing.status_code == 404
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert missing_admin.status_code == 404
    assert missing_admin.headers["cache-control"] == "no-store"
    assert missing_admin.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in missing_admin.headers["content-security-policy"]


def test_validation_and_unhandled_errors_do_not_echo_inputs() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))

    @app.get("/api/v1/runtime-test/{value}")
    async def typed_value(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/api/v1/runtime-failure")
    async def failure() -> None:
        raise RuntimeError("password=should-not-escape")

    with TestClient(app, raise_server_exceptions=False) as client:
        validation = client.get("/api/v1/runtime-test/not-an-integer")
        failure_response = client.get("/api/v1/runtime-failure")

    assert validation.status_code == 422
    assert validation.headers["cache-control"] == "no-store"
    assert validation.json()["error"]["code"] == "request_validation_failed"
    assert "not-an-integer" not in validation.text
    assert failure_response.status_code == 500
    assert failure_response.json()["error"]["code"] == "internal_error"
    assert "should-not-escape" not in failure_response.text


def test_metrics_use_route_templates_not_raw_identifiers() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    raw_id = "01f8f50e-sensitive-user-value"
    with TestClient(app) as client:
        response = client.get(f"/api/v1/items/{raw_id}")
        metrics = client.get("/metrics")

    assert response.status_code == 200
    assert raw_id not in metrics.text
    assert re.search(
        r'autplay_http_requests_total\{method="GET",'
        r'route="/api/v1/items/\{item_id\}",status_code="200"\} 1\.0',
        metrics.text,
    )


def test_metrics_normalize_client_controlled_http_methods() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    hostile_methods = ("M0001", "M0002", "M0003")

    with TestClient(app) as client:
        responses = [client.request(method, "/health/live") for method in hostile_methods]
        metrics = client.get("/metrics")

    assert all(response.status_code == 405 for response in responses)
    assert all(method not in metrics.text for method in hostile_methods)
    assert 'method="OTHER"' in metrics.text


def test_declared_and_streamed_oversized_bodies_fail_before_json_parsing() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    request_id = "018f47bc-2f9d-7cc2-8e39-01b4ce17cc89"
    oversized = b"x" * (MAX_REQUEST_BODY_BYTES + 1)

    with TestClient(app) as client:
        declared = client.post(
            "/api/v1/auth/refresh",
            content=b"{}",
            headers={
                "Content-Length": str(MAX_REQUEST_BODY_BYTES + 1),
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
        )
    streamed = asyncio.run(_send_streamed_body(app, (oversized[:700_000], oversized[700_000:])))

    assert declared.status_code == 413
    assert declared.headers["x-request-id"] == request_id
    assert declared.headers["cache-control"] == "no-store"
    assert declared.json()["error"]["code"] == "request_body_too_large"
    response_start = next(
        message for message in streamed if message["type"] == "http.response.start"
    )
    response_body = next(message for message in streamed if message["type"] == "http.response.body")
    headers = dict(response_start["headers"])
    assert response_start["status"] == 413
    assert headers[b"cache-control"] == b"no-store"
    assert json.loads(response_body["body"])["error"]["code"] == "request_body_too_large"


def test_many_tiny_body_frames_are_bounded_before_application_dispatch() -> None:
    app = _app(ReadinessResult(ready=True, component="postgresql"))
    streamed = asyncio.run(_send_streamed_body(app, (b"x",) * (MAX_REQUEST_BODY_FRAMES + 1)))

    response_start = next(
        message for message in streamed if message["type"] == "http.response.start"
    )
    response_body = next(message for message in streamed if message["type"] == "http.response.body")
    assert response_start["status"] == 413
    assert json.loads(response_body["body"])["error"]["code"] == "request_body_too_large"


async def _send_streamed_body(app: FastAPI, chunks: tuple[bytes, ...]) -> list[Message]:
    request_messages: list[Message] = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    response_messages: list[Message] = []
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/refresh",
            "raw_path": b"/api/v1/auth/refresh",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/json"),
                (b"transfer-encoding", b"chunked"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        },
    )

    async def receive() -> Message:
        if request_messages:
            return request_messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        response_messages.append(message)

    await app(scope, receive, send)
    return response_messages
