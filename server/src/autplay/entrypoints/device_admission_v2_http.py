"""Six fixed D1 admission/trusted routes; strict raw transport before authority."""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from functools import partial
from typing import Final

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_profile_v2 import DeviceProfileV2Error
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints.device_profile_v2_http import _error, _raw_document, _service_error
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_admission_v2_router(
    service: DeviceAdmissionServiceV2 | None,
    *,
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2")

    def require_service() -> DeviceAdmissionServiceV2:
        if service is None:
            raise _error("device_contract_unavailable", 503)
        return service

    async def invoke(command: Callable[[], object], *, status: int = 200) -> JSONResponse:
        try:
            outcome = await run_in_threadpool(command)
        except ProfilePairingError as error:
            if error.code.endswith("rate_limited"):
                raise ApiError(
                    code=error.code,
                    status_code=429,
                    retryable=True,
                    message="The requested device operation is unavailable.",
                    headers={
                        **_NO_STORE,
                        "Retry-After": "2"
                        if error.code == "admission_poll_rate_limited"
                        else "900",
                    },
                ) from None
            if error.code == "admission_recovery_required":
                raise _error(error.code, 409) from None
            raise _service_error(error) from None
        except (
            DeviceProfileV2Error,
            DeviceCompatibilityError,
            ResourceAdmissionError,
            SQLAlchemyError,
        ) as error:
            raise _service_error(error) from None
        if isinstance(outcome, tuple):
            result, replayed = outcome
            return JSONResponse(result, status_code=200 if replayed else 201, headers=_NO_STORE)
        return JSONResponse(outcome, status_code=status, headers=_NO_STORE)

    @router.post("/admission/requests")
    async def submit(request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        target = require_service()
        source = (
            canonical_source(request)
            if canonical_source is not None
            else (request.client.host if request.client else None)
        )
        return await invoke(partial(target.admission_submit, raw, source=source), status=202)

    @router.post("/admission/requests/{request_id}/poll")
    async def poll(request_id: str, request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        bearer = _poll_header(request)
        return await invoke(partial(require_service().admission_poll, raw, request_id, bearer))

    @router.post("/admission/requests/{request_id}/recover")
    async def recover(request_id: str, request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        return await invoke(partial(require_service().admission_recover, raw, request_id))

    @router.post("/admission/requests/{request_id}/exchange")
    async def exchange(request_id: str, request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        bearer = _poll_header(request)
        return await invoke(partial(require_service().admission_exchange, raw, request_id, bearer))

    @router.post("/trusted/challenge")
    async def trusted_challenge(request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        return await invoke(partial(require_service().trusted_challenge, raw), status=201)

    @router.post("/trusted/exchanges")
    async def trusted_exchange(request: Request) -> JSONResponse:
        raw = await _raw_document(request)
        return await invoke(partial(require_service().trusted_exchange, raw))

    return router


def _poll_header(request: Request) -> str:
    values = request.headers.getlist("x-autplay-admission-poll")
    if len(values) != 1 or re.fullmatch(r"[A-Za-z0-9_-]{22}", values[0]) is None:
        raise _error("device_contract_invalid", 400)
    value = values[0]
    decoded = base64.urlsafe_b64decode(value + "==")
    if (
        len(decoded) != 16
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise _error("device_contract_invalid", 400)
    return value


__all__ = ("create_device_admission_v2_router",)
