"""Four fixed D1 portable routes; raw bodies remain transient and redacted."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Final

from fastapi import APIRouter, Request
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.adapters.portable_account_crypto_v2 import MAX_DOCUMENT_BYTES, PortableAccountV2Error
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
from autplay.application.portable_account import PortableAccountService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.device_compatibility import DeviceCompatibilityError, family_is_qualified
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints.composition import build_identifier_sessions
from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
from autplay.runtime.http import ApiError
from autplay.runtime.settings import ApiSettings

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def build_device_portable_v2_service(
    settings: ApiSettings,
    engine: Engine,
    *,
    identity: ProfilePairingService | None,
    deletion_evidence: DeletionEvidenceRuntime | None = None,
) -> DevicePortableServiceV2 | None:
    """Retain exact replay/expiry handlers with either admission flag disabled.

    M5 identity is the already assembled owner. Use the same configured access
    issuer/key/audience, with the codec's existing 15-minute hard ceiling so a
    reduced NEW-grant TTL does not prevent reissuing an older exact live grant.
    Source qualification gates first effects without unmounting replay routes.
    """
    if identity is None:
        return None
    access = Hs256AccessTokenCodec(
        settings.auth_signing_secret.get_secret_value(),
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        max_ttl=timedelta(minutes=15),
    )
    portable = PortableAccountService(
        build_identifier_sessions(settings, engine, deletion_evidence=deletion_evidence),
        access,
        timedelta(seconds=settings.access_token_ttl_seconds),
        settings.public_access_source_hmac_secret.get_secret_value().encode("utf-8"),
    )
    return DevicePortableServiceV2(
        portable,
        identity=identity,
        enabled=lambda: (
            settings.device_compatibility_v2_enabled and family_is_qualified("portable")
        ),
        portable_enabled=lambda: settings.portable_accounts_enabled,
    )


def create_device_portable_v2_router(
    service: DevicePortableServiceV2 | None,
    *,
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2/portable")

    async def invoke(request: Request, operation: str) -> JSONResponse:
        raw = await _raw_document(request)
        if service is None:
            raise _error("device_contract_unavailable", 503)
        # Supply precisely the same trusted-proxy source resolver used by v1.
        # Never derive a separate v2 key from route, platform or contract version.
        source = (canonical_source(request) if canonical_source else None) or (
            request.client.host if request.client else "unknown"
        )
        handler = {
            "register_challenge": service.register_challenge,
            "login_challenge": service.login_challenge,
            "register": service.register,
            "login": service.login,
        }[operation]
        try:
            result = await run_in_threadpool(handler, raw, source=source)
        except (
            PortableAccountV2Error,
            DeviceCompatibilityError,
            ProfilePairingError,
            ResourceAdmissionError,
            SQLAlchemyError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ) as error:
            raise _service_error(error) from None
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/register-challenge")
    async def register_challenge(request: Request) -> JSONResponse:
        return await invoke(request, "register_challenge")

    @router.post("/login-challenge")
    async def login_challenge(request: Request) -> JSONResponse:
        return await invoke(request, "login_challenge")

    @router.post("/register")
    async def register(request: Request) -> JSONResponse:
        return await invoke(request, "register")

    @router.post("/login")
    async def login(request: Request) -> JSONResponse:
        return await invoke(request, "login")

    return router


async def _raw_document(request: Request) -> bytes:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or content_types[0].lower() != "application/json":
        raise _error("device_contract_invalid", 400)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_DOCUMENT_BYTES:
            raise _error("device_contract_invalid", 413)
        raw.extend(chunk)
    return bytes(raw)


def _service_error(error: Exception) -> ApiError:
    if isinstance(error, (PortableAccountV2Error, DeviceCompatibilityError)):
        return _error(error.code, error.status)
    if isinstance(error, (ProfilePairingError, ResourceAdmissionError)):
        code = error.code
        if code == "rate_limited":
            return _error(code, 429)
        if code in {
            "operation_conflict",
            "public_id_unavailable",
            "account_identity_conflict",
            "account_device_limit_reached",
            "account_limit_reached",
            "account_claim_required",
        }:
            return _error(code, 409)
        if code == "account_receipt_expired":
            return _error(code, 403)
        if code in {
            "account_authentication_failed",
            "device_key_blocked",
            "identity_changed",
            "account_challenge_expired",
        }:
            return _error(code, 401)
    # Never expose database parameters, JSON decoder bodies, private identity
    # configuration or crypto exception strings to response/logging middleware.
    return _error("device_contract_unavailable", 503)


def _error(code: str, status: int) -> ApiError:
    return ApiError(
        code=code,
        status_code=status,
        message="The account operation could not be completed.",
        headers=dict(_NO_STORE),
    )


__all__ = ("build_device_portable_v2_service", "create_device_portable_v2_router")
