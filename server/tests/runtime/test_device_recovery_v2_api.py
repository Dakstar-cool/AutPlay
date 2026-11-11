"""UNEXECUTED recovery HTTP framing and terminal-service composition fixtures."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest
from autplay.entrypoints.device_recovery_v2_http import create_device_recovery_v2_router
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from fastapi import FastAPI
from fastapi.testclient import TestClient

PREFIX = "/api/v1/device-compatibility/v2/recovery"


def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    install_error_handlers(app)
    app.include_router(create_device_recovery_v2_router(None), prefix="/api/v1")
    return TestClient(app)


@pytest.mark.parametrize("operation", ["preview", "commit", "outcome"])
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
        b'{"x":{"y":0,"y":1}}',
    ],
)
def test_bounded_strict_model_errors_precede_missing_service(operation: str, raw: bytes) -> None:
    reply = client().post(
        PREFIX + "/" + operation, content=raw, headers={"Content-Type": "application/json"}
    )
    assert reply.status_code == 400 and set(reply.json()) == {"error"}
    error = reply.json()["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert error["code"] == "device_contract_invalid" and len(error["message"]) <= 200
    assert str(UUID(error["request_id"])) == error["request_id"] == reply.headers["X-Request-ID"]
    assert reply.headers["Cache-Control"] == "no-store" and reply.headers["Pragma"] == "no-cache"


@pytest.mark.parametrize("operation", ["preview", "commit", "outcome"])
def test_raw_bounds_content_type_and_closed_routes(operation: str) -> None:
    for payload in (b" " * 8193, iter([b" " * 4096, b" " * 4097])):
        assert (
            client()
            .post(
                PREFIX + "/" + operation,
                content=payload,
                headers={"Content-Type": "application/json"},
            )
            .status_code
            == 413
        )
    assert (
        client()
        .post(
            PREFIX + "/" + operation,
            content=b"{}",
            headers=[("Content-Type", "application/json"), ("Content-Type", "application/json")],
        )
        .status_code
        == 400
    )
    assert client().get(PREFIX + "/" + operation).status_code == 405
    assert client().post(PREFIX + "/arbitrary", json={}).status_code == 404


def test_factory_retains_handlers_for_every_independent_flag_combination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES",
        frozenset({"recovery"}),
    )
    from autplay.entrypoints.composition import (
        build_device_recovery_v2_service,
        build_profile_pairing_service,
    )
    from autplay.runtime.settings import ApiSettings
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from pydantic import SecretStr
    from sqlalchemy import create_engine

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    settings_constructor = cast(Callable[..., ApiSettings], ApiSettings)
    settings = settings_constructor(
        _env_file=None,
        database_url=SecretStr("postgresql+psycopg://fixture:fixture@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("recovery-synthetic-access-secret-at-least-32bytes"),
        public_access_source_hmac_secret=SecretStr(
            "recovery-synthetic-source-secret-at-least-32bytes"
        ),
        profile_identity_private_key_pem=SecretStr(pem),
        account_recovery_enabled=False,
        device_compatibility_v2_enabled=False,
    )
    engine = create_engine("sqlite://")
    try:
        identity = build_profile_pairing_service(settings, engine)
        assert identity is not None
        for compatibility, recovery in ((False, False), (True, False), (False, True), (True, True)):
            configured = settings.model_copy(
                update={
                    "device_compatibility_v2_enabled": compatibility,
                    "account_recovery_enabled": recovery,
                }
            )
            service = build_device_recovery_v2_service(configured, engine, identity=identity)
            assert service is not None and service._enabled() is (compatibility and recovery)
        assert build_device_recovery_v2_service(settings, engine, identity=None) is None
    finally:
        engine.dispose()
