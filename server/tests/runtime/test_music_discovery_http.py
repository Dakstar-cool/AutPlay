from __future__ import annotations

from contextlib import nullcontext
from uuid import UUID, uuid4

import pytest
from autplay.application.music_discovery import MusicDiscoveryService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.music_discovery import (
    MusicDiscoveryCard,
    MusicDiscoveryEntity,
    MusicDiscoveryKind,
    MusicDiscoveryPage,
)
from autplay.entrypoints.music_discovery_http import create_music_discovery_router
from autplay.ports.track_metadata import MetadataProviderError
from autplay.runtime.http import ApiError, install_error_handlers
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

_OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)


class Provider:
    def __init__(self, error: MetadataProviderError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, object, int, int]] = []

    def search(
        self, query: str, kind: MusicDiscoveryKind, *, limit: int, offset: int
    ) -> MusicDiscoveryPage:
        self.calls.append(("search", (query, kind), limit, offset))
        if self.error:
            raise self.error
        entity = (
            MusicDiscoveryEntity.ARTIST
            if kind == MusicDiscoveryKind.ARTIST
            else MusicDiscoveryEntity.RELEASE
        )
        return MusicDiscoveryPage(
            (MusicDiscoveryCard(entity, uuid4(), "Title"),), limit, offset, offset + 1, 1
        )

    def artist_tracks(self, artist_id: UUID, *, limit: int, offset: int) -> MusicDiscoveryPage:
        self.calls.append(("artist", artist_id, limit, offset))
        return MusicDiscoveryPage((), limit, offset, 0, 0)

    def release_tracks(self, release_id: UUID, *, limit: int, offset: int) -> MusicDiscoveryPage:
        self.calls.append(("release", release_id, limit, offset))
        return MusicDiscoveryPage((), limit, offset, 0, 0)


def app(provider: Provider, *, enabled: bool = True, configured: bool = True) -> FastAPI:
    def authenticated(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer owner":
            raise ApiError("auth_invalid", "Authentication is unavailable.", 401)
        request.state.principal = _OWNER

    def factory(owner: Principal) -> nullcontext[Provider]:
        assert owner == _OWNER
        return nullcontext(provider)

    application = FastAPI()
    install_error_handlers(application)
    application.include_router(
        create_music_discovery_router(
            MusicDiscoveryService(factory) if configured else None,
            authenticated=authenticated,
            enabled=enabled,
        ),
        prefix="/api/v1",
    )
    return application


def test_private_catalogue_search_and_drilldown_preserve_uuid_capability_and_bounds() -> None:
    provider = Provider()
    with TestClient(app(provider)) as client:
        for kind in MusicDiscoveryKind:
            response = client.get(
                "/api/v1/music/discovery/search",
                params={"q": "  Artist  Name  ", "kind": kind.value},
                headers={"Authorization": "Bearer owner"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["contract_version"] == "music-discovery-v1"
            assert body["source_scope"] == "INTERNET" and body["availability"] == "METADATA_ONLY"
            assert body["capabilities"]["direct_acquisition"] is False
            assert body["items"][0]["acquisition_allowed"] is False
            assert "candidate_id" not in body["items"][0] and "search_id" not in body
            assert provider.calls[-1] == ("search", ("Artist Name", kind), 25, 0)
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["vary"] == "Authorization"
        identity = uuid4()
        for resource, name in (("artists", "artist"), ("releases", "release")):
            response = client.get(
                f"/api/v1/music/discovery/{resource}/{identity}/tracks",
                params={"limit": 1, "offset": 1000},
                headers={"Authorization": "Bearer owner"},
            )
            assert response.status_code == 200
            assert provider.calls[-1] == (name, identity, 1, 1000)


def test_unauthorized_invalid_disabled_and_unconfigured_reads_never_open_provider() -> None:
    provider = Provider()
    with TestClient(app(provider)) as client:
        response = client.get(
            "/api/v1/music/discovery/search", params={"q": "Artist", "kind": "artist"}
        )
        assert response.status_code == 401 and "no-store" in response.headers["cache-control"]
        for params in (
            {"q": "Artist", "kind": "all"},
            {"q": "Artist", "kind": "track"},
            {"q": "Artist"},
            {"q": " ", "kind": "artist"},
            {"q": "a" * 201, "kind": "artist"},
            {"q": "a\x00b", "kind": "artist"},
            {"q": "Artist", "kind": "artist", "offset": "1001"},
            {"q": "Artist", "kind": "artist", "limit": "51"},
        ):
            response = client.get(
                "/api/v1/music/discovery/search",
                params=params,
                headers={"Authorization": "Bearer owner"},
            )
            assert response.status_code == 422 and "no-store" in response.headers["cache-control"]
        assert (
            client.get(
                "/api/v1/music/discovery/artists/not-a-uuid/tracks",
                headers={"Authorization": "Bearer owner"},
            ).status_code
            == 422
        )
    for enabled, configured in ((False, True), (True, False)):
        with TestClient(app(provider, enabled=enabled, configured=configured)) as client:
            response = client.get(
                "/api/v1/music/discovery/search",
                params={"q": "Artist", "kind": "artist"},
                headers={"Authorization": "Bearer owner"},
            )
            assert (
                response.status_code == 503
                and response.json()["error"]["code"] == "music_discovery_disabled"
            )
    assert provider.calls == []


@pytest.mark.parametrize(
    "code,status,public_code",
    [
        ("metadata_provider_busy", 503, "music_discovery_busy"),
        ("metadata_network_unavailable", 503, "music_discovery_unavailable"),
        ("metadata_release_missing", 404, "music_discovery_entity_not_found"),
    ],
)
def test_catalogue_failures_have_private_stable_errors_without_raw_provider_detail(
    code: str, status: int, public_code: str
) -> None:
    with TestClient(app(Provider(MetadataProviderError(code)))) as client:
        response = client.get(
            "/api/v1/music/discovery/search",
            params={"q": "Artist", "kind": "artist"},
            headers={"Authorization": "Bearer owner"},
        )
    assert response.status_code == status and response.json()["error"]["code"] == public_code
    assert "no-store" in response.headers["cache-control"]
