"""Source fixtures for unavailable identity, bearer headers and exact read routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.application.device_compatibility import DeviceCompatibilityService
from autplay.domain.auth import AccessTokenClaims, AccountRole, InvalidAccessTokenError, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.entrypoints.device_compatibility_http import create_device_compatibility_router
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient


class UnavailableService:
    def discovery(self) -> dict[str, object]:
        raise DeviceCompatibilityError()

    def capabilities(self, principal: Principal, claims: AccessTokenClaims) -> dict[str, object]:
        raise DeviceCompatibilityError()


def authenticate(token: str) -> tuple[Principal, AccessTokenClaims]:
    if token != "synthetic-valid-bearer":
        raise InvalidAccessTokenError()
    principal = Principal(UUID(int=1), UUID(int=2), UUID(int=3), AccountRole.USER)
    now = datetime(2026, 10, 16, tzinfo=UTC)
    return principal, AccessTokenClaims(
        principal.user_id,
        principal.device_id,
        principal.session_id,
        uuid4(),
        now,
        now,
        now + timedelta(minutes=10),
    )


def _client(service: DeviceCompatibilityService | None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_device_compatibility_router(service, authenticate=authenticate))
    return TestClient(app)


@pytest.mark.parametrize("configured", [False, True])
def test_missing_or_unavailable_identity_returns_explicit_no_store_unavailable(
    configured: bool,
) -> None:
    service = cast(DeviceCompatibilityService, UnavailableService()) if configured else None
    with _client(service) as client:
        for path in ("discovery", "capabilities"):
            response = client.get(
                "/device-compatibility/v2/" + path,
                headers={"Authorization": "Bearer synthetic-valid-bearer"},
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "device_contract_unavailable"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Basic synthetic")],
        [("Authorization", "Bearer")],
        [("Authorization", "Bearer invalid")],
        [
            ("Authorization", "Bearer synthetic-valid-bearer"),
            ("Authorization", "Bearer synthetic-valid-bearer"),
        ],
    ],
)
def test_capabilities_requires_one_active_bearer_even_with_empty_families(
    headers: list[tuple[str, str]],
) -> None:
    with _client(cast(DeviceCompatibilityService, UnavailableService())) as client:
        response = client.get("/device-compatibility/v2/capabilities", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"


def test_unimplemented_families_are_absent_and_unknown_methods_remain_method_not_allowed() -> None:
    with _client(None) as client:
        assert client.post("/device-compatibility/v2/discovery").status_code == 405
        for path in ("enrollment/exchanges", "sessions/rotate", "arbitrary/operation"):
            response = client.post("/device-compatibility/v2/" + path, json={})
            assert response.status_code == 404
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"
