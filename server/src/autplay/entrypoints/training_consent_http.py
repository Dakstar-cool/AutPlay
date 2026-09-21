"""Private, bounded caller-owned training consent surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from autplay.application.training_consent import TrainingConsentError, TrainingConsentService
from autplay.domain.auth import Principal
from autplay.domain.training_consent import TrainingConsentEvidenceError
from autplay.runtime.http import ApiError

PRIVATE = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Vary": "Authorization",
}


class _PrivateRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await handler(request)
            except ApiError as error:
                raise ApiError(
                    error.code,
                    error.message,
                    error.status_code,
                    retryable=error.retryable,
                    details=error.details,
                    headers={**(error.headers or {}), **PRIVATE},
                ) from error
            except RequestValidationError as error:
                raise ApiError(
                    "request_validation_failed", "Invalid request.", 422, headers=PRIVATE
                ) from error
            response.headers.update(PRIVATE)
            return response

        return private


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


def create_training_consent_router(
    service: TrainingConsentService | None, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(
        prefix="/privacy/shared-training",
        dependencies=[Depends(authenticated)],
        route_class=_PrivateRoute,
    )

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("principal missing")
        return value

    def call(action: Callable[[], object]) -> object:
        try:
            return action()
        except TrainingConsentError as error:
            status = 401 if error.code == "auth_attention_required" else 409
            raise ApiError(error.code, "Consent operation unavailable.", status) from error
        except TrainingConsentEvidenceError as error:
            raise ApiError(
                "training_consent_evidence_unavailable", "Consent unavailable.", 503
            ) from error
        except (ValueError, KeyError, TypeError) as error:
            raise ApiError("request_validation_failed", "Invalid consent request.", 422) from error

    def available() -> TrainingConsentService:
        if service is None:
            raise ApiError("capability_missing", "Consent unavailable.", 503)
        return service

    @router.get("")
    def get(request: Request) -> object:
        selected = available()
        return call(lambda: selected.get(principal(request)))

    @router.put("")
    async def decide(request: Request) -> object:
        selected = available()
        try:
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise ValueError("content type")
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > 1024:
                    raise ValueError("body bound")
                payload.extend(chunk)
            body = json.loads(
                bytes(payload).decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
            if not isinstance(body, dict):
                raise ValueError("body shape")
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ApiError("request_validation_failed", "Invalid consent request.", 422) from error
        return await run_in_threadpool(call, lambda: selected.decide(principal(request), body))

    return router
