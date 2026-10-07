"""Metadata-only catalogue cards and track navigation, separate from download snapshots."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from autplay.application.music_discovery import MusicDiscoveryService
from autplay.domain.auth import Principal
from autplay.domain.music_discovery import MusicDiscoveryKind
from autplay.entrypoints.music_http import MusicRoute
from autplay.runtime.http import ApiError


def create_music_discovery_router(
    service: MusicDiscoveryService | None,
    *,
    authenticated: Callable[[Request], None],
    enabled: bool = False,
) -> APIRouter:
    router = APIRouter(
        prefix="/music/discovery", dependencies=[Depends(authenticated)], route_class=MusicRoute
    )

    def available() -> MusicDiscoveryService:
        if not enabled or service is None:
            raise ApiError(
                "music_discovery_disabled", "Catalogue search is unavailable on this server.", 503
            )
        return service

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("authenticated request is missing its principal")
        return value

    @router.get("/search")
    def search(
        request: Request,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kind: MusicDiscoveryKind,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    ) -> dict[str, object]:
        return available().search(principal(request), q, kind, limit=limit, offset=offset)

    @router.get("/artists/{artist_id}/tracks")
    def artist_tracks(
        artist_id: UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    ) -> dict[str, object]:
        return available().artist_tracks(principal(request), artist_id, limit=limit, offset=offset)

    @router.get("/releases/{release_id}/tracks")
    def release_tracks(
        release_id: UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    ) -> dict[str, object]:
        return available().release_tracks(
            principal(request), release_id, limit=limit, offset=offset
        )

    return router
