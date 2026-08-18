"""Authenticated HTTP boundary for the P09 sync protocol."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from autplay.application.sync import SyncError, SyncService
from autplay.entrypoints.auth_http import _principal, _request_id
from autplay.runtime.http import ApiError


def create_sync_router(
    service: SyncService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    """Expose bounded, bearer-protected sync routes without logging payloads."""
    router = APIRouter(dependencies=[Depends(authenticated)])

    @router.post("/devices/bind")
    async def bind(body: dict[str, Any], request: Request) -> dict[str, Any]:
        return _call(lambda: service.bind(_principal(request), body))

    @router.post("/sync/push")
    async def push(body: dict[str, Any], request: Request) -> dict[str, Any]:
        return _call(lambda: service.push(_principal(request), body, _request_id(request)))

    @router.get("/sync/pull")
    async def pull(request: Request) -> dict[str, Any]:
        body: dict[str, Any] = dict(request.query_params)
        body["protocol_version"] = _query_int(body, "protocol_version")
        if "limit" in body:
            body["limit"] = _query_int(body, "limit")
        return _call(lambda: service.pull(_principal(request), body))

    @router.post("/sync/bootstrap")
    async def bootstrap(body: dict[str, Any], request: Request) -> dict[str, Any]:
        return _call(lambda: service.bootstrap(_principal(request), body))

    @router.get("/sync/status")
    async def status(request: Request) -> dict[str, Any]:
        body: dict[str, Any] = dict(request.query_params)
        body["protocol_version"] = _query_int(body, "protocol_version")
        return _call(lambda: service.status(_principal(request), body))

    return router


def _call(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except SyncError as error:
        reset = error.code in {"CURSOR_INVALID", "JOURNAL_RESET_REQUIRED"}
        code = (
            "DEVICE_RESET_REQUIRED"
            if error.code == "JOURNAL_RESET_REQUIRED"
            else error.code
            if error.code == "CURSOR_INVALID"
            else error.code.lower()
        )
        raise ApiError(
            code,
            "The sync request could not be applied.",
            410 if error.code == "CURSOR_INVALID" else 409 if reset else 422,
            error.retryable,
            details={"bootstrap_required": True} if reset else None,
        ) from error


def _query_int(body: dict[str, Any], key: str) -> int:
    try:
        return int(body[key])
    except KeyError, ValueError:
        raise ApiError("request_validation_failed", "The sync request is invalid.", 422) from None


__all__ = ("create_sync_router",)
