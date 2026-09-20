"""Private owner metadata commands and authenticated cached JPEG bytes."""

from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from autplay.application.track_metadata import TrackMetadataService
from autplay.entrypoints.music_http import MusicRoute


class MetadataCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    expected_revision: int = Field(ge=0)
    action: Literal["REFRESH", "EDIT", "SELECT"]
    fields: dict[str, Any] | None = None
    candidate_id: str | None = Field(default=None, max_length=100)


def create_metadata_router(
    service: TrackMetadataService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(
        prefix="/music", dependencies=[Depends(authenticated)], route_class=MusicRoute
    )

    @router.get("/user-tracks/{ref_id}/metadata")
    def get(ref_id: UUID, request: Request) -> dict[str, Any]:
        return service.get(request.state.principal, ref_id)

    @router.post("/user-tracks/{ref_id}/metadata")
    def command(ref_id: UUID, body: MetadataCommandBody, request: Request) -> dict[str, Any]:
        return service.command(request.state.principal, ref_id, **body.model_dump())

    @router.post("/metadata/backfill")
    def backfill(request: Request) -> dict[str, int]:
        return {"queued": service.enqueue_missing(request.state.principal.user_id, limit=100)}

    @router.get("/user-tracks/{ref_id}/artwork/{sha256}")
    def artwork(ref_id: UUID, sha256: str, request: Request) -> Response:
        return Response(
            service.artwork(request.state.principal, ref_id, sha256),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, no-store",
                "Vary": "Authorization",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
