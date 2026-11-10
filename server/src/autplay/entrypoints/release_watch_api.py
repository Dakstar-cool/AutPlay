"""Bearer-authenticated, owner-scoped release-watch controls for Android."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from autplay.adapters.postgresql.watch_auto_download_consent import WatchAutoDownloadError
from autplay.application.discovery_automation import AUTO_IMPORT_CONFIRMATION
from autplay.application.release_watch import ReleaseWatchService
from autplay.domain.auth import OwnedObjectNotFoundError, Principal
from autplay.runtime.http import ApiError

_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Vary": "Authorization",
}


class _SetAutoDownload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: StrictBool
    expected_revision: StrictInt = Field(ge=0)
    consequence_confirmation: str | None = Field(default=None, max_length=80)


class _PrivateReleaseWatchRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def private_route_handler(request: Request) -> Response:
            try:
                response = await route_handler(request)
            except RequestValidationError as error:
                raise ApiError(
                    "release_watch_invalid",
                    "The release-watch request is invalid.",
                    422,
                    headers=dict(_HEADERS),
                ) from error
            except ApiError as error:
                headers = dict(error.headers or {})
                headers.update(_HEADERS)
                raise ApiError(
                    error.code,
                    error.message,
                    error.status_code,
                    retryable=error.retryable,
                    headers=headers,
                    details=error.details,
                ) from error
            response.headers.update(_HEADERS)
            return response

        return private_route_handler


def create_release_watch_router(
    service: ReleaseWatchService,
    *,
    authenticated: Callable[[Request], None],
) -> APIRouter:
    """Expose manual watches separately from acquisition and training consent."""

    router = APIRouter(
        prefix="/release-watch",
        dependencies=[Depends(authenticated)],
        route_class=_PrivateReleaseWatchRoute,
    )

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("authenticated request is missing its principal")
        return value

    @router.get("/artists")
    def artists(
        request: Request,
        limit: int = Query(default=100, ge=1, le=100),
        after_artist_id: UUID | None = None,
    ) -> dict[str, object]:
        values = service.followed_artists(
            principal(request), limit=limit, after_artist_id=after_artist_id
        )
        return {
            "contract_version": "release-watch-v1",
            "schema_version": 1,
            "artists": [
                {
                    "artist_id": str(value.artist_id),
                    "reason": value.reason,
                    "score_milli": value.score_milli,
                    "favorite_generation": value.favorite_generation,
                    "auto_download": {
                        "watch_policy_id": (
                            str(value.auto_download.watch_policy_id)
                            if value.auto_download.watch_policy_id is not None
                            else None
                        ),
                        "provider_artist_id": value.auto_download.provider_artist_id,
                        "policy_revision": value.auto_download.policy_revision,
                        "policy_import_mode": value.auto_download.policy_import_mode,
                        "effective_opt_in": value.auto_download.effective_opt_in,
                        "execution_enabled": value.auto_download.execution_enabled,
                        "reason": value.auto_download.reason,
                    },
                }
                for value in values
            ],
        }

    @router.get("/auto-download")
    def auto_download(request: Request) -> dict[str, object]:
        try:
            state = service.auto_download(principal(request))
        except OwnedObjectNotFoundError as error:
            raise ApiError(
                "release_watch_owner_not_found",
                "The owner is unavailable.",
                404,
                headers=dict(_HEADERS),
            ) from error
        return {
            "contract_version": "release-watch-v1",
            "schema_version": 1,
            "enabled": state.enabled,
            "revision": state.revision,
            "execution_enabled": state.execution_enabled,
            "consequence_confirmation": AUTO_IMPORT_CONFIRMATION,
        }

    @router.put("/auto-download")
    def set_auto_download(request: Request, body: _SetAutoDownload) -> dict[str, object]:
        try:
            state = service.set_auto_download(
                principal(request),
                enabled=body.enabled,
                expected_revision=body.expected_revision,
                confirmation_code=body.consequence_confirmation,
            )
        except OwnedObjectNotFoundError as error:
            raise ApiError(
                "release_watch_owner_not_found",
                "The owner is unavailable.",
                404,
                headers=dict(_HEADERS),
            ) from error
        except WatchAutoDownloadError as error:
            status = 422 if error.code.endswith("confirmation_invalid") else 409
            raise ApiError(
                error.code,
                "The auto-download setting could not be changed.",
                status,
                headers=dict(_HEADERS),
            ) from error
        return {
            "contract_version": "release-watch-v1",
            "schema_version": 1,
            "enabled": state.enabled,
            "revision": state.revision,
            "execution_enabled": state.execution_enabled,
            "consequence_confirmation": AUTO_IMPORT_CONFIRMATION,
        }

    @router.get("/exclusions")
    def exclusions(
        request: Request,
        limit: int = Query(default=100, ge=1, le=100),
        after_artist_id: UUID | None = None,
    ) -> dict[str, object]:
        values = service.excluded_artists(
            principal(request), limit=limit, after_artist_id=after_artist_id
        )
        return {
            "contract_version": "release-watch-v1",
            "schema_version": 1,
            "artist_ids": [str(value) for value in values],
        }

    def set_follow(request: Request, artist_id: UUID, *, follow: bool) -> dict[str, object]:
        try:
            revision = (
                service.follow(principal(request), artist_id)
                if follow
                else service.unfollow(principal(request), artist_id)
            )
        except OwnedObjectNotFoundError as error:
            raise ApiError(
                "release_watch_artist_not_found",
                "The artist is unavailable.",
                404,
                headers=dict(_HEADERS),
            ) from error
        return {
            "contract_version": "release-watch-v1",
            "schema_version": 1,
            "artist_id": str(artist_id),
            "revision": revision,
        }

    @router.put("/artists/{artist_id}")
    def follow(request: Request, artist_id: UUID) -> dict[str, object]:
        return set_follow(request, artist_id, follow=True)

    @router.delete("/artists/{artist_id}")
    def unfollow(request: Request, artist_id: UUID) -> dict[str, object]:
        return set_follow(request, artist_id, follow=False)

    return router


__all__ = ("create_release_watch_router",)
