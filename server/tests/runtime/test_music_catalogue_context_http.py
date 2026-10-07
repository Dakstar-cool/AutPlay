"""The private context route accepts identities and never client metadata assertions."""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, NoReturn, cast
from uuid import UUID, uuid4

import pytest
from autplay.application.catalogue_context import CatalogueContextService
from autplay.application.music_library import MusicError
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.catalogue_context import CatalogueContextEntity
from autplay.entrypoints.music_catalogue_context_http import create_catalogue_context_router
from autplay.runtime.http import ApiError, install_error_handlers
from fastapi import FastAPI, Request
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient


def client(*, enabled: bool = True) -> TestClient:
    def authenticate(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer owner":
            raise ApiError("unauthorized", "Authentication required.", 401)
        request.state.principal = "owner"

    def create(
        principal: object,
        entity_type: CatalogueContextEntity,
        entity_id: UUID,
        release_id: UUID | None,
    ) -> dict[str, object]:
        assert principal == "owner"
        if entity_id.int == 0:
            raise MusicError("music_catalogue_entity_not_found", "Unavailable.", 404)
        return {"catalogue_context_id": str(uuid4()), "acquisition_allowed": False}

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_catalogue_context_router(
            cast(CatalogueContextService, SimpleNamespace(create=create)),
            authenticated=authenticate,
            enabled=enabled,
        )
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "patch",
    [
        {"album": "Forged album"},
        {"artist": "Forged artist"},
        {"source": "SOURCE_NATIVE"},
        {"year": 2001},
        {"entity_type": "artist"},
        {"release_id": str(uuid4())},
    ],
)
def test_recording_rejects_metadata_assertions_and_release(patch: dict[str, object]) -> None:
    with client() as http:
        response = http.post(
            "/music/discovery/contexts",
            json={
                "entity_type": "recording",
                "entity_id": str(uuid4()),
                **patch,
            },
            headers={"Authorization": "Bearer owner"},
        )
    assert response.status_code == 422
    assert "no-store" in response.headers["cache-control"]


def test_membership_shape_auth_disabled_and_errors_are_private() -> None:
    with client() as http:
        body = {"entity_type": "release_track", "entity_id": str(uuid4())}
        outcomes = [
            (http.post("/music/discovery/contexts", json=body), 401),
            (
                http.post(
                    "/music/discovery/contexts",
                    json=body,
                    headers={"Authorization": "Bearer owner"},
                ),
                422,
            ),
            (
                http.post(
                    "/music/discovery/contexts",
                    json={**body, "release_id": str(uuid4())},
                    headers={"Authorization": "Bearer owner"},
                ),
                200,
            ),
            (
                http.post(
                    "/music/discovery/contexts",
                    json={
                        "entity_type": "recording",
                        "entity_id": str(UUID(int=0)),
                    },
                    headers={"Authorization": "Bearer owner"},
                ),
                404,
            ),
        ]
        for response, expected in outcomes:
            assert response.status_code == expected
            assert "private" in response.headers["cache-control"]
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["vary"] == "Authorization"
    with client(enabled=False) as http:
        response = http.post(
            "/music/discovery/contexts",
            json={
                "entity_type": "recording",
                "entity_id": str(uuid4()),
            },
            headers={"Authorization": "Bearer owner"},
        )
        assert response.status_code == 503


def test_missing_retained_runtime_mounts_private_disabled_route() -> None:
    def authenticate(request: Request) -> None:
        request.state.principal = "owner"

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_catalogue_context_router(None, authenticated=authenticate, enabled=True)
    )
    with TestClient(app) as http:
        response = http.post(
            "/music/discovery/contexts",
            json={
                "entity_type": "recording",
                "entity_id": str(uuid4()),
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "music_catalogue_disabled"
    assert "no-store" in response.headers["cache-control"]


def test_database_outage_is_sanitized_retryable_catalogue_error() -> None:
    def unavailable_session() -> NoReturn:
        raise OperationalError("private SQL", {}, Exception("private database origin"))

    class UnreachableHttp:
        def json(self, url: str) -> dict[str, Any]:
            raise AssertionError("no network after database failure")

    service = CatalogueContextService(
        cast(sessionmaker[Session], unavailable_session),
        lambda principal: nullcontext(UnreachableHttp()),
    )
    actor = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    with pytest.raises(MusicError) as caught:
        service.create(actor, CatalogueContextEntity.RECORDING, uuid4())
    error = caught.value
    assert error.code == "music_catalogue_unavailable" and error.status_code == 503
    assert error.retryable and "private" not in error.message
