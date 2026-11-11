"""Two fixed D1 write routes; validate raw duplicate-safe JSON before proof."""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_profile_v2 import MAX_DOCUMENT_BYTES, DeviceProfileV2Error
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_profile_v2_router(service: DeviceProfileServiceV2 | None) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2")

    @router.post("/enrollment/exchanges")
    async def enrollment_exchange(request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        if service is None:
            raise _error("device_contract_unavailable", 503)
        try:
            result, replayed = await run_in_threadpool(service.enrollment_exchange, raw)
        except (
            DeviceProfileV2Error,
            DeviceCompatibilityError,
            ProfilePairingError,
            ResourceAdmissionError,
            SQLAlchemyError,
        ) as error:
            raise _service_error(error) from None
        return JSONResponse(result, status_code=200 if replayed else 201, headers=_NO_STORE)

    @router.post("/sessions/rotate")
    async def session_rotate(request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        if service is None:
            raise _error("device_contract_unavailable", 503)
        try:
            result, _ = await run_in_threadpool(service.session_rotate, raw)
        except (
            DeviceProfileV2Error,
            DeviceCompatibilityError,
            ProfilePairingError,
            ResourceAdmissionError,
            SQLAlchemyError,
        ) as error:
            raise _service_error(error) from None
        return JSONResponse(result, headers=_NO_STORE)

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
    if isinstance(error, (DeviceProfileV2Error, DeviceCompatibilityError)):
        return _error(error.code, error.status)
    if isinstance(error, ResourceAdmissionError):
        return _error(error.code, 409 if error.code == "account_device_limit_reached" else 503)
    if isinstance(error, ProfilePairingError):
        status = (
            409
            if error.code == "operation_conflict"
            else 403
            if error.code == "unauthorized"
            else 401
        )
        return _error(error.code, status)
    # Database exception strings/parameters may contain private evidence. Never
    # attach them to a public error or pass them to the runtime exception logger.
    return _error("device_contract_unavailable", 503)


def _error(code: str, status: int) -> ApiError:
    return ApiError(
        code=code,
        status_code=status,
        message="The requested device operation is unavailable.",
        headers=dict(_NO_STORE),
    )


__all__ = ("create_device_profile_v2_router",)
