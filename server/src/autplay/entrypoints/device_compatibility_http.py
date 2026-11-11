"""The two explicit v2 negotiation reads; no generic ceremony dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from autplay.application.device_compatibility import DeviceCompatibilityService
from autplay.domain.auth import AccessTokenClaims, InvalidAccessTokenError, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_compatibility_router(
    service: DeviceCompatibilityService | None,
    *,
    authenticate: Callable[[str], tuple[Principal, AccessTokenClaims]] | None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2")

    @router.get("/discovery")
    def discovery() -> JSONResponse:
        if service is None:
            raise _error(DeviceCompatibilityError())
        try:
            return JSONResponse(service.discovery(), headers=_NO_STORE)
        except DeviceCompatibilityError as error:
            raise _error(error) from error

    @router.get("/capabilities")
    def capabilities(request: Request) -> JSONResponse:
        authorizations = request.headers.getlist("authorization")
        if len(authorizations) != 1:
            raise _error(DeviceCompatibilityError("authentication_required", 401))
        scheme, separator, credential = authorizations[0].partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not credential:
            raise _error(DeviceCompatibilityError("authentication_required", 401))
        if service is None or authenticate is None:
            raise _error(DeviceCompatibilityError())
        try:
            principal, claims = authenticate(credential)
            return JSONResponse(service.capabilities(principal, claims), headers=_NO_STORE)
        except InvalidAccessTokenError as error:
            raise _error(DeviceCompatibilityError("authentication_required", 401)) from error
        except DeviceCompatibilityError as error:
            raise _error(error) from error

    return router


def _error(error: DeviceCompatibilityError) -> ApiError:
    return ApiError(
        code=error.code,
        status_code=error.status,
        message="Authentication is required."
        if error.status == 401
        else "Device compatibility is unavailable.",
        headers={**_NO_STORE, **({"WWW-Authenticate": "Bearer"} if error.status == 401 else {})},
    )
