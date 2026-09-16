"""Bounded authenticated and possession-proved self-device pairing routes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain.auth import Principal
from autplay.domain.self_device_pairing import SelfPairingError
from autplay.runtime.http import ApiError

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(value: str) -> None:
    del value
    raise ValueError("non-JSON constant")


def _error(code: str, status: int = 403) -> ApiError:
    if code in {
        "operation_conflict",
        "self_pairing_revision_conflict",
        "account_device_limit_reached",
        "self_pairing_key_already_bound",
    }:
        status = 409
    headers = dict(_HEADERS)
    if code == "self_pairing_rate_limited":
        status = 429
        headers["Retry-After"] = "15"
    return ApiError(
        code=code,
        message="Device pairing is unavailable.",
        status_code=status,
        headers=headers,
        retryable=code == "self_pairing_rate_limited",
    )


def create_self_device_pairing_router(
    service: SelfDevicePairingService | None,
    *,
    authenticated: Callable[[Request], None],
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def source(request: Request) -> str:
        if canonical_source is not None:
            return canonical_source(request) or "unknown"
        return request.client.host if request.client else "unknown"

    def actor(request: Request) -> Principal:
        authenticated(request)
        value = getattr(request.state, "principal", None)
        if not isinstance(value, Principal):
            raise SelfPairingError()
        return value

    def process(
        kind: str, request: Request, body: dict[str, Any], ceremony_id: UUID | None
    ) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            service.rate_gate(source(request))
            if ceremony_id is not None and body.get("ceremony_id") != str(ceremony_id):
                raise SelfPairingError("self_pairing_request_invalid")
            if kind == "start":
                result = service.start(actor(request), body)
            elif kind == "decision":
                result = service.decide(actor(request), body)
            else:
                secrets = request.headers.getlist("x-autplay-pairing-secret")
                if len(secrets) != 1 or len(secrets[0]) != 43:
                    raise SelfPairingError()
                handlers = {
                    "claim": service.claim,
                    "poll": service.poll,
                    "exchange": service.exchange,
                }
                result = handlers[kind](secrets[0], body)
            return JSONResponse(result, headers=_HEADERS)
        except SelfPairingError as error:
            raise _error(error.code) from None

    async def dispatch(
        kind: str, request: Request, ceremony_id: UUID | None = None
    ) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise ValueError("content type")
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > 8192:
                    raise ValueError("body bound")
                payload.extend(chunk)
            body = json.loads(
                bytes(payload).decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
            if not isinstance(body, dict):
                raise ValueError("object required")
        except ValueError, UnicodeError, RecursionError:
            raise _error("self_pairing_request_invalid", 400) from None
        return await run_in_threadpool(process, kind, request, body, ceremony_id)

    @router.post("/account/device-pairings")
    async def start(request: Request) -> JSONResponse:
        return await dispatch("start", request)

    @router.get("/account/device-pairings/{ceremony_id}")
    def status(ceremony_id: UUID, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            service.rate_gate(source(request))
            return JSONResponse(service.status(actor(request), ceremony_id), headers=_HEADERS)
        except SelfPairingError as error:
            raise _error(error.code) from None

    @router.post("/account/device-pairings/{ceremony_id}/decision")
    async def decision(ceremony_id: UUID, request: Request) -> JSONResponse:
        return await dispatch("decision", request, ceremony_id)

    @router.post("/pairing/self-service/{ceremony_id}/{kind}")
    async def proof(ceremony_id: UUID, kind: str, request: Request) -> JSONResponse:
        if kind not in {"claim", "poll", "exchange"}:
            raise _error("self_pairing_unavailable", 404)
        return await dispatch(kind, request, ceremony_id)

    return router
