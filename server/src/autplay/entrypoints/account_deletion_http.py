"""Bounded deletion endpoints with explicit request, receipt and cancellation purposes."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.account_deletion import AccountDeletionService
from autplay.domain.account_deletion import AccountDeletionError
from autplay.domain.account_recovery import AccountRecoveryError, unique_pairs
from autplay.domain.auth import Principal
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.http import ApiError

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _constant(value: str) -> None:
    del value
    raise ValueError("non-JSON constant")


def _error(code: str, status: int = 403) -> ApiError:
    if code in {"deletion_request_invalid", "recovery_request_invalid", "recovery_code_invalid"}:
        status = 400
    elif code in {
        "operation_conflict",
        "last_owner_required",
        "deletion_generation_conflict",
        "recovery_generation_conflict",
        "recovery_operation_superseded",
        "recovery_code_unchanged",
        "recovery_key_already_bound",
        "account_device_limit_reached",
        "deletion_resolution_not_ready",
        "deletion_initializing",
    }:
        status = 409
    headers = dict(_HEADERS)
    if code == "account_recovery_rate_limited":
        status = 429
        headers["Retry-After"] = "900"
    return ApiError(
        code=code,
        message="Account deletion is unavailable.",
        status_code=status,
        headers=headers,
        retryable=code == "account_recovery_rate_limited",
    )


def create_account_deletion_router(
    service: AccountDeletionService | None,
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
            service.recovery.rate_gate(source or "unknown")
            if kind == "status":
                result = service.status(actor(request))
            elif kind == "cancel_outcome":
                refreshes = request.headers.getlist("x-autplay-recovery-refresh")
                if len(refreshes) != 1 or not 32 <= len(refreshes[0]) <= 128:
                    raise AccountRecoveryError()
                result = service.cancel_outcome(refreshes[0], body)
            else:
                codes = request.headers.getlist("x-autplay-recovery-code")
                if len(codes) != 1 or not 1 <= len(codes[0]) <= 96:
                    raise AccountRecoveryError()
                if kind == "request":
                    result = service.request(actor(request), codes[0], body)
                elif kind == "request_receipt":
                    result = service.request_receipt(codes[0], body)
                elif kind == "request_resolve":
                    result = service.request_resolve(codes[0], body)
                elif kind == "preview":
                    result = service.preview(codes[0], body)
                else:
                    result = service.cancel(codes[0], body)
            return JSONResponse(result, headers=_HEADERS)
        except (AccountDeletionError, AccountRecoveryError, ResourceAdmissionError) as error:
            raise _error(error.code) from None
        except DeletionEvidenceError:
            raise _error("deletion_evidence_unavailable", 503) from None
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
            raise _error("deletion_request_invalid") from None
        return await run_in_threadpool(process, kind, request, body)

    @router.get("/account/deletion")
    def status(request: Request) -> JSONResponse:
        return process("status", request, {})

    @router.post("/account/deletion")
    async def request_deletion(request: Request) -> JSONResponse:
        return await dispatch("request", request)

    @router.post("/deletion/request-receipt")
    async def request_receipt(request: Request) -> JSONResponse:
        return await dispatch("request_receipt", request)

    @router.post("/deletion/request-resolve")
    async def request_resolve(request: Request) -> JSONResponse:
        return await dispatch("request_resolve", request)

    @router.post("/deletion/cancel/preview")
    async def preview(request: Request) -> JSONResponse:
        return await dispatch("preview", request)

    @router.post("/deletion/cancel/commit")
    async def cancel(request: Request) -> JSONResponse:
        return await dispatch("cancel", request)

    @router.post("/deletion/cancel/outcome")
    async def cancel_outcome(request: Request) -> JSONResponse:
        return await dispatch("cancel_outcome", request)

    return router
