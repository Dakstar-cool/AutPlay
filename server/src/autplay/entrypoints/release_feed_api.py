"""Private, read-only Android Home release-feed router (registered by composition)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from autplay.application.release_feed import (
    CONTRACT_VERSION,
    RANKING_VERSION,
    InvalidReleaseFeedCursor,
    ReleaseFeedService,
)
from autplay.domain.auth import Principal
from autplay.runtime.http import ApiError

_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Vary": "Authorization",
}


class _PrivateReleaseFeedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError as error:
                raise ApiError(
                    "release_feed_invalid",
                    "The release-feed request is invalid.",
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

        return private_handler


def create_release_feed_router(
    service: ReleaseFeedService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(
        prefix="/release-watch",
        dependencies=[Depends(authenticated)],
        route_class=_PrivateReleaseFeedRoute,
    )

    @router.get("/feed")
    def feed(
        request: Request,
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> dict[str, object]:
        principal = request.state.principal
        if not isinstance(principal, Principal):
            raise RuntimeError("authenticated request is missing its principal")
        try:
            page = service.page(principal, limit=limit, cursor=cursor)
        except (InvalidReleaseFeedCursor, ValueError) as error:
            raise ApiError(
                "release_feed_invalid_cursor",
                "The release-feed cursor is invalid.",
                422,
                headers=dict(_HEADERS),
            ) from error
        return {
            "contract_version": CONTRACT_VERSION,
            "schema_version": 1,
            "ranking_version": RANKING_VERSION,
            "items": [
                {
                    "candidate_id": str(item.candidate_id),
                    "canonical_artist_id": str(item.canonical_artist_id),
                    "artist_name": item.artist_name,
                    "title": item.title,
                    "released_at": item.released_at.isoformat(),
                    "reason": item.reason,
                    "state": item.state,
                    "score_milli": item.score_milli,
                    "release_id": str(item.release_id) if item.release_id else None,
                    "recording_id": str(item.recording_id) if item.recording_id else None,
                    "user_track_ref_id": (
                        str(item.user_track_ref_id) if item.user_track_ref_id else None
                    ),
                    "provider_id": str(item.provider_id),
                    "evidence_run_id": str(item.evidence_run_id),
                    "evidence_adapter_version": item.evidence_adapter_version,
                    "available_action": (
                        "PLAY"
                        if item.state == "READY"
                        else "REVIEW"
                        if item.state == "REVIEW_REQUIRED"
                        else "NONE"
                        if item.state in {"UNAVAILABLE", "FAILED", "ACQUIRING"}
                        else "OPEN_DISCOVERY"
                    ),
                }
                for item in page.items
            ],
            "next_cursor": page.next_cursor,
        }

    return router


__all__ = ("create_release_feed_router",)
