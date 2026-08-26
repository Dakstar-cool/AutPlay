"""Authenticated HTTP evidence for the bounded S1C social surface."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import uuid4

from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.social_http import create_social_router
from autplay.runtime.http import ApiError, error_response
from fastapi import FastAPI, Request
from starlette.requests import HTTPConnection
from starlette.testclient import TestClient

OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)


class Service:
    settings_body: dict[str, object] | None = None
    profile_statistics_settings_body: dict[str, object] | None = None
    profile_statistics_denied = False

    def snapshot(self, principal: Principal, now: datetime) -> dict[str, object]:
        assert principal == OWNER
        return {
            "friends": [],
            "incoming_requests": [],
            "outgoing_requests": [],
            "blocked": [],
            "sent_room_invitations": [],
            "received_room_invitations": [],
            "presence_settings": {
                "friend_presence_visibility_enabled": False,
                "room_activity_sharing_enabled": False,
                "invite_availability_enabled": False,
            },
        }

    def presence_page(self, principal: Principal, now: datetime) -> dict[str, object]:
        assert principal == OWNER
        return {"items": []}

    def set_settings(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, bool]:
        assert principal == OWNER
        self.settings_body = body
        return {
            "friend_presence_visibility_enabled": bool(body["friend_presence_visibility_enabled"]),
            "room_activity_sharing_enabled": bool(body["room_activity_sharing_enabled"]),
            "invite_availability_enabled": bool(body["invite_availability_enabled"]),
        }

    def command(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, object]:
        raise SocialError("active_room_exit_required", details={"room_count": 2})

    def get_profile_statistics_settings(
        self, principal: Principal, now: datetime
    ) -> dict[str, object]:
        assert principal == OWNER
        return {
            "schema_version": 1,
            "friends_can_view_statistics": False,
            "revision": 0,
        }

    def set_profile_statistics_settings(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, object]:
        assert principal == OWNER
        self.profile_statistics_settings_body = body
        return {
            "schema_version": 1,
            "operation_id": str(body["operation_id"]),
            "friends_can_view_statistics": bool(body["friends_can_view_statistics"]),
            "revision": cast(int, body["expected_revision"]) + 1,
        }

    def friend_profile_statistics(
        self, principal: Principal, target: object, now: datetime
    ) -> dict[str, object]:
        assert principal == OWNER
        if self.profile_statistics_denied:
            raise SocialError("profile_statistics_unavailable")
        return {
            "schema_version": 1,
            "through_utc_date": "2026-08-24",
            "windows": [
                {
                    "window": f"LAST_{days}_COMPLETE_DAYS",
                    "play_session_count": 0,
                    "listened_ms": 0,
                    "unique_track_count": 0,
                }
                for days in (7, 30, 365)
            ],
        }


def _client(service: Service) -> TestClient:
    app = FastAPI()

    def principal_state(connection: HTTPConnection) -> None:
        connection.state.principal = OWNER

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError) -> object:
        return error_response(
            request_id="test-request",
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            retryable=error.retryable,
            headers=error.headers,
            details=error.details,
        )

    app.include_router(
        create_social_router(cast(SocialService, service), authenticated=principal_state)
    )
    return TestClient(app)


def test_snapshot_static_presence_route_and_exact_private_settings() -> None:
    service = Service()
    client = _client(service)
    snapshot = client.get("/social/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.headers["cache-control"] == "no-store"
    assert client.get("/social/friends/presence").json() == {"items": []}

    operation_id = uuid4()
    response = client.put(
        "/social/presence/settings",
        json={
            "operation_id": str(operation_id),
            "friend_presence_visibility_enabled": True,
            "room_activity_sharing_enabled": False,
            "invite_availability_enabled": True,
        },
    )
    assert response.status_code == 200
    assert service.settings_body == {
        "operation_id": str(operation_id),
        "friend_presence_visibility_enabled": True,
        "room_activity_sharing_enabled": False,
        "invite_availability_enabled": True,
    }
    assert (
        client.put(
            "/social/presence/settings",
            json={
                "operation_id": str(uuid4()),
                "friends_can_see_presence": True,
                "share_room_activity": False,
                "available_to_invite": True,
            },
        ).status_code
        == 422
    )


def test_active_room_block_exposes_only_bounded_count() -> None:
    client = _client(Service())
    response = client.post(
        "/social/friendships/commands",
        json={
            "operation_id": str(uuid4()),
            "action": "BLOCK_USER",
            "target_account_id": "11111111-1111-4111-8111-111111111111",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "active_room_exit_required"
    assert response.json()["error"]["room_count"] == 2


def test_profile_statistics_routes_are_strict_and_private_for_success_and_errors() -> None:
    service = Service()
    client = _client(service)
    private_headers = {
        "cache-control": "private, no-store, max-age=0",
        "pragma": "no-cache",
        "vary": "Authorization",
    }

    settings = client.get("/social/profile-statistics/settings")
    assert settings.status_code == 200
    assert settings.json() == {
        "schema_version": 1,
        "friends_can_view_statistics": False,
        "revision": 0,
    }
    assert {key: settings.headers[key] for key in private_headers} == private_headers

    operation_id = uuid4()
    updated = client.put(
        "/social/profile-statistics/settings",
        json={
            "operation_id": str(operation_id),
            "expected_revision": 0,
            "friends_can_view_statistics": True,
        },
    )
    assert updated.status_code == 200
    assert service.profile_statistics_settings_body == {
        "operation_id": str(operation_id),
        "expected_revision": 0,
        "friends_can_view_statistics": True,
    }
    assert {key: updated.headers[key] for key in private_headers} == private_headers

    invalid = client.put(
        "/social/profile-statistics/settings",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "friends_can_view_statistics": False,
            "publish_publicly": True,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_validation_failed"
    assert {key: invalid.headers[key] for key in private_headers} == private_headers

    target = uuid4()
    visible = client.get(f"/social/friends/{target}/profile-statistics")
    assert visible.status_code == 200
    assert len(visible.content) < 2048
    assert {key: visible.headers[key] for key in private_headers} == private_headers

    service.profile_statistics_denied = True
    unavailable = client.get(f"/social/friends/{target}/profile-statistics")
    assert unavailable.status_code == 404
    assert unavailable.json()["error"]["code"] == "profile_statistics_unavailable"
    assert {key: unavailable.headers[key] for key in private_headers} == private_headers
