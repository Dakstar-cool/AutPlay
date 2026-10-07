"""The actual API factory wires the catalogue through one principal-bound coordinator."""

from __future__ import annotations

import threading
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.public_catalogue_context import MusicBrainzCatalogueContextProvider
from autplay.application.auth import AuthService
from autplay.application.catalogue_context import CatalogueContextService, CatalogueHttpFactory
from autplay.domain.auth import AccountRole, InvalidAccessTokenError, Principal
from autplay.domain.catalogue_context import CatalogueContextEntity
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints import api, privacy_deletion
from autplay.entrypoints.catalog_composition import CatalogRuntime
from autplay.ports.track_metadata import MetadataProviderError
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
ARTIST_ID, RELEASE_ID, RECORDING_ID, TRACK_ID = (uuid4() for _ in range(4))


class Auth:
    def authenticate_access(self, credential: str) -> Principal:
        if credential != "owner":
            raise InvalidAccessTokenError()
        return OWNER


class Probe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(True, "postgresql")


class Http:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: MetadataProviderError | None = None

    def json(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if self.error:
            raise self.error
        if "/recording?" in url:
            return {
                "recording-count": 1,
                "recording-offset": 0,
                "recordings": [
                    {
                        "id": str(RECORDING_ID),
                        "title": "Song (Live)",
                        "artist-credit": [{"name": "Artist"}],
                    }
                ],
            }
        if f"/release/{RELEASE_ID}?" in url:
            return {
                "id": str(RELEASE_ID),
                "title": "Exact edition",
                "media": [
                    {
                        "position": 2,
                        "track-count": 1,
                        "tracks": [
                            {
                                "id": str(TRACK_ID),
                                "position": 3,
                                "title": "Song (Live)",
                                "recording": {
                                    "id": str(RECORDING_ID),
                                    "title": "Song (Live)",
                                    "artist-credit": [{"name": "Artist"}],
                                },
                            }
                        ],
                    }
                ],
            }
        return {"count": 1, "offset": 0, "artists": [{"id": str(ARTIST_ID), "name": "Artist"}]}


class Coordinator:
    def __init__(self) -> None:
        self.transport = Http()
        self.owners: list[Principal] = []

    def http(self, principal: Principal) -> Http:
        self.owners.append(principal)
        return self.transport


class Runtime:
    def __init__(self, events: list[str]) -> None:
        self.work = Coordinator()
        self.result = ReadinessResult(True, "music_catalog")
        self.events = events
        self.pending = (uuid4(),)
        self.shutdown_thread: int | None = None

    def check(self) -> ReadinessResult:
        return self.result

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        assert timeout == 5
        self.shutdown_thread = threading.get_ident()
        self.events.append("catalog_shutdown")
        return self.pending


def settings(*, enabled: bool = True) -> ApiSettings:
    return ApiSettings(
        database_url=SecretStr("postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
        public_access_source_hmac_secret=SecretStr("public-source-secret-at-least-32-bytes"),
        internet_music_enabled=enabled,
    )


def test_actual_api_uses_one_catalog_runtime_with_owner_transport_and_contained_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    runtime = Runtime(events)
    constructions: list[ApiSettings] = []

    def build_catalog(configuration: ApiSettings) -> CatalogRuntime:
        constructions.append(configuration)
        return cast(CatalogRuntime, runtime)

    def build_engine(configuration: ApiSettings) -> Engine:
        engine = create_runtime_engine(configuration)
        event.listen(engine, "engine_disposed", lambda _: events.append("api_engine_dispose"))
        return engine

    monkeypatch.setattr(api, "CatalogRuntime", build_catalog)
    monkeypatch.setattr(api, "create_runtime_engine", build_engine)
    app = api.create_app(
        settings(), readiness_probe=Probe(), auth_service=cast(AuthService, Auth())
    )
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        paths = (
            "/api/v1/music/discovery/search?q=Artist&kind=artist",
            f"/api/v1/music/discovery/artists/{ARTIST_ID}/tracks",
            f"/api/v1/music/discovery/releases/{RELEASE_ID}/tracks",
        )
        for path in paths:
            response = client.get(path, headers={"Authorization": "Bearer owner"})
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            assert response.json()["acquisition_allowed"] is False
        assert response.json()["items"][0]["disc_number"] == 2
        assert response.json()["items"][0]["track_number"] == 3
        assert constructions == [app.state.settings]
        assert runtime.work.owners == [OWNER, OWNER, OWNER]
        assert len(runtime.work.transport.calls) == 3
        for headers in (
            {},
            {"Authorization": "Bearer invalid"},
            {"X-AutPlay-Guest-Capability": "B" * 43},
        ):
            assert client.get(paths[0], headers=headers).status_code == 401
        assert len(runtime.work.transport.calls) == 3
        assert client.post("/api/v1/music/internet/search", json={}).status_code == 401
        runtime.work.transport.error = MetadataProviderError("metadata_provider_busy")
        busy = client.get(paths[0], headers={"Authorization": "Bearer owner"})
        assert busy.status_code == 503 and busy.json()["error"]["code"] == "music_discovery_busy"
        runtime.result = ReadinessResult(
            False, "music_catalog", "metadata_catalog_exit_unconfirmed"
        )
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "metadata_catalog_exit_unconfirmed"
    assert app.state.unconfirmed_catalog_io == runtime.pending
    assert runtime.shutdown_thread is not None
    assert events == ["catalog_shutdown", "api_engine_dispose"]


def test_disabled_catalogue_does_not_construct_a_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(configuration: ApiSettings) -> CatalogRuntime:
        pytest.fail("disabled catalogue must not create a network coordinator")

    monkeypatch.setattr(api, "CatalogRuntime", forbidden)
    app = api.create_app(
        settings(enabled=False), readiness_probe=Probe(), auth_service=cast(AuthService, Auth())
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/music/discovery/search?q=Artist&kind=artist",
            headers={"Authorization": "Bearer owner"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "music_discovery_disabled"
        context = client.post(
            "/api/v1/music/discovery/contexts",
            json={"entity_type": "recording", "entity_id": str(RECORDING_ID)},
            headers={"Authorization": "Bearer owner"},
        )
        assert context.status_code == 503
        assert context.json()["error"]["code"] == "music_catalogue_disabled"
        assert client.get("/health/ready").status_code == 200


def test_missing_containment_fails_closed_without_breaking_existing_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(configuration: ApiSettings) -> CatalogRuntime:
        raise ResourceAdmissionError("metadata_catalog_containment_unavailable")

    monkeypatch.setattr(api, "CatalogRuntime", unavailable)
    app = api.create_app(
        settings(), readiness_probe=Probe(), auth_service=cast(AuthService, Auth())
    )
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get(
            "/api/v1/music/discovery/search?q=Artist&kind=artist",
            headers={"Authorization": "Bearer owner"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "music_discovery_disabled"
        context = client.post(
            "/api/v1/music/discovery/contexts",
            json={"entity_type": "recording", "entity_id": str(RECORDING_ID)},
            headers={"Authorization": "Bearer owner"},
        )
        assert context.status_code == 503
        assert context.json()["error"]["code"] == "music_catalogue_disabled"
        assert client.get("/health/ready").json()["error"]["code"] == (
            "metadata_catalog_containment_unavailable"
        )


def test_catalog_shutdown_also_runs_after_startup_guard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    runtime = Runtime(events)

    def guard(*_: object) -> None:
        raise RuntimeError("privacy_restore_blocked")

    monkeypatch.setattr(privacy_deletion, "enforce_privacy_restore_guard", guard)
    app = api.create_app(
        settings(),
        readiness_probe=Probe(),
        auth_service=cast(AuthService, Auth()),
        catalog_runtime=cast(CatalogRuntime, runtime),
    )
    with pytest.raises(RuntimeError, match="privacy_restore_blocked"), TestClient(app):
        pass
    assert events == ["catalog_shutdown"]
    assert app.state.unconfirmed_catalog_io == runtime.pending


def test_context_route_shares_the_browse_coordinator_and_accepts_only_entity_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Runtime([])
    constructions: list[ApiSettings] = []
    context_constructions: list[sessionmaker[Session]] = []

    class Context:
        def __init__(
            self, sessions: sessionmaker[Session], http_factory: CatalogueHttpFactory
        ) -> None:
            context_constructions.append(sessions)
            self.http_factory = http_factory

        def create(
            self,
            principal: Principal,
            entity_type: CatalogueContextEntity,
            entity_id: UUID,
            release_id: UUID | None = None,
        ) -> dict[str, object]:
            with self.http_factory(principal) as http:
                card = MusicBrainzCatalogueContextProvider(http).hydrate(
                    entity_type, entity_id, release_id
                )
            return {
                "catalogue_context_id": str(uuid4()),
                "acquisition_allowed": False,
                "lookup_metadata": card.document(),
            }

    def build_context(
        sessions: sessionmaker[Session], http_factory: CatalogueHttpFactory
    ) -> CatalogueContextService:
        return cast(CatalogueContextService, Context(sessions, http_factory))

    def build_catalog(configuration: ApiSettings) -> CatalogRuntime:
        constructions.append(configuration)
        return cast(CatalogRuntime, runtime)

    monkeypatch.setattr(api, "CatalogRuntime", build_catalog)
    monkeypatch.setattr(api, "CatalogueContextService", build_context)
    app = api.create_app(
        settings(), readiness_probe=Probe(), auth_service=cast(AuthService, Auth())
    )
    body = {
        "entity_type": "release_track",
        "entity_id": str(TRACK_ID),
        "release_id": str(RELEASE_ID),
    }
    path = "/api/v1/music/discovery/contexts"
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        headers = {"Authorization": "Bearer owner"}
        forged = client.post(path, json={**body, "album": "Fake edition"}, headers=headers)
        assert forged.status_code == 422
        assert not runtime.work.owners
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert response.json()["acquisition_allowed"] is False
        actual = response.json()["lookup_metadata"]
        assert actual["album"] == "Exact edition"
        assert actual["recording_mbid"] == str(RECORDING_ID)
        assert actual["release_mbid"] == str(RELEASE_ID)
        browse = client.get(
            f"/api/v1/music/discovery/releases/{RELEASE_ID}/tracks", headers=headers
        )
        assert browse.status_code == 200
        assert runtime.work.owners == [OWNER, OWNER]
        assert len(constructions) == len(context_constructions) == 1
        assert app.state.catalog_runtime is runtime
        assert len(runtime.work.transport.calls) == 2
