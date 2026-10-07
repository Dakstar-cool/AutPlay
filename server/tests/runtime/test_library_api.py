from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from autplay.application.internet_music import InternetMusicService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.music_search import MusicSearchKind
from autplay.entrypoints.api import create_app
from autplay.entrypoints.music_http import create_music_router
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings
from fastapi import FastAPI, Request
from pydantic import SecretStr
from starlette.testclient import TestClient

_SETTINGS = ApiSettings(
    database_url=SecretStr("postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"),
    auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
    public_access_source_hmac_secret=SecretStr(
        "public-access-source-hmac-secret-at-least-32-bytes"
    ),
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


@dataclass
class TypedQueries(Queries):
    calls: list[tuple[str, int, MusicSearchKind]] = field(default_factory=list)
    result: Entry = field(default_factory=Entry)

    def query_search(
        self,
        principal: Principal,
        query: str,
        limit: int,
        *,
        kind: MusicSearchKind = MusicSearchKind.ALL,
    ) -> list[object]:
        assert principal == _OWNER
        self.calls.append((query, limit, kind))
        return [self.result]


def test_search_kinds_preserve_navigable_track_response_and_default() -> None:
    queries = TypedQueries()
    app = create_app(_SETTINGS, auth_service=Auth(), library_service=queries)  # type: ignore[arg-type]
    with TestClient(app) as client:
        default = client.get(
            "/api/v1/library/search",
            params={"q": "  AC/DC  Live  ", "limit": 1},
            headers={"Authorization": "Bearer good"},
        )
        for kind in MusicSearchKind:
            response = client.get(
                "/api/v1/library/search",
                params={"q": "  AC/DC  Live  ", "limit": 1, "kind": kind.value},
                headers={"Authorization": "Bearer good"},
            )
            assert response.status_code == 200
            assert response.json() == default.json()
            assert response.headers["cache-control"] == "no-store"
            assert queries.calls[-1] == ("AC/DC Live", 1, kind)
    assert default.status_code == 200
    assert default.json()["next_cursor"] is None
    assert default.json()["items"][0]["user_track_ref_id"] == str(queries.result.user_track_ref_id)
    assert default.json()["items"][0]["availability_status"] == "LOCAL"


def test_search_rejects_invalid_bounds_kinds_and_unauthenticated_queries() -> None:
    queries = TypedQueries()
    app = create_app(_SETTINGS, auth_service=Auth(), library_service=queries)  # type: ignore[arg-type]
    with TestClient(app) as client:
        for params in (
            {"q": "song", "kind": "playlist"},
            {"q": "song", "kind": "ARTIST"},
            {"q": "song", "limit": "101"},
            {"q": "song", "limit": "0"},
            {"q": "a" * 201},
            {"q": " \t "},
            {"q": "song\x00name"},
        ):
            response = client.get(
                "/api/v1/library/search", params=params, headers={"Authorization": "Bearer good"}
            )
            assert response.status_code == 422
        for kind in MusicSearchKind:
            response = client.get(
                "/api/v1/library/search", params={"q": "song", "kind": kind.value}
            )
            assert response.status_code == 401
    assert queries.calls == []


def test_internet_search_retains_track_only_request_and_selection_boundary() -> None:
    service = Mock(spec=InternetMusicService)
    service.search.return_value = {"contract_version": "internet-music-v1", "candidates": []}

    def authenticated(request: Request) -> None:
        request.state.principal = _OWNER

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_music_router(service, authenticated=authenticated, internet_enabled=True),
        prefix="/api/v1",
    )
    body = {"query": "Artist Album", "operation_id": str(uuid4())}
    with TestClient(app) as client:
        assert client.post("/api/v1/music/internet/search", json=body).status_code == 200
        for kind in MusicSearchKind:
            response = client.post(
                "/api/v1/music/internet/search", json={**body, "kind": kind.value}
            )
            assert response.status_code == 422
        response = client.post(
            "/api/v1/music/internet/acquisitions",
            json={"search_id": str(uuid4()), "candidate_id": str(uuid4())},
        )
        assert response.status_code == 422
    service.search.assert_called_once()
    service.select.assert_not_called()
