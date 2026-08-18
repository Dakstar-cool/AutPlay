from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.api import create_app
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from starlette.testclient import TestClient

_SETTINGS = ApiSettings(
    database_url=SecretStr("postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"),
    auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
)
_OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)


class Auth:
    def authenticate_access(self, token: str) -> Principal:
        if token != "good":
            from autplay.domain.auth import InvalidAccessTokenError

            raise InvalidAccessTokenError()
        return _OWNER


@dataclass
class Entry:
    library_entry_id: object = uuid4()
    user_track_ref_id: object = uuid4()
    source: str = "LOCAL"
    availability_status: str = "LOCAL"
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    row_version: int = 1


@dataclass
class Playlist:
    playlist_id: object = field(default_factory=uuid4)
    name: str = "P07"
    description: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    row_version: int = 1


class Queries:
    def query_library(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: object | None = None,
    ) -> list[object]:
        assert principal == _OWNER and limit <= 101
        if before is not None:
            assert before_id is not None
            return []
        return [Entry(), Entry()]

    def query_search(self, principal: Principal, query: str, limit: int) -> list[object]:
        assert principal == _OWNER and query == "song" and limit <= 100
        return []

    def query_playlists(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: object | None = None,
    ) -> list[object]:
        if before is not None:
            assert before_id is not None
            return []
        return [Playlist(), Playlist()]

    def query_history(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: object | None = None,
    ) -> list[object]:
        return []


def test_library_queries_are_authenticated_bounded_and_read_only() -> None:
    app = create_app(_SETTINGS, auth_service=Auth(), library_service=Queries())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/library/entries?limit=1", headers={"Authorization": "Bearer good"}
        )
        search = client.get(
            "/api/v1/library/search?q=song", headers={"Authorization": "Bearer good"}
        )
        blank_search = client.get(
            "/api/v1/library/search?q=%20", headers={"Authorization": "Bearer good"}
        )
        playlists = client.get(
            "/api/v1/library/playlists?limit=1", headers={"Authorization": "Bearer good"}
        )
        write = client.post("/api/v1/library/entries", headers={"Authorization": "Bearer good"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["availability_status"] == "LOCAL"
    cursor = response.json()["next_cursor"]
    assert isinstance(cursor, str)
    with TestClient(app) as client:
        next_page = client.get(
            f"/api/v1/library/entries?limit=1&cursor={cursor}",
            headers={"Authorization": "Bearer good"},
        )
        malformed = client.get(
            "/api/v1/library/entries?cursor=bad", headers={"Authorization": "Bearer good"}
        )
    assert next_page.status_code == 200 and next_page.json()["items"] == []
    assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "cursor_invalid"
    assert search.status_code == 200
    assert blank_search.status_code == 422
    assert blank_search.json()["error"]["code"] == "search_query_invalid"
    assert isinstance(playlists.json()["next_cursor"], str)
    with TestClient(app) as client:
        playlist_page = client.get(
            f"/api/v1/library/playlists?limit=1&cursor={playlists.json()['next_cursor']}",
            headers={"Authorization": "Bearer good"},
        )
    assert playlist_page.status_code == 200 and playlist_page.json()["items"] == []
    assert write.status_code == 405
