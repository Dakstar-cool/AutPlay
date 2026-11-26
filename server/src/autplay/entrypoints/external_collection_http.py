"""Authenticated shared Android/desktop collection import contract."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from autplay.application.external_collections import ExternalCollections
from autplay.domain.external_collections import CollectionError
from autplay.runtime.http import ApiError


class StartCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    favorites: bool = False
    new_copy: bool = False


class ConfirmCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    selection_key: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


def _run(call: Callable[[], Any]) -> JSONResponse:
    try:
        return JSONResponse(call(), headers={"Cache-Control": "private, no-store"})
    except CollectionError as e:
        raise ApiError(
            "external_import_" + e.code,
            "The collection import could not proceed.",
            e.status,
            retryable=e.status in {429, 502, 503},
            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None,
        ) from e


def external_collection_router(
    service: ExternalCollections, authenticate: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(prefix="/collection-imports")
    auth = [Depends(authenticate)]

    @router.post("", dependencies=auth)
    def start(body: StartCollection, request: Request) -> JSONResponse:
        return _run(
            lambda: service.start(
                request.state.principal.user_id,
                body.operation_id,
                body.url,
                favorites=body.favorites,
                new_copy=body.new_copy,
            )
        )

    @router.get("", dependencies=auth)
    def recent(request: Request) -> JSONResponse:
        return _run(lambda: {"imports": service.recent(request.state.principal.user_id)})

    @router.get("/spotify/callback")
    def callback(
        state: Annotated[str, Query(min_length=1, max_length=4096)],
        code: Annotated[str, Query(min_length=1, max_length=2048)],
    ) -> PlainTextResponse:
        _run(lambda: service.authorize_spotify(state, code))
        return PlainTextResponse(
            "Spotify connected. Return to AutPlay to continue the import.",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.get("/{operation}", dependencies=auth)
    def report(
        operation: UUID, request: Request, offset: Annotated[int, Query(ge=0, le=10000)] = 0
    ) -> JSONResponse:
        return _run(lambda: service.report(request.state.principal.user_id, operation, offset))

    @router.post("/{operation}/resume", dependencies=auth)
    def resume(operation: UUID, request: Request) -> JSONResponse:
        return _run(lambda: service.advance(request.state.principal.user_id, operation))

    @router.post("/{operation}/confirm", dependencies=auth)
    def confirm(operation: UUID, body: ConfirmCollection, request: Request) -> JSONResponse:
        return _run(
            lambda: service.confirm(
                request.state.principal.user_id, operation, body.revision, body.selection_key
            )
        )

    @router.post("/{operation}/cancel", dependencies=auth)
    def cancel(operation: UUID, request: Request) -> JSONResponse:
        return _run(lambda: service.cancel(request.state.principal.user_id, operation))

    return router
