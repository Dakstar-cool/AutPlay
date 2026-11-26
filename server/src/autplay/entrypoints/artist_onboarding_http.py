"""First-login artist choices belong to the authenticated account."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autplay.adapters.postgresql.artist_onboarding import ArtistOnboarding
from autplay.runtime.http import ApiError


class BrowseArtists(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: UUID | None = None
    query: str = Field("", max_length=120)
    seen: list[UUID] = Field(default_factory=list, max_length=350)


class ChooseArtists(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artists: list[UUID] = Field(max_length=60)


def artist_onboarding_router(
    service: ArtistOnboarding, authenticate: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(prefix="/artist-onboarding", dependencies=[Depends(authenticate)])
    headers = {"Cache-Control": "private, no-store"}

    @router.get("")
    def status(request: Request) -> JSONResponse:
        return JSONResponse(
            {"pending": service.pending(request.state.principal.user_id)}, headers=headers
        )

    @router.post("/browse")
    def browse(request: Request, body: BrowseArtists) -> JSONResponse:
        return JSONResponse(
            {
                "artists": service.browse(
                    request.state.principal.user_id, body.parent, body.query.strip(), body.seen
                )
            },
            headers=headers,
        )

    @router.post("/finish")
    def finish(request: Request, body: ChooseArtists) -> JSONResponse:
        try:
            service.finish(request.state.principal.user_id, list(dict.fromkeys(body.artists)))
        except ValueError as error:
            raise ApiError("artist_unavailable", "Refresh the available artists.", 409) from error
        return JSONResponse({"pending": False}, headers=headers)

    return router
