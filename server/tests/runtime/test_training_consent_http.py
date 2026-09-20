"""Consent transport bounds, private errors and authenticated caller scope."""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pytest
from autplay.application.training_consent import TrainingConsentService
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.training_consent_http import create_training_consent_router
from autplay.runtime.http import ApiError, error_response
from fastapi import FastAPI, Request
from starlette.requests import HTTPConnection
from starlette.testclient import TestClient

ACTOR = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)


def test_feature_is_default_off_and_production_accepts_execution_enforcement(
    tmp_path: Path,
) -> None:
    from autplay.runtime.settings import ApiSettings, RuntimeProfile
    from pydantic import SecretStr

    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://autplay:test@localhost:5432/autplay"),
        auth_signing_secret=SecretStr("test-secret-at-least-32-bytes-long"),
        public_access_source_hmac_secret=SecretStr("test-source-secret-at-least-32-bytes-long"),
    )
    assert settings.shared_training_consent_enabled is False
    production = ApiSettings.model_validate(
        {
            **settings.model_dump(),
            "profile": RuntimeProfile.PRODUCTION,
            "shared_training_consent_enabled": True,
            "privacy_ledger_path": tmp_path / "ledger.sqlite3",
            "privacy_ledger_key": SecretStr("l" * 32),
            "privacy_ledger_key_id": "test",
            "training_consent_ledger_path": tmp_path / "consent.sqlite3",
            "training_consent_ledger_key": SecretStr("c" * 32),
            "training_consent_ledger_key_id": "test",
        }
    )
    assert production.shared_training_consent_enabled is True


class Service:
    def get(self, principal: Principal) -> dict[str, object]:
        assert principal == ACTOR
        return {
            "schema_version": 1,
            "account_id": str(principal.user_id),
            "decision": "UNKNOWN",
            "revision": 0,
            "policy_version": 1,
            "changed_at": None,
        }

    def decide(self, principal: Principal, body: dict[str, object]) -> dict[str, object]:
        assert principal == ACTOR
        return body


def client(service: object | None = Service()) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def handle(connection: HTTPConnection, error: ApiError):  # type: ignore[no-untyped-def]
        return error_response(
            request_id="test",
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            retryable=error.retryable,
            headers=error.headers,
        )

    def auth(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer test":
            raise ApiError("authentication_required", "Authentication required.", 401)
        request.state.principal = ACTOR

    app.include_router(
        create_training_consent_router(
            cast(TrainingConsentService | None, service), authenticated=auth
        )
    )
    return TestClient(app)


class HttpResponse(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...


def private(response: HttpResponse) -> None:
    assert "no-store" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]
    assert response.headers["vary"] == "Authorization"
    assert response.headers["pragma"] == "no-cache"


def test_read_is_caller_owned_and_auth_errors_are_private() -> None:
    with client() as http:
        response = http.get("/privacy/shared-training", headers={"Authorization": "Bearer test"})
        assert response.status_code == 200
        assert response.json()["account_id"] == str(ACTOR.user_id)
        private(response)
        denied = http.get("/privacy/shared-training")
        assert denied.status_code == 401
        private(denied)


@pytest.mark.parametrize(
    "body",
    [
        "[1]",
        '{"decision":"GRANTED","decision":"DENIED"}',
        '{"revision":NaN}',
        "{" + " " * 1025 + "}",
    ],
)
def test_invalid_and_oversized_bodies_never_reach_service(body: str) -> None:
    with client() as http:
        response = http.put(
            "/privacy/shared-training",
            content=body,
            headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        private(response)


def test_disabled_capability_is_private_and_wrong_content_type_is_rejected() -> None:
    with client(None) as http:
        response = http.get("/privacy/shared-training", headers={"Authorization": "Bearer test"})
        assert response.status_code == 503
        private(response)
    with client() as http:
        response = http.put(
            "/privacy/shared-training",
            content="{}",
            headers={"Authorization": "Bearer test", "Content-Type": "text/plain"},
        )
        assert response.status_code == 422
        private(response)


def test_independent_evidence_failure_is_private_503() -> None:
    from autplay.domain.training_consent import TrainingConsentEvidenceError

    class Unavailable(Service):
        def get(self, principal: Principal) -> dict[str, object]:
            raise TrainingConsentEvidenceError()

    with client(Unavailable()) as http:
        response = http.get("/privacy/shared-training", headers={"Authorization": "Bearer test"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "training_consent_evidence_unavailable"
        private(response)
