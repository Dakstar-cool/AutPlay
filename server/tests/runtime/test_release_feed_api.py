"""Read-only Home feed HTTP contract and private owner boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from autplay.adapters.postgresql.release_feed import ReleaseFeedItem
from autplay.application.release_feed import (
    InvalidReleaseFeedCursor,
    ReleaseFeedPage,
    ReleaseFeedService,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.release_feed_api import create_release_feed_router
from autplay.runtime.http import install_error_handlers

OWNER = UUID("11111111-1111-4111-8111-111111111111")
ARTIST = UUID("22222222-2222-4222-8222-222222222222")
CANDIDATE = UUID("33333333-3333-4333-8333-333333333333")
PROVIDER = UUID("44444444-4444-4444-8444-444444444444")
RUN = UUID("55555555-5555-4555-8555-555555555555")


class _Feed:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, str | None]] = []

    def page(self, principal: Principal, *, limit: int, cursor: str | None) -> ReleaseFeedPage:
        self.calls.append((principal.user_id, limit, cursor))
        if cursor == "bad":
            raise InvalidReleaseFeedCursor("invalid")
        item = ReleaseFeedItem(
            CANDIDATE,
            ARTIST,
            "Artist",
            "Release",
            datetime(2026, 9, 24, tzinfo=UTC),
            "MANUAL_FOLLOW",
            "DISCOVERED",
            0,
            None,
            None,
            None,
            PROVIDER,
            RUN,
            "1.0.0",
            0,
            1,
            3,
        )
        return ReleaseFeedPage((item,), None)


def test_private_feed_contract_uses_authenticated_owner() -> None:
    feed = _Feed()
    app = FastAPI()
    install_error_handlers(app)

    def authenticated(request: Request) -> None:
        request.state.principal = Principal(OWNER, UUID(int=1), UUID(int=2), AccountRole.USER)

    app.include_router(
        create_release_feed_router(cast(ReleaseFeedService, feed), authenticated=authenticated)
    )
    client = TestClient(app)
    response = client.get("/release-watch/feed?limit=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["vary"] == "Authorization"
    assert response.json()["ranking_version"] == "owner-local-v1"
    item = response.json()["items"][0]
    assert item["state"] == "DISCOVERED"
    assert item["reason"] == "MANUAL_FOLLOW"
    assert item["recording_id"] is None and item["user_track_ref_id"] is None
    assert item["available_action"] == "OPEN_DISCOVERY"
    assert "license_url" not in item and "share_url" not in item
    assert feed.calls == [(OWNER, 1, None)]
    invalid = client.get("/release-watch/feed?cursor=bad")
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "private, no-store, max-age=0"
