"""Versioned HTTP routes for P03 device-session authentication."""

from __future__ import annotations

from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.responses import JSONResponse, Response

from autplay.application.auth import AuthService
from autplay.domain.auth import (
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    OwnedObjectNotFoundError,
    Principal,
    RefreshTokenReplayError,
    TokenPair,
)
from autplay.runtime.http import ApiError

_NO_STORE_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}


class RefreshRequest(BaseModel):
    """One bounded opaque refresh credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_token: SecretStr = Field(min_length=1, max_length=256)


def create_auth_router(service: AuthService) -> APIRouter:
    """Build real auth/device routes around one application service."""

    router = APIRouter()

    def authenticate_request(request: Request) -> None:
        authorizations = request.headers.getlist("authorization")
        if len(authorizations) != 1:
            raise _access_error()
        authorization = authorizations[0]
        scheme, separator, credential = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not credential:
            raise _access_error()
        try:
            request.state.principal = service.authenticate_access(credential)
        except InvalidAccessTokenError as error:
            raise _access_error() from error

    authenticated = [Depends(authenticate_request)]

    @router.post("/auth/refresh", response_model=None)
    def rotate_refresh(body: RefreshRequest, request: Request) -> JSONResponse:
        try:
            pair = service.rotate_refresh(
                body.refresh_token.get_secret_value(),
                request_id=_request_id(request),
            )
        except RefreshTokenReplayError as error:
            raise ApiError(
                code=error.code,
                message="The refresh credential was already used.",
                status_code=401,
                headers=dict(_NO_STORE_HEADERS),
            ) from error
        except InvalidRefreshTokenError as error:
            raise ApiError(
                code=error.code,
                message="The refresh credential is invalid.",
                status_code=401,
                headers=dict(_NO_STORE_HEADERS),
            ) from error
        return _token_response(pair)

    @router.post("/auth/logout", status_code=204, dependencies=authenticated)
    def logout(request: Request) -> Response:
        principal = _principal(request)
        service.logout(principal, request_id=_request_id(request))
        return Response(status_code=204, headers=_NO_STORE_HEADERS)

    @router.post("/auth/logout-all", status_code=204, dependencies=authenticated)
    def logout_all(request: Request) -> Response:
        principal = _principal(request)
        service.logout_all(principal, request_id=_request_id(request))
        return Response(status_code=204, headers=_NO_STORE_HEADERS)

    @router.post(
        "/devices/{device_id}/revoke",
        status_code=204,
        dependencies=authenticated,
    )
    def revoke_device(
        device_id: UUID,
        request: Request,
    ) -> Response:
        principal = _principal(request)
        try:
            service.revoke_device(
                principal,
                device_id,
                request_id=_request_id(request),
            )
        except OwnedObjectNotFoundError as error:
            raise ApiError(
                code="not_found",
                message="The requested resource was not found.",
                status_code=404,
            ) from error
        return Response(status_code=204, headers=_NO_STORE_HEADERS)

    return router


def _access_error() -> ApiError:
    return ApiError(
        code="authentication_required",
        message="Authentication is required.",
        status_code=401,
        headers={**_NO_STORE_HEADERS, "WWW-Authenticate": "Bearer"},
    )


def _request_id(request: Request) -> UUID:
    return UUID(str(request.state.request_id))


def _principal(request: Request) -> Principal:
    principal = request.state.principal
    if not isinstance(principal, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return principal


def _token_response(pair: TokenPair) -> JSONResponse:
    return JSONResponse(
        content={
            "access_token": pair.access_token,
            "refresh_token": pair.refresh_token,
            "token_type": pair.token_type,
            "access_expires_at": pair.access_expires_at.isoformat(),
            "refresh_expires_at": pair.refresh_expires_at.isoformat(),
            "user_id": str(pair.user_id),
            "device_id": str(pair.device_id),
            "session_id": str(pair.session_id),
        },
        headers=_NO_STORE_HEADERS,
    )


__all__ = ("RefreshRequest", "create_auth_router")
