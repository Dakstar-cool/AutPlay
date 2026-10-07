"""Only provider identities can request a server-validated catalogue receipt."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, model_validator

from autplay.application.catalogue_context import CatalogueContextService
from autplay.domain.catalogue_context import CatalogueContextEntity
from autplay.entrypoints.music_http import MusicRoute
from autplay.runtime.http import ApiError


class CatalogueContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: CatalogueContextEntity
    entity_id: UUID
    release_id: UUID | None = None

    @model_validator(mode="after")
    def membership_shape(self) -> CatalogueContextBody:
        if (self.entity_type == CatalogueContextEntity.RELEASE_TRACK) != (
            self.release_id is not None
        ):
            raise ValueError("release_id must identify the selected release track's edition")
        return self


def create_catalogue_context_router(
    service: CatalogueContextService | None,
    *,
    authenticated: Callable[[Request], None],
    enabled: bool = False,
) -> APIRouter:
    router = APIRouter(
        prefix="/music/discovery", dependencies=[Depends(authenticated)], route_class=MusicRoute
    )

    @router.post("/contexts")
    def create(body: CatalogueContextBody, request: Request) -> dict[str, object]:
        if not enabled or service is None:
            raise ApiError("music_catalogue_disabled", "The catalogue is unavailable.", 503)
        return service.create(
            request.state.principal, body.entity_type, body.entity_id, body.release_id
        )

    return router
