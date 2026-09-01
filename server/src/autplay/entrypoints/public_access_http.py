"""HTTP boundary for PA2 invite-only account provisioning."""

from __future__ import annotations

from collections.abc import Callable
from ipaddress import IPv6Address, ip_address
from typing import Any, Final
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from autplay.application.public_access import (
    PublicAccessError,
    PublicAccessService,
    normalize_account_display_name,
)
from autplay.domain.auth import Principal
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_CLIENT_IP_HEADER: Final = "x-autplay-client-ip"


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(pattern="^v1$")
    schema_version: int = Field(ge=1, le=1)
    operation_id: UUID
    account_display_name: str
    expires_in_seconds: int = Field(ge=60, le=1800)

    @field_validator("account_display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return normalize_account_display_name(value)


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(pattern="^v1$")
    schema_version: int = Field(ge=1, le=1)
    operation_id: UUID
    reason_code: str = Field(pattern="^(USER_REQUESTED|SECURITY|ACCESS_ENDED)$")


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(pattern="^v1$")
    schema_version: int = Field(ge=1, le=1)
    registration_id: UUID
    binding_commit_id: UUID
    invitation_id: UUID
    invitation_secret: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_api_origin: str = Field(pattern=r"^https://", max_length=2048)
    expected_stream_origin: str = Field(pattern=r"^https://", max_length=2048)
    expected_account_display_name: str = Field(min_length=1, max_length=120)
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


def create_public_access_router(
    service: PublicAccessService | None,
    *,
    authenticated: Callable[[Request], Principal | None],
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def _principal(request: Request) -> Principal:
        principal = authenticated(request)
        if principal is None:
            raise _error("authentication_required", 401)
        return principal

    @router.post("/public-access/account-invitations", status_code=201)
    def create(body: InvitationCreate, request: Request) -> JSONResponse:
        runtime = _service(service)
        principal = _principal(request)
        try:
            result, replay = runtime.create_invitation(principal, _dump(body))
        except PublicAccessError as error:
            raise _public_error(error) from error
        if replay:
            result.pop("invitation_secret", None)
            return JSONResponse(result, headers=_NO_STORE)
        return JSONResponse(
            result,
            status_code=201,
            media_type="application/vnd.autplay.account-invitation+json",
            headers=_NO_STORE,
        )

    @router.get("/public-access/account-invitations")
    def list_invitations(
        request: Request, limit: int = 50, cursor: str | None = None
    ) -> JSONResponse:
        try:
            principal = _principal(request)
            return JSONResponse(
                _service(service).list_invitations(principal, limit, cursor), headers=_NO_STORE
            )
        except PublicAccessError as error:
            raise _public_error(error) from error

    @router.post("/public-access/account-invitations/{invitation_id}/cancel")
    def cancel(invitation_id: UUID, body: LifecycleCommand, request: Request) -> JSONResponse:
        try:
            principal = _principal(request)
            return JSONResponse(
                _service(service).cancel_invitation(principal, invitation_id, _dump(body)),
                headers=_NO_STORE,
            )
        except PublicAccessError as error:
            raise _public_error(error) from error

    @router.post("/public-access/account-invitations/redeem", status_code=201)
    async def redeem(request: Request) -> JSONResponse:
        if request.headers.get("authorization") or request.cookies:
            raise _error("registration_authentication_forbidden", 401)
        content_length = request.headers.get("content-length")
        if content_length is not None and (
            not content_length.isdigit() or int(content_length) > 16 * 1024
        ):
            raise _error("registration_request_too_large", 413)
        raw = await request.body()
        if len(raw) > 16 * 1024:
            raise _error("registration_request_too_large", 413)
        try:
            body = RegistrationRequest.model_validate_json(raw)
        except ValueError as error:
            raise _error("public_access_unavailable", 401) from error
        # PA2 has no configured trusted edge. Ignore peer/forwarded addresses by default and use
        # the server-global fallback; PA3 may inject a resolver only after exact proxy trust.
        source = canonical_source(request) if canonical_source is not None else None
        try:
            result, replay = _service(service).redeem(_dump(body), source)
            return JSONResponse(result, status_code=200 if replay else 201, headers=_NO_STORE)
        except PublicAccessError as error:
            raise _public_error(error) from error

    @router.get("/public-access/accounts")
    def list_accounts(request: Request, limit: int = 50, cursor: str | None = None) -> JSONResponse:
        try:
            principal = _principal(request)
            return JSONResponse(
                _service(service).list_accounts(principal, limit, cursor), headers=_NO_STORE
            )
        except PublicAccessError as error:
            raise _public_error(error) from error

    @router.post("/public-access/accounts/{user_id}/disable")
    def disable(user_id: UUID, body: LifecycleCommand, request: Request) -> JSONResponse:
        try:
            principal = _principal(request)
            return JSONResponse(
                _service(service).disable_account(principal, user_id, _dump(body)),
                headers=_NO_STORE,
            )
        except PublicAccessError as error:
            raise _public_error(error) from error

    return router


def build_exact_proxy_source_resolver(
    trusted_proxy_ip: str | None,
) -> Callable[[Request], str | None] | None:
    """Return a resolver that accepts one canonical client IP from one exact proxy peer."""

    if trusted_proxy_ip is None:
        return None
    trusted_peer = ip_address(trusted_proxy_ip)

    def resolve(request: Request) -> str | None:
        values = request.headers.getlist(_CLIENT_IP_HEADER)
        if len(values) != 1 or request.client is None:
            return None
        try:
            peer = ip_address(request.client.host)
        except ValueError:
            return None
        if peer != trusted_peer:
            return None
        value = values[0]
        if not value or value != value.strip() or "," in value or "%" in value:
            return None
        try:
            source = ip_address(value)
        except ValueError:
            return None
        if isinstance(source, IPv6Address) and source.ipv4_mapped is not None:
            source = source.ipv4_mapped
        return source.compressed.lower()

    return resolve


def _service(value: PublicAccessService | None) -> PublicAccessService:
    if value is None:
        raise _error("public_access_unavailable", 503)
    return value


def _error(code: str, status: int) -> ApiError:
    return ApiError(
        code=code,
        message="The requested public-access operation is unavailable.",
        status_code=status,
        headers=dict(_NO_STORE),
    )


def _public_error(error: PublicAccessError) -> ApiError:
    status = (
        429
        if error.code
        in {"registration_rate_limited", "invitation_limit_reached", "account_limit_reached"}
        else 409
        if error.code in {"operation_conflict", "registration_conflict"}
        else 403
        if error.code == "unauthorized"
        else 404
        if error.code == "not_found"
        else 400
        if error.code == "invalid_cursor"
        else 401
    )
    return _error(error.code, status)


def _dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


__all__ = ("build_exact_proxy_source_resolver", "create_public_access_router")
