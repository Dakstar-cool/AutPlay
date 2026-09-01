"""PA2 HTTP response and redaction checks without PostgreSQL."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.public_access_http import (
    build_exact_proxy_source_resolver,
    create_public_access_router,
)
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "public-access" / "v1" / "schema-examples.json"


def _example(name: str) -> dict[str, Any]:
    records = json.loads(FIXTURES.read_text(encoding="utf-8"))["examples"]
    return cast(
        dict[str, Any],
        copy.deepcopy(next(row["instance"] for row in records if row["schema"] == name)),
    )


class _Service:
    last_source: str | None = "not-called"

    def create_invitation(
        self, _: Principal, __: dict[str, object]
    ) -> tuple[dict[str, object], bool]:
        return _example("account-invitation-document.schema.json"), False

    def list_invitations(self, _: Principal, __: int, ___: str | None) -> dict[str, object]:
        return _example("account-invitation-page.schema.json")

    def cancel_invitation(
        self, _: Principal, __: UUID, ___: dict[str, object]
    ) -> dict[str, object]:
        result = _example("account-lifecycle-result.schema.json")
        result["target_type"] = "ACCOUNT_INVITATION"
        return result

    def redeem(self, _: dict[str, object], source: str | None) -> tuple[dict[str, object], bool]:
        self.last_source = source
        return _example("account-registration-response.schema.json"), False

    def list_accounts(self, _: Principal, __: int, ___: str | None) -> dict[str, object]:
        return _example("invited-account-page.schema.json")

    def disable_account(self, _: Principal, __: UUID, ___: dict[str, object]) -> dict[str, object]:
        return _example("account-lifecycle-result.schema.json")


def _authenticated(_: Request) -> Principal:
    return Principal(
        uuid := UUID("11111111-1111-4111-8111-111111111111"), uuid, uuid, AccountRole.OWNER
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_public_access_router(cast(Any, _Service()), authenticated=_authenticated)
    )
    return TestClient(app)


def test_six_operations_are_no_store_and_keep_invitation_secret_out_of_replay(
    client: TestClient,
) -> None:
    create = _example("account-invitation-create.schema.json")
    lifecycle = _example("account-lifecycle-command.schema.json")
    registration = _example("account-registration-request.schema.json")
    registration["invitation_secret"] = "A" * 43
    invitation_id = "22222222-2222-4222-8222-222222222222"
    user_id = "66666666-6666-4666-8666-666666666666"
    cases = (
        client.post("/public-access/account-invitations", json=create),
        client.get("/public-access/account-invitations"),
        client.post(f"/public-access/account-invitations/{invitation_id}/cancel", json=lifecycle),
        client.post("/public-access/account-invitations/redeem", json=registration),
        client.get("/public-access/accounts"),
        client.post(f"/public-access/accounts/{user_id}/disable", json=lifecycle),
    )
    for response in cases:
        assert response.status_code in {200, 201}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
    assert "invitation_secret" not in client.get("/public-access/account-invitations").text


@pytest.mark.parametrize(
    "display_name",
    ("   ", "friend\x00name", "friend\nname", "friend\u202ename", "friend\u2066name"),
)
def test_invitation_display_name_rejects_empty_control_and_bidi_values(
    client: TestClient, display_name: str
) -> None:
    request = _example("account-invitation-create.schema.json")
    request["account_display_name"] = display_name

    response = client.post("/public-access/account-invitations", json=request)

    assert response.status_code == 422


def test_invitation_display_name_is_trimmed_before_service_dispatch() -> None:
    class CapturingService(_Service):
        display_name: object | None = None

        def create_invitation(
            self, _: Principal, body: dict[str, object]
        ) -> tuple[dict[str, object], bool]:
            self.display_name = body["account_display_name"]
            return super().create_invitation(_, body)

    service = CapturingService()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_public_access_router(cast(Any, service), authenticated=_authenticated)
    )
    request = _example("account-invitation-create.schema.json")
    request["account_display_name"] = "\u2003 friend \u2003"

    response = TestClient(app).post("/public-access/account-invitations", json=request)

    assert response.status_code == 201
    assert service.display_name == "friend"


def test_http_uses_global_source_fallback_until_a_trusted_edge_resolver_is_injected() -> None:
    service = _Service()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_public_access_router(cast(Any, service), authenticated=_authenticated)
    )
    request = _example("account-registration-request.schema.json")
    request["invitation_secret"] = "A" * 43

    response = TestClient(app).post(
        "/public-access/account-invitations/redeem",
        json=request,
        headers={"X-Forwarded-For": "203.0.113.55"},
    )

    assert response.status_code == 201
    assert service.last_source is None


@pytest.mark.parametrize(
    ("peer", "headers", "expected"),
    (
        ("172.30.77.2", [(b"x-autplay-client-ip", b"203.0.113.55")], "203.0.113.55"),
        ("172.30.77.2", [(b"x-autplay-client-ip", b"2001:db8::5")], "2001:db8::5"),
        ("172.30.77.2", [(b"x-autplay-client-ip", b"::ffff:192.0.2.9")], "192.0.2.9"),
        ("172.30.77.3", [(b"x-autplay-client-ip", b"203.0.113.55")], None),
        ("172.30.77.2", [], None),
        (
            "172.30.77.2",
            [
                (b"x-autplay-client-ip", b"203.0.113.55"),
                (b"x-autplay-client-ip", b"198.51.100.7"),
            ],
            None,
        ),
        ("172.30.77.2", [(b"x-autplay-client-ip", b"203.0.113.55, 198.51.100.7")], None),
        ("172.30.77.2", [(b"x-autplay-client-ip", b"[2001:db8::5]:443")], None),
        ("172.30.77.2", [(b"x-autplay-client-ip", b"fe80::1%eth0")], None),
        ("172.30.77.2", [(b"x-autplay-client-ip", b" 203.0.113.55")], None),
    ),
)
def test_exact_proxy_source_resolver_rejects_spoofed_or_ambiguous_evidence(
    peer: str, headers: list[tuple[bytes, bytes]], expected: str | None
) -> None:
    resolver = build_exact_proxy_source_resolver("172.30.77.2")
    assert resolver is not None
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/public-access/account-invitations/redeem",
            "headers": headers,
            "client": (peer, 55123),
        }
    )

    assert resolver(request) == expected


def test_redemption_rejects_authorization_cookie_and_oversized_body_without_cache(
    client: TestClient,
) -> None:
    request = _example("account-registration-request.schema.json")
    request["invitation_secret"] = "A" * 43
    cases = (
        client.post(
            "/public-access/account-invitations/redeem",
            json=request,
            headers={"Authorization": "Bearer x"},
        ),
        client.post(
            "/public-access/account-invitations/redeem", json=request, cookies={"session": "x"}
        ),
        client.post(
            "/public-access/account-invitations/redeem",
            content=b"x" * (16 * 1024 + 1),
            headers={"content-type": "application/json"},
        ),
    )
    for response in cases:
        assert response.status_code in {401, 413}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
