"""M5B signed discovery and bounded profile API routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccessTokenClaims, AccountRole, InvalidAccessTokenError, Principal
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class InvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(pattern="^v1$")
    schema_version: int = 1
    operation_id: UUID
    expires_in_seconds: int = Field(ge=1, le=1800)


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "v1"
    schema_version: int = 1
    exchange_id: UUID
    binding_commit_id: UUID
    invitation_id: UUID
    invitation_secret: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_api_origin: str = Field(max_length=2048)
    expected_stream_origin: str = Field(max_length=2048)
    expected_user_id: UUID
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(pattern="^ANDROID$")
    app_version: str = Field(min_length=1, max_length=32)
    device_public_key_spki_b64: str = Field(min_length=32, max_length=256)
    device_key_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    next_refresh_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: str = Field(pattern="^ES256-P1363$")
    device_signature_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class RotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "v1"
    schema_version: int = 1
    rotation_id: UUID
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    device_id: UUID
    parent_session_id: UUID
    current_generation: int = Field(ge=0)
    current_refresh_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    next_refresh_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: str = Field(pattern="^ES256-P1363$")
    device_signature_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "v1"
    schema_version: int = 1
    operation_id: UUID
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")


def create_profile_pairing_router(
    service: ProfilePairingService | None,
    *,
    authenticated: Callable[[Request], None],
    decode_access: Callable[[str], AccessTokenClaims] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/pairing/discovery")
    def discovery() -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        return JSONResponse(service.discovery(), headers=_NO_STORE)

    @router.get("/profile/capabilities", dependencies=[Depends(authenticated)])
    def capabilities(request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        return JSONResponse(service.capabilities(_principal(request)), headers=_NO_STORE)

    @router.post(
        "/pairing/enrollment/invitations", status_code=201, dependencies=[Depends(authenticated)]
    )
    def invite(body: InvitationRequest, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result = service.issue_invitation(
                _principal(request), body.operation_id, body.expires_in_seconds
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.post("/pairing/enrollment/exchanges")
    def exchange(body: ExchangeRequest) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result, replayed = service.exchange(_dump(body))
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, status_code=200 if replayed else 201, headers=_NO_STORE)

    @router.post(
        "/pairing/enrollment/invitations/{invitation_id}/cancel",
        dependencies=[Depends(authenticated)],
    )
    def cancel(invitation_id: UUID, body: LifecycleCommand, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result = service.cancel_invitation(
                _principal(request), invitation_id, body.operation_id, body.reason_code
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.get("/account/devices", dependencies=[Depends(authenticated)])
    def devices(request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        return JSONResponse(service.list_devices(_principal(request)), headers=_NO_STORE)

    @router.get("/account/sessions", dependencies=[Depends(authenticated)])
    def sessions(request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        return JSONResponse(service.list_sessions(_principal(request)), headers=_NO_STORE)

    @router.post("/account/sessions/rotate")
    def rotate(body: RotationRequest) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result, _ = service.rotate(_dump(body))
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/account/sessions/current/logout")
    def logout_current(body: LifecycleCommand, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        retry = _lifecycle_retry(
            request,
            service,
            authenticated,
            decode_access,
            body,
            "profile.session_logged_out",
            "USER_SESSION",
            None,
        )
        if retry is not None:
            return JSONResponse(retry, headers=_NO_STORE)
        try:
            result = service.logout_current(
                _principal(request), body.operation_id, body.reason_code, _access_token_id(request)
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/account/sessions/logout-all")
    def logout_all(body: LifecycleCommand, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        retry = _lifecycle_retry(
            request,
            service,
            authenticated,
            decode_access,
            body,
            "profile.all_sessions_logged_out",
            "USER_ACCOUNT",
            None,
        )
        if retry is not None:
            return JSONResponse(retry, headers=_NO_STORE)
        try:
            result = service.logout_all(
                _principal(request), body.operation_id, body.reason_code, _access_token_id(request)
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/account/devices/{device_id}/revoke")
    def revoke_device(device_id: UUID, body: LifecycleCommand, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        retry = _lifecycle_retry(
            request,
            service,
            authenticated,
            decode_access,
            body,
            "profile.device_revoked",
            "DEVICE",
            device_id,
        )
        if retry is not None:
            return JSONResponse(retry, headers=_NO_STORE)
        try:
            result = service.revoke_device(
                _principal(request),
                device_id,
                body.operation_id,
                body.reason_code,
                _access_token_id(request),
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    return router


def _principal(request: Request) -> Principal:
    principal = request.state.principal
    if not isinstance(principal, Principal):
        raise RuntimeError("authenticated request is missing principal")
    return principal


def _access_token_id(request: Request) -> UUID | None:
    value = getattr(request.state, "access_token_id", None)
    return value if isinstance(value, UUID) else None


def _lifecycle_retry(
    request: Request,
    service: ProfilePairingService,
    authenticated: Callable[[Request], None],
    decode_access: Callable[[str], AccessTokenClaims] | None,
    body: LifecycleCommand,
    action: str,
    target_type: str,
    explicit_target_id: UUID | None,
) -> dict[str, object] | None:
    """Require active auth first; a revoked JWT may only read its exact receipt."""
    try:
        authenticated(request)
        return None
    except ApiError:
        pass
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise _authentication_required()
    if decode_access is None:
        raise _authentication_required()
    try:
        claims = decode_access(token)
    except InvalidAccessTokenError as error:
        raise _authentication_required() from error
    target_id = (
        claims.session_id
        if target_type == "USER_SESSION"
        else claims.user_id
        if target_type == "USER_ACCOUNT"
        else explicit_target_id
    )
    if target_id is None:
        raise _authentication_required()
    role = AccountRole.USER
    principal = Principal(claims.user_id, claims.device_id, claims.session_id, role)
    result = service.lifecycle_retry(
        principal=principal,
        access_token_id=claims.token_id,
        operation_id=body.operation_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason_code=body.reason_code,
    )
    if result is None:
        raise _authentication_required()
    request.state.principal = principal
    request.state.access_token_id = claims.token_id
    return result


def _error(code: str, status: int) -> ApiError:
    return ApiError(
        code=code,
        message="The requested profile operation is unavailable.",
        status_code=status,
        headers=dict(_NO_STORE),
    )


def _authentication_required() -> ApiError:
    return ApiError(
        code="authentication_required",
        message="Authentication is required.",
        status_code=401,
        headers={**_NO_STORE, "WWW-Authenticate": "Bearer"},
    )


def _profile_error(error: ProfilePairingError) -> ApiError:
    status = (
        429
        if error.code == "enrollment_rate_limited"
        else 403
        if error.code == "unauthorized"
        else 401
    )
    return _error(error.code, status)


def _dump(body: BaseModel) -> dict[str, object]:
    return body.model_dump(mode="json")


__all__ = ("create_profile_pairing_router",)
