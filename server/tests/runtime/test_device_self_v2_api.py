"""UNEXECUTED SELF framing, error precedence and disabled composition fixtures."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest
import rfc8785
from autplay.domain.canonical_json import JsonValue
from autplay.domain.profile_pairing import canonical_sha256
from autplay.entrypoints.device_self_v2_http import create_device_self_v2_router
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from fastapi import FastAPI
from fastapi.testclient import TestClient

PREFIX = "/api/v1/device-compatibility/v2/self/ceremonies"
IDENTIFIER = "00000000-0000-0000-0000-000000000072"


def client_for_missing_identity() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    install_error_handlers(app)
    app.include_router(
        create_device_self_v2_router(None, authenticated=lambda request: None), prefix="/api/v1"
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        f"/{IDENTIFIER}/claim",
        f"/{IDENTIFIER}/poll",
        f"/{IDENTIFIER}/decision",
        f"/{IDENTIFIER}/exchange",
    ],
)
@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b"[]",
        b"{}",
        b'{"x":NaN}',
        b'{"schema_version":2,"schema_version":2}',
        b'{"schema_version":true}',
        b'{"schema_version":2e0}',
        b'{"expected_identity_epoch":1.0}',
        b'{"x":{"y":0,"y":1}}',
    ],
)
def test_invalid_original_precedes_missing_identity_without_framework_detail(
    suffix: str, raw: bytes
) -> None:
    response = client_for_missing_identity().post(
        PREFIX + suffix, content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400 and set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id", "retryable"}
    assert error["code"] == "device_contract_invalid" and len(error["message"]) <= 200
    assert str(UUID(error["request_id"])) == error["request_id"]
    assert response.headers["X-Request-ID"] == error["request_id"]
    assert (
        response.headers["Cache-Control"] == "no-store" and response.headers["Pragma"] == "no-cache"
    )


def test_raw_chunk_limit_duplicate_headers_and_closed_route_registry() -> None:
    client = client_for_missing_identity()
    for raw in (b" " * 8193, iter([b" " * 4096, b" " * 4097])):
        assert (
            client.post(
                PREFIX, content=raw, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )
    response = client.post(
        PREFIX,
        content=b"{}",
        headers=[("Content-Type", "application/json"), ("Content-Type", "application/json")],
    )
    assert response.status_code == 400
    assert client.post(PREFIX + f"/{IDENTIFIER}/arbitrary", json={}).status_code == 404
    assert client.get(PREFIX + f"/{IDENTIFIER}/claim").status_code == 405
    assert client.get(PREFIX + "/not-a-uuid").status_code == 400


def test_valid_start_without_m5_is_explicit_unavailable() -> None:
    body = {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": "ANDROID",
        "ceremony_id": IDENTIFIER,
        "operation_id": "00000000-0000-0000-0000-000000000073",
        "requested_at": "2026-10-16T00:00:00Z",
        "rendezvous_secret_sha256": "0" * 64,
        "expected_server_instance_id": IDENTIFIER,
        "expected_identity_epoch": 1,
        "expected_identity_thumbprint_sha256": "1" * 64,
        "expected_api_origin": "https://api.test.invalid",
        "expected_stream_origin": "https://stream.test.invalid",
    }
    body["request_sha256"] = canonical_sha256(body).hex()
    response = client_for_missing_identity().post(
        PREFIX,
        content=rfc8785.dumps(cast(JsonValue, body)),
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "device_contract_unavailable"
    )


def test_factory_retains_handler_for_independently_disabled_flags() -> None:
    from autplay.entrypoints.composition import (
        build_device_self_v2_service,
        build_profile_pairing_service,
    )
    from autplay.runtime.settings import ApiSettings
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from pydantic import SecretStr
    from sqlalchemy import create_engine

    # Future source fixture constructs a REAL configured M5 owner, no DB connection.
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
        auth_signing_secret=SecretStr("self-synthetic-access-secret-at-least-32bytes"),
        public_access_source_hmac_secret=SecretStr("self-synthetic-source-secret-at-least-32bytes"),
        profile_identity_private_key_pem=SecretStr(pem),
        device_compatibility_v2_enabled=False,
        self_device_pairing_enabled=False,
    )
    engine = create_engine("sqlite://")
    identity = build_profile_pairing_service(settings, engine)
    assert identity is not None
    service = build_device_self_v2_service(settings, engine, identity=identity)
    assert service is not None and service._enabled() is False
    for compatibility, family in ((False, False), (True, False), (False, True), (True, True)):
        configured = settings.model_copy(
            update={
                "device_compatibility_v2_enabled": compatibility,
                "self_device_pairing_enabled": family,
            }
        )
        retained = build_device_self_v2_service(configured, engine, identity=identity)
        assert retained is not None and retained._enabled() is (compatibility and family)
    assert build_device_self_v2_service(settings, engine, identity=None) is None
    engine.dispose()
