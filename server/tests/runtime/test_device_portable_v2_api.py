"""UNEXECUTED portable v2 framing/error/factory fixtures; no pass claim."""

from __future__ import annotations

from typing import Any, cast

import pytest
from autplay.adapters.portable_account_crypto_v2 import (
    PortableAccountPurposeV2,
    parse_portable_account_v2,
)
from autplay.application.device_portable_service_v2 import (
    DevicePortableServiceV2,
    parse_portable_challenge_v2,
)
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.entrypoints.device_portable_v2_http import (
    build_device_portable_v2_service,
    create_device_portable_v2_router,
)
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

PREFIX = "/api/v1/device-compatibility/v2/portable"
ROUTES = ("register-challenge", "login-challenge", "register", "login")


class ParsingBoundary:
    def __init__(self) -> None:
        self.effects = 0

    def parse(self, operation: str, raw: bytes, source: str) -> dict[str, object]:
        purpose = (
            PortableAccountPurposeV2.REGISTER
            if operation.startswith("register")
            else PortableAccountPurposeV2.LOGIN
        )
        if operation.endswith("challenge"):
            parse_portable_challenge_v2(purpose, raw)
        else:
            parse_portable_account_v2(purpose, raw)
        self.effects += 1
        raise AssertionError("malformed document crossed its exact parser")

    def register_challenge(self, raw: bytes, *, source: str) -> dict[str, object]:
        return self.parse("register-challenge", raw, source)

    def login_challenge(self, raw: bytes, *, source: str) -> dict[str, object]:
        return self.parse("login-challenge", raw, source)

    def register(self, raw: bytes, *, source: str) -> dict[str, object]:
        return self.parse("register", raw, source)

    def login(self, raw: bytes, *, source: str) -> dict[str, object]:
        return self.parse("login", raw, source)


def client_for(service: object | None, **kwargs: Any) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_device_portable_v2_router(
            cast(DevicePortableServiceV2 | None, service),
            **kwargs,
        ),
        prefix="/api/v1",
    )
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "raw",
    [
        b'{"contract_version":"v2","contract_version":"v2"}',
        b'{"account":{"public_id":"a","public_id":"b"}}',
        b'{"request":{"challenge":{"nonce":"a","nonce":"b"}},"proof":"AA=="}',
        b'{"schema_version":2.0}',
        b'{"schema_version":2e0}',
        b'{"schema_version":true}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"schema_version":9007199254740992}',
        b'{"schema_version":-1}',
        b'{"public_id":"\\ud800"}',
        b'{"\\udfff":0}',
        b"\xff",
        b"{",
        b"[]",
        b"null",
    ],
)
def test_raw_duplicate_numeric_unicode_and_shape_errors_never_reach_effects(
    route: str, raw: bytes
) -> None:
    boundary = ParsingBoundary()
    with client_for(boundary) as client:
        response = client.post(
            PREFIX + "/" + route, content=raw, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "device_contract_invalid"
    assert set(response.json()["error"]) == {"code", "message", "request_id", "retryable"}
    assert (
        response.headers["Cache-Control"] == "no-store" and response.headers["Pragma"] == "no-cache"
    )
    assert boundary.effects == 0


@pytest.mark.parametrize("route", ROUTES)
def test_raw_limit_including_chunked_input_precedes_json_parse(route: str) -> None:
    boundary = ParsingBoundary()
    with client_for(boundary) as client:
        for raw in (b" " * 16385, iter([b" " * 8192, b" " * 8193])):
            response = client.post(
                PREFIX + "/" + route, content=raw, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "device_contract_invalid"
            assert response.headers["Pragma"] == "no-cache"
    assert boundary.effects == 0


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Type", "text/plain")],
        [("Content-Type", "application/json"), ("Content-Type", "application/json")],
    ],
)
def test_content_type_must_have_one_json_occurrence(
    route: str, headers: list[tuple[str, str]]
) -> None:
    boundary = ParsingBoundary()
    with client_for(boundary) as client:
        response = client.post(PREFIX + "/" + route, content=b"{}", headers=headers)
    assert response.status_code == 400 and boundary.effects == 0
    assert response.headers["Cache-Control"] == "no-store"


