"""Three closed recovery v2 routes; two exact signed models, transient headers."""

from __future__ import annotations

from collections.abc import Callable

import rfc8785
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.device_recovery_service_v2 import DeviceRecoveryServiceV2
from autplay.domain.account_recovery import AccountRecoveryError
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_recovery_v2 import (
    MAX_BODY_BYTES,
    RecoveryV2Error,
    parse_recovery_request,
)
from autplay.domain.device_recovery_v2 import RecoveryOperation as Op
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints.account_recovery_http import _error as recovery_error
from autplay.entrypoints.device_profile_v2_http import _error

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_recovery_v2_router(
    service: DeviceRecoveryServiceV2 | None,
    *,
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2/recovery")

    def invoke(operation: Op, request: Request, raw: bytes) -> JSONResponse:
        try:
            parse_recovery_request(operation, raw)  # Invalid body precedes missing configuration.
            if service is None:
                raise _error("device_contract_unavailable", 503)
            source = (
                canonical_source(request)
                if canonical_source is not None
                else (request.client.host if request.client else None)
            )
            service.rate_gate(source or "unknown")
            name = (
                "x-autplay-recovery-refresh"
                if operation is Op.OUTCOME
                else "x-autplay-recovery-code"
            )
            secrets = request.headers.getlist(name)
            minimum, maximum = (32, 128) if operation is Op.OUTCOME else (1, 96)
            if len(secrets) != 1 or not minimum <= len(secrets[0]) <= maximum:
                raise AccountRecoveryError()
            handler = {
                Op.PREVIEW: service.preview,
                Op.RECOVER: service.recover,
                Op.OUTCOME: service.recover_outcome,
            }[operation]
            result = handler(secrets[0], raw)
            if len(rfc8785.dumps(result)) > 65536:
                raise _error("device_contract_unavailable", 503)
            return JSONResponse(result, headers=_NO_STORE)
        except RecoveryV2Error as error:
            raise _error(error.code, error.http_status) from None
        except DeviceCompatibilityError as error:
            raise _error(error.code, error.status) from None
        except (AccountRecoveryError, ResourceAdmissionError) as error:
            raise recovery_error(error.code) from None
        except SQLAlchemyError:
            raise _error("device_contract_unavailable", 503) from None

    async def dispatch(operation: Op, request: Request) -> JSONResponse:
        types = request.headers.getlist("content-type")
        if len(types) != 1 or types[0].lower() != "application/json":
            raise _error("device_contract_invalid", 400)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise _error("device_contract_invalid", 413)
            body.extend(chunk)
        return await run_in_threadpool(invoke, operation, request, bytes(body))

    @router.post("/preview")
    async def preview(request: Request) -> JSONResponse:
        return await dispatch(Op.PREVIEW, request)

    @router.post("/commit")
    async def commit(request: Request) -> JSONResponse:
        return await dispatch(Op.RECOVER, request)

    @router.post("/outcome")
    async def outcome(request: Request) -> JSONResponse:
        return await dispatch(Op.OUTCOME, request)

    return router


__all__ = ("create_device_recovery_v2_router",)
