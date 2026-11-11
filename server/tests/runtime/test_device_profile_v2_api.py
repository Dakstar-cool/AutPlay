"""UNEXECUTED D1 HTTP framing/error/factory fixtures; no endpoint pass claim."""

from __future__ import annotations

from typing import cast

import pytest
from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.domain.device_profile_v2 import DeviceProfileOperation, parse_device_profile_v2
from autplay.entrypoints.device_profile_v2_http import create_device_profile_v2_router
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient

PATH = "/api/v1/device-compatibility/v2"


class ParsingBoundary:
    """Route fixture uses the real parser, before any authority/effect callback."""

    accepted = 0

    def enrollment_exchange(self, raw: bytes) -> tuple[dict[str, object], bool]:
        parse_device_profile_v2(DeviceProfileOperation.ENROLLMENT_EXCHANGE, raw)
        self.accepted += 1
        raise AssertionError("malformed request crossed the parser")

    def session_rotate(self, raw: bytes) -> tuple[dict[str, object], bool]:
        parse_device_profile_v2(DeviceProfileOperation.SESSION_ROTATE, raw)
        self.accepted += 1
        raise AssertionError("malformed request crossed the parser")


def client_for(service: object | None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_device_profile_v2_router(cast(DeviceProfileServiceV2 | None, service)),
        prefix="/api/v1",
    )
    return TestClient(app)


@pytest.mark.parametrize("route", ["enrollment/exchanges", "sessions/rotate"])
@pytest.mark.parametrize(
    "body",
    [
        b'{"contract_version":"v2","contract_version":"v2"}',
        b'{"device_public_key_jwk":{"x":"a","x":"b"}}',
        b'{"schema_version":2.0}',
        b'{"schema_version":2e0}',
        b'{"schema_version":true}',
        b'{"schema_version":NaN}',
        b'{"platform":"MACOS","unexpected":null}',
        b"[]",
        b"null",
        b'{"device_name":"\\ud800"}',
        b"\xff",
        b"{",
    ],
)
def test_raw_malformed_json_never_reaches_effects(route: str, body: bytes) -> None:
    service = ParsingBoundary()
    with client_for(service) as client:
        response = client.post(
            PATH + "/" + route, content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "device_contract_invalid"
    assert set(response.json()["error"]) == {"code", "message", "request_id", "retryable"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert service.accepted == 0


@pytest.mark.parametrize("route", ["enrollment/exchanges", "sessions/rotate"])
def test_wire_byte_limit_applies_before_parse_and_chunked_body(route: str) -> None:
    service = ParsingBoundary()
    with client_for(service) as client:
        for body in (b" " * 16385, iter([b" " * 8192, b" " * 8193])):
            response = client.post(
                PATH + "/" + route,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "device_contract_invalid"
            assert response.headers["Pragma"] == "no-cache"
    assert service.accepted == 0


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Type", "text/plain")],
        [("Content-Type", "application/json"), ("Content-Type", "application/json")],
    ],
)
def test_single_json_content_type_is_required(headers: list[tuple[str, str]]) -> None:
    with client_for(ParsingBoundary()) as client:
        response = client.post(PATH + "/sessions/rotate", content=b"{}", headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "device_contract_invalid"


def test_missing_identity_is_unavailable_and_no_generic_operation_route_exists() -> None:
    with client_for(None) as client:
        for route in ("enrollment/exchanges", "sessions/rotate"):
            response = client.post(PATH + "/" + route, json={})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "device_contract_unavailable"
            assert response.headers["Pragma"] == "no-cache"
            assert client.get(PATH + "/" + route).status_code == 405
        assert client.post(PATH + "/arbitrary/exchange", json={}).status_code == 404


def test_factory_keeps_disabled_service_and_same_m5_identity() -> None:
    from autplay.application.profile_pairing import ProfilePairingService
    from autplay.entrypoints.composition import build_device_profile_v2_service
    from autplay.runtime.settings import ApiSettings
    from pydantic import SecretStr
    from sqlalchemy import create_engine

    # No connection is opened; the future fixture only inspects composition.
    engine = create_engine("sqlite://")
    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:fixture@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("d1-synthetic-secret-at-least-thirty-two-bytes"),
        public_access_source_hmac_secret=SecretStr("d1-source-synthetic-secret-at-least-32-bytes"),
    )
    identity = object()
    service = build_device_profile_v2_service(
        settings, engine, identity=cast(ProfilePairingService, identity)
    )
    assert service is not None and service._identity is identity
    assert service._enabled() is False
    assert build_device_profile_v2_service(settings, engine, identity=None) is None
    engine.dispose()
