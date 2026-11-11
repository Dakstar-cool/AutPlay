"""UNEXECUTED raw HTTP precedence and closed six-operation registry fixtures."""

from __future__ import annotations

from uuid import UUID

import pytest
from autplay.domain import device_compatibility
from autplay.entrypoints.device_deletion_v2_http import create_device_deletion_v2_router
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typed_assertions import route_path

PREFIX = "/api/v1/device-compatibility/v2/deletion"
OPERATIONS = (
    "requests",
    "request-receipt",
    "request-resolve",
    "cancel/preview",
    "cancel/commit",
    "cancel/outcome",
)


def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    install_error_handlers(app)
    app.include_router(create_device_deletion_v2_router(None), prefix="/api/v1")
    return TestClient(app)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b"[]",
        b"{}",
        b'{"schema_version":2,"schema_version":2}',
        b'{"schema_version":true}',
        b'{"schema_version":2.0}',
        b'{"expected_identity_epoch":1e0}',
        b'{"device_signature_b64url":null}',
        b'{"unexpected":{"x":1,"x":2}}',
    ],
)
def test_strict_original_errors_precede_service_secrets_and_bearer(
    operation: str, raw: bytes
) -> None:
    with client() as http:
        reply = http.post(
            PREFIX + "/" + operation, content=raw, headers={"Content-Type": "application/json"}
        )
    assert reply.status_code == 400 and set(reply.json()) == {"error"}
    error = reply.json()["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert error["code"] == "device_contract_invalid" and len(error["message"]) <= 200
    assert str(UUID(error["request_id"])) == error["request_id"] == reply.headers["X-Request-ID"]
    assert reply.headers["Cache-Control"] == "no-store" and reply.headers["Pragma"] == "no-cache"


@pytest.mark.parametrize("operation", OPERATIONS)
def test_bounded_raw_chunks_duplicate_type_and_exact_route_methods(operation: str) -> None:
    with client() as http:
        for payload in (b" " * 8193, iter([b" " * 4096, b" " * 4097])):
            reply = http.post(
                PREFIX + "/" + operation,
                content=payload,
                headers={"Content-Type": "application/json"},
            )
            assert reply.status_code == 413 and reply.headers["Cache-Control"] == "no-store"
        assert (
            http.post(
                PREFIX + "/" + operation,
                content=b"{}",
                headers=[
                    ("Content-Type", "application/json"),
                    ("Content-Type", "application/json"),
                ],
            ).status_code
            == 400
        )
        assert http.get(PREFIX + "/" + operation).status_code == 405
        assert http.post(PREFIX + "/arbitrary", json={}).status_code == 404


def test_exactly_six_routes_do_not_qualify_registry_or_construct_default_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)

    router = create_device_deletion_v2_router(None)
    assert {route_path(route) for route in router.routes} == {
        "/device-compatibility/v2/deletion/" + operation for operation in OPERATIONS
    }
    assert len(router.routes) == 6
    assert frozenset() == device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES
