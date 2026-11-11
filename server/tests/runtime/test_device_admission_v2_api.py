"""UNEXECUTED transport framing/error/composition gates for all six D1 routes."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.domain.device_profile_v2 import DeviceProfileOperation as Op
from autplay.domain.device_profile_v2 import parse_device_profile_v2
from autplay.entrypoints.device_admission_v2_http import create_device_admission_v2_router
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient

PREFIX = "/api/v1/device-compatibility/v2"
REQUEST = "00000000-0000-0000-0000-000000000071"
ROUTES = (
    "/admission/requests",
    f"/admission/requests/{REQUEST}/poll",
    f"/admission/requests/{REQUEST}/recover",
    f"/admission/requests/{REQUEST}/exchange",
    "/trusted/challenge",
    "/trusted/exchanges",
)


class ParsingBoundary:
    accepted = 0

    def call(self, operation: Op, raw: bytes) -> object:
        parse_device_profile_v2(operation, raw)
        self.accepted += 1
        raise AssertionError("malformed document passed the exact parser")

    def admission_submit(self, raw: bytes, *, source: str | None = None) -> object:
        return self.call(Op.ADMISSION_SUBMIT, raw)

    def admission_poll(self, raw: bytes, request_id: str, bearer: str) -> object:
        return self.call(Op.ADMISSION_POLL, raw)

    def admission_recover(self, raw: bytes, request_id: str) -> object:
        return self.call(Op.ADMISSION_RECOVER, raw)

    def admission_exchange(self, raw: bytes, request_id: str, bearer: str) -> object:
        return self.call(Op.ADMISSION_EXCHANGE, raw)

    def trusted_challenge(self, raw: bytes) -> object:
        return self.call(Op.TRUSTED_CHALLENGE, raw)

    def trusted_exchange(self, raw: bytes) -> object:
        return self.call(Op.TRUSTED_EXCHANGE, raw)


def client_for(service: object | None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_device_admission_v2_router(cast(DeviceAdmissionServiceV2 | None, service)),
        prefix="/api/v1",
    )
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b"{}",
        b'{"schema_version":2,"schema_version":2}',
        b'{"schema_version":true}',
        b'{"schema_version":2e0}',
        b'{"jwk":{"x":"a","x":"b"}}',
        b'{"x":NaN}',
    ],
)
def test_closed_utf8_duplicate_safe_models_never_reach_effects(route: str, raw: bytes) -> None:
    service = ParsingBoundary()
    with client_for(service) as client:
        response = client.post(
            PREFIX + route,
            content=raw,
            headers={"Content-Type": "application/json", "X-AutPlay-Admission-Poll": "A" * 22},
        )
    assert response.status_code == 400 and service.accepted == 0
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert str(UUID(error["request_id"])) == error["request_id"]
    assert len(error["message"]) <= 200 and "NaN" not in error["message"]
    assert (
        response.headers["Cache-Control"] == "no-store" and response.headers["Pragma"] == "no-cache"
    )


@pytest.mark.parametrize("route", ROUTES)
def test_body_limit_is_raw_and_streamed_and_single_json_type_is_required(route: str) -> None:
    with client_for(ParsingBoundary()) as client:
        for raw in (b" " * 16385, iter([b" " * 8192, b" " * 8193])):
            response = client.post(
                PREFIX + route, content=raw, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 413
        for headers in (
            [("Content-Type", "text/plain")],
            [("Content-Type", "application/json"), ("Content-Type", "application/json")],
        ):
            assert client.post(PREFIX + route, content=b"{}", headers=headers).status_code == 400


@pytest.mark.parametrize("suffix", ["poll", "exchange"])
@pytest.mark.parametrize("values", [[], ["A" * 21], ["A" * 21 + "B"], ["A" * 22, "A" * 22]])
def test_poll_credential_is_single_canonical_sixteen_byte_header(
    suffix: str, values: list[str]
) -> None:
    with client_for(ParsingBoundary()) as client:
        response = client.post(
            PREFIX + f"/admission/requests/{REQUEST}/{suffix}",
            content=b"{}",
            headers=[
                ("Content-Type", "application/json"),
                *(("X-AutPlay-Admission-Poll", value) for value in values),
            ],
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "device_contract_invalid"


def test_factory_retains_disabled_handlers_and_m5_identity() -> None:
    from autplay.application.profile_pairing import ProfilePairingService
    from autplay.entrypoints.composition import build_device_admission_v2_service
    from autplay.runtime.settings import ApiSettings
    from pydantic import SecretStr
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:fixture@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("d1-factory-synthetic-secret-at-least-32-bytes"),
        public_access_source_hmac_secret=SecretStr("d1-source-synthetic-secret-at-least-32-bytes"),
    )
    identity = cast(ProfilePairingService, object())
    service = build_device_admission_v2_service(settings, engine, identity=identity)
    assert service is not None and service._identity is identity and service._enabled() is False
    assert build_device_admission_v2_service(settings, engine, identity=None) is None
    with client_for(None) as client:
        for route in ROUTES:
            response = client.post(
                PREFIX + route, json={}, headers={"X-AutPlay-Admission-Poll": "A" * 22}
            )
            assert (
                response.status_code == 503
                and response.json()["error"]["code"] == "device_contract_unavailable"
            )
            assert client.get(PREFIX + route).status_code == 405
        assert client.post(PREFIX + "/any/operation", json={}).status_code == 404
    engine.dispose()
