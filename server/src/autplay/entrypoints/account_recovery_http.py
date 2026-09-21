"""Bounded recovery endpoints; codes travel only in a private request header."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.account_recovery import AccountRecoveryService
from autplay.domain.account_recovery import AccountRecoveryError, unique_pairs
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.http import ApiError

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _constant(value: str) -> None:
    del value
    raise ValueError("non-JSON constant")


def _error(code: str, status: int = 403) -> ApiError:
    if code in {"recovery_request_invalid", "recovery_code_invalid"}:
        status = 400
    elif code in {
        "operation_conflict",
        "recovery_generation_conflict",
        "recovery_operation_superseded",
        "recovery_code_unchanged",
        "recovery_key_already_bound",
        "account_device_limit_reached",
    }:
        status = 409
    headers = dict(_HEADERS)
    if code == "account_recovery_rate_limited":
        status = 429
        headers["Retry-After"] = "900"
    return ApiError(
        code=code,
        message="Account recovery is unavailable.",
        status_code=status,
        headers=headers,
        retryable=code == "account_recovery_rate_limited",
    )


def create_account_recovery_router(
    service: AccountRecoveryService | None,
    *,
    authenticated: Callable[[Request], None],
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def actor(request: Request) -> Principal:
        authenticated(request)
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, Principal):
            raise AccountRecoveryError()
        return principal

    def process(kind: str, request: Request, body: dict[str, Any]) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            source = (
                canonical_source(request)
                if canonical_source is not None
                else request.client.host
                if request.client
                else None
            )
            service.rate_gate(source or "unknown")
            if kind == "status":
                result = service.status(actor(request))
            elif kind == "configure":
                result = service.configure(actor(request), body)
            elif kind == "outcome":
                refreshes = request.headers.getlist("x-autplay-recovery-refresh")
                if len(refreshes) != 1 or not 32 <= len(refreshes[0]) <= 128:
                    raise AccountRecoveryError()
                result = service.recover_outcome(refreshes[0], body)
            else:
                codes = request.headers.getlist("x-autplay-recovery-code")
                if len(codes) != 1 or not 1 <= len(codes[0]) <= 96:
                    raise AccountRecoveryError()
                handler = service.preview if kind == "preview" else service.recover
                result = handler(codes[0], body)
            return JSONResponse(result, headers=_HEADERS)
        except (AccountRecoveryError, ResourceAdmissionError) as error:
            raise _error(error.code) from None
        except ApiError as error:
            raise replace(error, headers={**(error.headers or {}), **_HEADERS}) from None

    async def dispatch(kind: str, request: Request) -> JSONResponse:
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
                bytes(payload).decode("utf-8"),
                object_pairs_hook=unique_pairs,
                parse_constant=_constant,
            )
            if not isinstance(body, dict):
                raise ValueError("object required")
        except AccountRecoveryError, ValueError, UnicodeError, RecursionError:
            raise _error("recovery_request_invalid") from None
        return await run_in_threadpool(process, kind, request, body)

    @router.get("/account/recovery")
    def status(request: Request) -> JSONResponse:
        return process("status", request, {})

    @router.post("/account/recovery")
    async def configure(request: Request) -> JSONResponse:
        return await dispatch("configure", request)

    @router.post("/recovery/preview")
    async def preview(request: Request) -> JSONResponse:
        return await dispatch("preview", request)

    @router.post("/recovery/commit")
    async def recover(request: Request) -> JSONResponse:
        return await dispatch("recover", request)

    @router.post("/recovery/outcome")
    async def outcome(request: Request) -> JSONResponse:
        return await dispatch("outcome", request)

    return router
