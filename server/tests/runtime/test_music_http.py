"""Music routes preserve authorization, validation and private error envelopes."""

from typing import Any
from uuid import UUID, uuid4

from autplay.application.internet_music import InternetMusicService
from autplay.application.music_library import MusicError
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.music_http import create_music_router
from autplay.runtime.http import ApiError, install_error_handlers
from fastapi import FastAPI, Request
from starlette.testclient import TestClient


def client(*, enabled: bool = True) -> TestClient:
    owner = Principal(UUID(int=1), UUID(int=2), UUID(int=3), AccountRole.USER)

    def authenticate(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer owner":
            raise ApiError("unauthorized", "Authentication required.", 401)
        request.state.principal = owner

    class Service(InternetMusicService):
        def __init__(self) -> None:
            pass

        def search(self, principal: Principal, query: str, operation_id: UUID) -> dict[str, Any]:
            assert principal == owner
            if query == "unavailable":
                raise MusicError("music_search_failed", "Search unavailable.", 503, retryable=True)
            return {"search_id": str(operation_id), "candidates": []}

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_music_router(Service(), authenticated=authenticate, internet_enabled=enabled)
    )
    return TestClient(app)


def test_music_search_auth_validation_error_and_success_are_private() -> None:
    with client() as http:
        body = {"query": "song", "operation_id": str(uuid4())}
        outcomes = [
            (http.post("/music/internet/search", json=body), 401),
            (
                http.post(
                    "/music/internet/search", json=body, headers={"Authorization": "Bearer owner"}
                ),
                200,
            ),
            (
                http.post(
                    "/music/internet/search",
                    json={**body, "query": ""},
                    headers={"Authorization": "Bearer owner"},
                ),
                422,
            ),
            (
                http.post(
                    "/music/internet/search",
                    json={**body, "query": "unavailable"},
                    headers={"Authorization": "Bearer owner"},
                ),
                503,
            ),
        ]
        for response, status in outcomes:
            assert response.status_code == status
            assert "private" in response.headers["cache-control"]
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["vary"] == "Authorization"
        assert outcomes[-1][0].json()["error"]["retryable"] is True


def test_music_search_disabled_has_no_provider_call() -> None:
    with client(enabled=False) as http:
        result = http.post(
            "/music/internet/search",
            json={"query": "song", "operation_id": str(uuid4())},
            headers={"Authorization": "Bearer owner"},
        )
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "music_search_disabled"
