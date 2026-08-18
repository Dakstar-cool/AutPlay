"""Authenticated model-independent P11 recommendation HTTP routes."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from autplay.domain.auth import Principal
from autplay.domain.recommendations import (
    HomeFeed,
    OfflinePack,
    RankedRecommendation,
    RecommendationNotFound,
    RecommendationQuery,
    RecommendationResponse,
    RecommendationSurface,
    ReplayInputUnavailable,
)
from autplay.runtime.http import ApiError

SafeKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9._-]+$")]


class RecommendationRequestBody(BaseModel):
    """Bounded public request containing no model-framework fields."""

    model_config = ConfigDict(extra="forbid")

    context: str = Field(default="GENERAL", min_length=1, max_length=20)
    limit: int = Field(default=25, ge=1, le=100)
    exploration: float = Field(default=0.2, ge=0.0, le=1.0)
    seed: int = 0
    pipeline_key: SafeKey = "cpu-baseline"
    pipeline_version: SafeKey | None = None
    shadow: bool = False


class OfflinePackRequestBody(RecommendationRequestBody):
    """Offline pack request with a bounded expiry."""

    ttl_days: int = Field(default=7, ge=1, le=30)


class RecommendationHttpService(Protocol):
    """Narrow application service shape used for injection tests."""

    def recommend(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> RecommendationResponse: ...

    def exact_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse: ...

    def algorithmic_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse: ...

    def home(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> HomeFeed: ...

    def offline_pack(
        self,
        query: RecommendationQuery,
        *,
        device_id: UUID,
        ttl: timedelta,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> OfflinePack: ...


def create_recommendation_router(
    service: RecommendationHttpService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    """Return owner-scoped serve, replay, home and offline-pack routes."""
    router = APIRouter(dependencies=[Depends(authenticated)])

    @router.post("/recommendations", response_model=None)
    def recommend(request: Request, body: RecommendationRequestBody) -> JSONResponse:
        principal = _principal(request)
        try:
            response = service.recommend(
                _query(principal, body, RecommendationSurface.RECOMMENDATIONS),
                pipeline_key=body.pipeline_key,
                pipeline_version=body.pipeline_version,
            )
        except ReplayInputUnavailable:
            raise _pipeline_unavailable() from None
        return _response(response, replay="served")

    @router.post("/home", response_model=None)
    def home(request: Request, body: RecommendationRequestBody) -> JSONResponse:
        try:
            feed = service.home(
                _query(_principal(request), body, RecommendationSurface.HOME),
                pipeline_key=body.pipeline_key,
                pipeline_version=body.pipeline_version,
            )
        except ReplayInputUnavailable:
            raise _pipeline_unavailable() from None
        return JSONResponse(
            {
                "recommendation_request_id": str(feed.recommendation_request_id),
                "sections": [
                    {
                        "key": section.key,
                        "title": section.title,
                        "items": [
                            _item(feed.recommendation_request_id, item) for item in section.items
                        ],
                    }
                    for section in feed.sections
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/recommendation-packs", response_model=None)
    def offline_pack(request: Request, body: OfflinePackRequestBody) -> JSONResponse:
        principal = _principal(request)
        try:
            pack = service.offline_pack(
                _query(principal, body, RecommendationSurface.OFFLINE_PACK),
                device_id=principal.device_id,
                ttl=timedelta(days=body.ttl_days),
                pipeline_key=body.pipeline_key,
                pipeline_version=body.pipeline_version,
            )
        except ReplayInputUnavailable:
            raise _pipeline_unavailable() from None
        return JSONResponse(
            {
                "offline_pack_id": str(pack.offline_pack_id),
                "recommendation_request_id": str(pack.recommendation_request_id),
                "payload_version": pack.payload_version,
                "payload_encoding": pack.payload_encoding,
                "payload_base64": base64.b64encode(pack.payload).decode("ascii"),
                "payload_sha256": pack.payload_sha256,
                "created_at_ms": _epoch_ms(pack.created_at),
                "expires_at_ms": _epoch_ms(pack.expires_at),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/recommendations/{request_id}", response_model=None)
    def exact_replay(request: Request, request_id: UUID) -> JSONResponse:
        try:
            response = service.exact_replay(_principal(request).user_id, request_id)
        except RecommendationNotFound:
            raise ApiError(
                "recommendation_not_found", "The recommendation request was not found.", 404
            ) from None
        return _response(response, replay="exact")

    @router.post("/recommendations/{request_id}/replay", response_model=None)
    def algorithmic_replay(request: Request, request_id: UUID) -> JSONResponse:
        try:
            response = service.algorithmic_replay(_principal(request).user_id, request_id)
        except RecommendationNotFound:
            raise ApiError(
                "recommendation_not_found", "The recommendation request was not found.", 404
            ) from None
        except ReplayInputUnavailable:
            raise ApiError(
                "REPLAY_INPUT_UNAVAILABLE",
                "The retained inputs required for replay are unavailable.",
                409,
            ) from None
        return _response(response, replay="algorithmic")

    return router


def _query(
    principal: Principal,
    body: RecommendationRequestBody,
    surface: RecommendationSurface,
) -> RecommendationQuery:
    try:
        return RecommendationQuery(
            user_id=principal.user_id,
            surface=surface,
            context=body.context,
            limit=body.limit,
            exploration=body.exploration,
            seed=body.seed,
            shadow=body.shadow,
        )
    except ValueError:
        raise ApiError(
            "recommendation_request_invalid", "The recommendation request is invalid.", 422
        ) from None


def _response(response: RecommendationResponse, *, replay: str) -> JSONResponse:
    trace = response.request
    return JSONResponse(
        {
            "recommendation_request_id": str(trace.recommendation_request_id),
            "surface": trace.query.surface.value,
            "context": trace.query.context,
            "pipeline": {
                "key": trace.pipeline.pipeline_key,
                "version": trace.pipeline.version,
                "manifest_sha256": trace.pipeline.manifest_sha256,
            },
            "request_sha256": trace.request_sha256,
            "input_snapshot_sha256": trace.snapshot.input_snapshot_sha256,
            "replay": replay,
            "shadow": trace.query.shadow,
            "items": [_item(trace.recommendation_request_id, item) for item in response.items],
        },
        headers={"Cache-Control": "no-store"},
    )


def _item(request_id: UUID, item: RankedRecommendation) -> dict[str, object]:
    return {
        "recommendation_request_id": str(request_id),
        "recording_id": str(item.recording_id),
        "source_rank": item.source_rank,
        "score": item.score,
        "score_kind": "heuristic",
        "reason_code": item.reason_code,
        "reason_codes": list(item.reason_codes),
        "section": item.section,
        "contributions": [
            {
                "source_key": contribution.source_key,
                "source_version": contribution.source_version,
                "source_rank": contribution.source_rank,
                "raw_score": contribution.raw_score,
                "provenance": contribution.provenance,
            }
            for contribution in item.contributions
        ],
    }


def _principal(request: Request) -> Principal:
    value = request.state.principal
    if not isinstance(value, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return value


def _pipeline_unavailable() -> ApiError:
    return ApiError(
        "recommendation_pipeline_unavailable",
        "The requested recommendation pipeline is unavailable.",
        409,
    )


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("pack timestamp is invalid")
    return int(value.astimezone(UTC).timestamp() * 1000)


__all__ = (
    "OfflinePackRequestBody",
    "RecommendationHttpService",
    "RecommendationRequestBody",
    "create_recommendation_router",
)
