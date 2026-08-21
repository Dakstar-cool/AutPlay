"""M5B HTTP boundary checks independent of a running PostgreSQL instance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.profile_pairing_http import create_profile_pairing_router
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "contracts" / "profile-pairing" / "v1"
FIXTURES = ROOT / "tests" / "fixtures" / "profile-pairing" / "v1" / "schema-examples.json"


def _example(name: str) -> dict[str, Any]:
    examples = json.loads(FIXTURES.read_text(encoding="utf-8"))["examples"]
    return cast(
        dict[str, Any],
        copy.deepcopy(next(item["instance"] for item in examples if item["schema"] == name)),
    )


class _Service:
    def discovery(self) -> dict[str, object]:
        return _example("discovery-metadata.schema.json")

    def capabilities(self, _: Principal) -> dict[str, object]:
        return _example("capabilities.schema.json")

    def issue_invitation(self, *_: object) -> dict[str, object]:
        return _example("enrollment-invitation.schema.json")

    def exchange(self, _: dict[str, object]) -> tuple[dict[str, object], bool]:
        return _example("enrollment-exchange-response.schema.json"), False

    def cancel_invitation(self, *_: object) -> dict[str, object]:
        return _example("lifecycle-result.schema.json")

    def list_devices(self, _: Principal) -> dict[str, object]:
        return _example("device-list.schema.json")

    def list_sessions(self, _: Principal) -> dict[str, object]:
        return _example("session-list.schema.json")

    def rotate(self, _: dict[str, object]) -> tuple[dict[str, object], bool]:
        return _example("session-rotation-response.schema.json"), False

    def logout_current(self, *_: object) -> dict[str, object]:
        return _example("lifecycle-result.schema.json")

    def logout_all(self, *_: object) -> dict[str, object]:
        return _example("lifecycle-result.schema.json")

    def revoke_device(self, *_: object) -> dict[str, object]:
        return _example("lifecycle-result.schema.json")


def _authenticated(request: Request) -> None:
    request.state.principal = Principal(
        UUID("22222222-2222-4222-8222-222222222222"),
        UUID("33333333-3333-4333-8333-333333333333"),
        UUID("66666666-6666-4666-8666-666666666666"),
        AccountRole.OWNER,
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_profile_pairing_router(cast(Any, _Service()), authenticated=_authenticated)
    )
    return TestClient(app)


def _validate(schema_name: str, document: Any) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    _assert_contract_shape(schema, document)


def _assert_contract_shape(schema: dict[str, Any], value: Any) -> None:
    """Validate the OpenAPI-linked response shape without adding a server dependency."""
    expected_type = schema.get("type")
    if expected_type == "object":
        assert isinstance(value, dict)
        required = schema.get("required", [])
        assert all(key in value for key in required)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties)
        for key, nested in properties.items():
            if key in value:
                _assert_contract_shape(nested, value[key])
    elif expected_type == "array":
        assert isinstance(value, list)
        items = schema.get("items")
        if isinstance(items, dict):
            for item in value:
                _assert_contract_shape(items, item)
    elif expected_type == "string":
        assert isinstance(value, str)
    elif expected_type == "integer":
        assert isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "boolean":
        assert isinstance(value, bool)
    if "const" in schema:
        assert value == schema["const"]
    if "enum" in schema:
        assert value in schema["enum"]


def test_every_runtime_success_response_matches_its_openapi_schema_and_is_no_store(
    client: TestClient,
) -> None:
    invitation = _example("create-invitation-request.schema.json")
    exchange = _example("enrollment-exchange-request.schema.json")
    rotation = _example("session-rotation-request.schema.json")
    lifecycle = _example("lifecycle-command.schema.json")
    invitation_id = _example("enrollment-invitation.schema.json")["invitation_id"]
    device_id = _example("device-list.schema.json")["devices"][0]["device_id"]
    cases = (
        ("get", "/pairing/discovery", None, "discovery-metadata.schema.json"),
        ("get", "/profile/capabilities", None, "capabilities.schema.json"),
        (
            "post",
            "/pairing/enrollment/invitations",
            invitation,
            "enrollment-invitation.schema.json",
        ),
        (
            "post",
            "/pairing/enrollment/exchanges",
            exchange,
            "enrollment-exchange-response.schema.json",
        ),
        (
            "post",
            f"/pairing/enrollment/invitations/{invitation_id}/cancel",
            lifecycle,
            "lifecycle-result.schema.json",
        ),
        ("get", "/account/devices", None, "device-list.schema.json"),
        ("get", "/account/sessions", None, "session-list.schema.json"),
        ("post", "/account/sessions/rotate", rotation, "session-rotation-response.schema.json"),
        ("post", "/account/sessions/current/logout", lifecycle, "lifecycle-result.schema.json"),
        ("post", "/account/sessions/logout-all", lifecycle, "lifecycle-result.schema.json"),
        ("post", f"/account/devices/{device_id}/revoke", lifecycle, "lifecycle-result.schema.json"),
    )
    for method, path, payload, schema in cases:
        response = client.request(method.upper(), path, json=payload)
        assert response.status_code in {200, 201}
        _validate(schema, response.json())
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"


def test_pairing_errors_and_request_validation_are_non_cacheable(client: TestClient) -> None:
    unavailable = TestClient(FastAPI())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_profile_pairing_router(None, authenticated=_authenticated))
    unavailable = TestClient(app)
    for response in (
        unavailable.get("/pairing/discovery"),
        client.post("/pairing/enrollment/exchanges", json={}),
    ):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
