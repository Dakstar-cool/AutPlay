"""Owner-only Android contract for manual release monitoring."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from autplay.adapters.postgresql.release_watch import ArtistWatch
from autplay.adapters.postgresql.watch_auto_download_consent import (
    WatchAutoDownloadError,
    WatchAutoDownloadState,
)
from autplay.application.discovery_automation import AUTO_IMPORT_CONFIRMATION
from autplay.application.release_watch import ReleaseWatchService
from autplay.domain.auth import AccountRole, OwnedObjectNotFoundError, Principal
from autplay.entrypoints.release_watch_api import create_release_watch_router
from autplay.runtime.http import install_error_handlers

OWNER = UUID("11111111-1111-4111-8111-111111111111")
ARTIST = UUID("22222222-2222-4222-8222-222222222222")


class _Watch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, UUID | None]] = []
        self.auto_state = WatchAutoDownloadState(False, 0, False)

    def followed_artists(
        self, principal: Principal, *, limit: int, after_artist_id: UUID | None
    ) -> list[ArtistWatch]:
        assert limit == 100
        self.calls.append(("list", principal.user_id, after_artist_id))
        return [ArtistWatch(ARTIST, "MANUAL_FOLLOW", 0, None)]

    def excluded_artists(
        self, principal: Principal, *, limit: int, after_artist_id: UUID | None
    ) -> list[UUID]:
        assert limit == 100
        self.calls.append(("exclusions", principal.user_id, after_artist_id))
        return [ARTIST]

    def follow(self, principal: Principal, artist_id: UUID) -> int:
        self.calls.append(("follow", principal.user_id, artist_id))
        if artist_id != ARTIST:
            raise OwnedObjectNotFoundError()
        return 1

    def unfollow(self, principal: Principal, artist_id: UUID) -> int:
        self.calls.append(("unfollow", principal.user_id, artist_id))
        return 2

    def auto_download(self, principal: Principal) -> WatchAutoDownloadState:
        self.calls.append(("auto_get", principal.user_id, None))
        return self.auto_state

    def set_auto_download(
        self,
        principal: Principal,
        *,
        enabled: bool,
        expected_revision: int,
        confirmation_code: str | None,
    ) -> WatchAutoDownloadState:
        self.calls.append(("auto_set", principal.user_id, None))
        if enabled and not self.auto_state.execution_enabled:
            raise WatchAutoDownloadError("release_watch_auto_download_unavailable")
        assert expected_revision == self.auto_state.revision
        assert confirmation_code == (AUTO_IMPORT_CONFIRMATION if enabled else None)
        self.auto_state = WatchAutoDownloadState(
            enabled, self.auto_state.revision + 1, self.auto_state.execution_enabled
        )
        return self.auto_state


def _client(watch: _Watch) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    def authenticated(request: Request) -> None:
        request.state.principal = Principal(OWNER, UUID(int=1), UUID(int=2), AccountRole.USER)

    app.include_router(
        create_release_watch_router(cast(ReleaseWatchService, watch), authenticated=authenticated)
    )
    return TestClient(app)


def test_follow_list_exclude_and_unfollow_use_owner_principal() -> None:
    watch = _Watch()
    client = _client(watch)
    followed = client.put(f"/release-watch/artists/{ARTIST}")
    assert followed.status_code == 200
    assert followed.json() == {
        "contract_version": "release-watch-v1",
        "schema_version": 1,
        "artist_id": str(ARTIST),
        "revision": 1,
    }
    assert followed.headers["cache-control"] == "private, no-store, max-age=0"
    listed = client.get("/release-watch/artists")
    assert listed.json()["artists"] == [
        {
            "artist_id": str(ARTIST),
            "reason": "MANUAL_FOLLOW",
            "score_milli": 0,
            "favorite_generation": None,
            "auto_download": {
                "watch_policy_id": None,
                "provider_artist_id": None,
                "policy_revision": None,
                "policy_import_mode": None,
                "effective_opt_in": False,
                "execution_enabled": False,
                "reason": "WATCH_POLICY_PENDING",
            },
        }
    ]
    assert client.get("/release-watch/exclusions").json()["artist_ids"] == [str(ARTIST)]
    assert client.delete(f"/release-watch/artists/{ARTIST}").json()["revision"] == 2
    assert [call[1] for call in watch.calls] == [OWNER] * 4


def test_invalid_artist_and_limit_do_not_call_storage() -> None:
    watch = _Watch()
    client = _client(watch)
    assert client.get("/release-watch/artists?limit=10000").status_code == 422
    assert watch.calls == []
    missing = client.put(f"/release-watch/artists/{UUID(int=3)}")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "private, no-store, max-age=0"


def test_auto_download_contract_rejects_preconsent_and_allows_disable() -> None:
    watch = _Watch()
    client = _client(watch)
    initial = client.get("/release-watch/auto-download")
    assert initial.json() == {
        "contract_version": "release-watch-v1",
        "schema_version": 1,
        "enabled": False,
        "revision": 0,
        "execution_enabled": False,
        "consequence_confirmation": AUTO_IMPORT_CONFIRMATION,
    }
    assert initial.headers["cache-control"] == "private, no-store, max-age=0"
    denied = client.put(
        "/release-watch/auto-download",
        json={
            "enabled": True,
            "expected_revision": 0,
            "consequence_confirmation": AUTO_IMPORT_CONFIRMATION,
        },
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "release_watch_auto_download_unavailable"
    assert watch.auto_state.revision == 0
    disabled = client.put(
        "/release-watch/auto-download",
        json={"enabled": False, "expected_revision": 0},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert [call[1] for call in watch.calls] == [OWNER] * 3


def test_auto_download_api_accepts_confirmed_owner_choice_when_gate_is_on() -> None:
    watch = _Watch()
    watch.auto_state = WatchAutoDownloadState(False, 0, True)
    client = _client(watch)
    enabled = client.put(
        "/release-watch/auto-download",
        json={
            "enabled": True,
            "expected_revision": 0,
            "consequence_confirmation": AUTO_IMPORT_CONFIRMATION,
        },
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["revision"] == 1
    assert enabled.headers["cache-control"] == "private, no-store, max-age=0"
    disabled = client.put(
        "/release-watch/auto-download",
        json={"enabled": False, "expected_revision": 1},
    )
    assert disabled.status_code == 200 and disabled.json()["revision"] == 2
    invalid = client.put(
        "/release-watch/auto-download",
        json={"enabled": "true", "expected_revision": 2},
    )
    assert invalid.status_code == 422
    assert [call[0] for call in watch.calls] == ["auto_set", "auto_set"]
