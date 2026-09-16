"""Private owner-authenticated music selection and phone-upload commands."""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from autplay.application.internet_music import InternetMusicService
from autplay.application.music_library import MusicError
from autplay.entrypoints.discovery_automation_api import _PrivateAutomationRoute
from autplay.runtime.http import ApiError


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    operation_id: UUID


class SelectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_id: UUID
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID


class MusicRoute(_PrivateAutomationRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except MusicError as error:
                raise ApiError(
                    error.code,
                    error.message,
                    error.status_code,
                    retryable=error.retryable,
                    headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
                ) from error

        return run


def create_music_router(
    service: InternetMusicService,
    *,
    authenticated: Callable[[Request], None],
    internet_enabled: bool = False,
) -> APIRouter:
    router = APIRouter(
        prefix="/music", dependencies=[Depends(authenticated)], route_class=MusicRoute
    )

    @router.post("/user-tracks/{ref_id}/prepare-upload")
    def prepare(ref_id: UUID, request: Request) -> dict[str, str]:
        return {"recording_id": str(service.library.prepare(request.state.principal, ref_id))}

    @router.post("/user-tracks/{ref_id}/publish-upload")
    def publish(ref_id: UUID, body: PublishBody, request: Request) -> dict[str, str]:
        return {
            "audio_variant_id": str(
                service.library.publish(request.state.principal, ref_id, body.upload_id)
            )
        }

    @router.post("/internet/search")
    def search(body: SearchBody, request: Request) -> dict[str, Any]:
        if not internet_enabled:
            raise ApiError(
                "music_search_disabled", "Internet search is unavailable on this server.", 503
            )
        return service.search(request.state.principal, body.query, body.operation_id)

    @router.post("/internet/acquisitions")
    def select(body: SelectBody, request: Request) -> dict[str, Any]:
        if not internet_enabled:
            raise ApiError(
                "music_search_disabled", "Internet search is unavailable on this server.", 503
            )
        return service.select(request.state.principal, body.search_id, body.candidate_id)

    @router.get("/internet/acquisitions/{identity}")
    def status(identity: UUID, request: Request) -> dict[str, Any]:
        return service.status(request.state.principal, identity)

    return router
