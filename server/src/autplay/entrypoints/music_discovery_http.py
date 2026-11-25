"""Metadata-only catalogue cards and track navigation, separate from download snapshots."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from autplay.application.catalog_downloads import CatalogDownloads
from autplay.application.music_discovery import MusicDiscoveryService
from autplay.domain.auth import Principal
from autplay.domain.music_discovery import MusicDiscoveryKind
from autplay.entrypoints.music_http import MusicRoute
from autplay.runtime.http import ApiError


class CatalogDownloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    target_kind: Literal["recording", "release", "artist"]
    entity_id: UUID
    alternatives: bool = False


def create_music_discovery_router(
    service: MusicDiscoveryService | None,
    *,
    authenticated: Callable[[Request], None],
    enabled: bool = False,
    downloads: CatalogDownloads | None = None,
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

    def download_service() -> CatalogDownloads:
        if not enabled or downloads is None:
            raise ApiError("music_discovery_disabled", "Catalogue downloads are unavailable.", 503)
        return downloads

    @router.post("/downloads/preview")
    def preview(request: Request, body: CatalogDownloadBody) -> dict[str, object]:
        return download_service().preview(
            principal(request),
            body.operation_id,
            body.target_kind,
            body.entity_id,
            body.alternatives,
        )

    @router.get("/downloads/{operation}")
    def download_status(request: Request, operation: UUID) -> dict[str, object]:
        return download_service().command(principal(request), operation, "status")

    @router.post("/downloads/{operation}/device-files")
    def device_files(
        request: Request, operation: UUID, offset: Annotated[int, Query(ge=0)] = 0
    ) -> dict[str, object]:
        return download_service().device_files(principal(request), operation, offset)

    @router.post("/downloads/{operation}/{action}")
    def advance(
        request: Request, operation: UUID, action: Literal["advance", "start"]
    ) -> dict[str, object]:
        return download_service().command(principal(request), operation, action)

    @router.get("/releases/{release_id}/cover")
    def cover(request: Request, release_id: UUID) -> Response:
        data = available().release_cover(principal(request), release_id)
        return Response(
            data, media_type="image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        )

    @router.get("/artists/{artist_id}/portrait")
    def artist_portrait(request: Request, artist_id: UUID) -> Response:
        data = available().artist_portrait(principal(request), artist_id)
        return Response(data, media_type="image/png" if data.startswith(b"\x89PNG") else "image/jpeg")

    @router.get("/library-artists/{artist_id}/ranking")
    def artist_ranking(artist_id: UUID, request: Request) -> dict[str, object]:
        return available().artist_ranking(principal(request), artist_id)

    @router.get("/search")
    def search(
        request: Request,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kind: MusicDiscoveryKind,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    ) -> dict[str, object]:
        return available().search(principal(request), q, kind, limit=limit, offset=offset)

    @router.get("/artists/{artist_id}/overview")
    def artist_overview(
        artist_id: UUID,
        request: Request,
        background_tasks: BackgroundTasks,
        baseline: UUID | None = None,
    ) -> dict[str, object]:
        return available().artist_overview(
            principal(request), artist_id, background_tasks.add_task, baseline
        )

    @router.get("/artists/{artist_id}/releases")
    def artist_releases(
        artist_id: UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, object]:
        return available().artist_releases(
            principal(request), artist_id, limit=limit, offset=offset
        )

    @router.get("/artists/{artist_id}/tracks")
    def artist_tracks(
        artist_id: UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, object]:
        return available().artist_tracks(principal(request), artist_id, limit=limit, offset=offset)

    @router.get("/releases/{release_id}/tracks")
    def release_tracks(
        release_id: UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, object]:
        return available().release_tracks(
            principal(request), release_id, limit=limit, offset=offset
        )

    return router