def test_only_four_post_paths_exist_and_missing_identity_fails_closed() -> None:
    with client_for(None) as client:
        for route in ROUTES:
            response = client.post(PREFIX + "/" + route, json={})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "device_contract_unavailable"
            assert response.headers["Pragma"] == "no-cache"
            assert client.get(PREFIX + "/" + route).status_code == 405
        for route in ("complete", "challenge", "claim", "rotate", "outcome", "arbitrary"):
            assert client.post(PREFIX + "/" + route, json={}).status_code == 404


def test_exact_raw_bytes_and_same_trusted_source_are_forwarded_without_reencoding() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[bytes, str]] = []

        def register(self, raw: bytes, *, source: str) -> dict[str, object]:
            self.calls.append((raw, source))
            raise ProfilePairingError("account_authentication_failed")

        register_challenge = login_challenge = login = register

    recorder, raw = Recorder(), b' { "request" : {}, "proof" : "AA==" } '
    with client_for(recorder, canonical_source=lambda request: "same-v1-trusted-source") as client:
        for route in ROUTES:
            assert (
                client.post(
                    PREFIX + "/" + route, content=raw, headers={"Content-Type": "application/json"}
                ).status_code
                == 401
            )
    assert recorder.calls == [(raw, "same-v1-trusted-source")] * 4


@pytest.mark.parametrize(
    "exception",
    [SQLAlchemyError("private proof/body parameters"), ValueError("private ROOT proof")],
)
def test_internal_exception_text_is_never_returned_or_logged(
    exception: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingBoundary:
        def register(self, raw: bytes, *, source: str) -> dict[str, object]:
            raise exception

        register_challenge = login_challenge = login = register

    with client_for(FailingBoundary()) as client:
        response = client.post(PREFIX + "/register", json={})
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "device_contract_unavailable"
    )
    assert "private" not in response.text and "private" not in caplog.text
    assert response.headers["Pragma"] == "no-cache"


def test_factory_retains_disabled_handlers_same_m5_owner_and_independent_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES",
        frozenset({"portable"}),
    )
    # Validated synthetic settings construct composition without a DB connection.
    # This does not qualify migration availability or runtime behavior.
    engine = create_engine("sqlite://")
    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:fixture@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("d1-fixture-access-issuer-secret-at-least-thirty-two-bytes"),
        public_access_source_hmac_secret=SecretStr(
            "d1-fixture-source-secret-at-least-thirty-two-bytes"
        ),
        auth_issuer="fixture",
        auth_audience="fixture",
        access_token_ttl_seconds=600,
        device_compatibility_v2_enabled=False,
        portable_accounts_enabled=False,
    )
    identity = cast(ProfilePairingService, object())
    try:
        assert build_device_portable_v2_service(settings, engine, identity=None) is None
        service = build_device_portable_v2_service(settings, engine, identity=identity)
        assert service is not None and service._identity is identity
        for v2, portable in ((False, False), (True, False), (False, True)):
            configured = settings.model_copy(
                update={
                    "device_compatibility_v2_enabled": v2,
                    "portable_accounts_enabled": portable,
                }
            )
            configured_service = build_device_portable_v2_service(
                configured, engine, identity=identity
            )
            assert configured_service is not None
            with pytest.raises(Exception, match="device_contract_unavailable"):
                configured_service._require_enabled()
        enabled = settings.model_copy(
            update={"device_compatibility_v2_enabled": True, "portable_accounts_enabled": True}
        )
        enabled_service = build_device_portable_v2_service(enabled, engine, identity=identity)
        assert enabled_service is not None
        enabled_service._require_enabled()
    finally:
        engine.dispose()
