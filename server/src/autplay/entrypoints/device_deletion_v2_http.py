"""Six fixed deletion v2 routes; full original bodies and transient headers.

API composition mounts under /api/v1 with one independent evidence runtime.
Do not remove terminal handlers when feature switches are off. Qualification is pending.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import rfc8785
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.device_deletion_service_v2 import DeviceDeletionServiceV2
from autplay.domain.account_deletion import AccountDeletionError
from autplay.domain.account_recovery import AccountRecoveryError
from autplay.domain.auth import AccessTokenClaims, InvalidAccessTokenError, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_deletion_v2 import (
    MAX_BODY_BYTES,
    DeletionV2Error,
    parse_deletion_request,
)
from autplay.domain.device_deletion_v2 import DeletionOperation as Op
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints.account_deletion_http import _error as deletion_error
from autplay.entrypoints.device_profile_v2_http import _error
from autplay.runtime.http import ApiError

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_deletion_v2_router(
    service: DeviceDeletionServiceV2 | None,
    *,
    authenticate: Callable[[str], tuple[Principal, AccessTokenClaims]] | None = None,
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2/deletion")

    def invoke(operation: Op, request: Request, raw: bytes) -> JSONResponse:
        try:
            parse_deletion_request(
                operation, raw
            )  # Body errors precede configuration/header errors.
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
                if operation is Op.CANCEL_OUTCOME
                else "x-autplay-recovery-code"
            )
            secrets = request.headers.getlist(name)
            minimum, maximum = (32, 128) if operation is Op.CANCEL_OUTCOME else (1, 96)
            if len(secrets) != 1 or not minimum <= len(secrets[0]) <= maximum:
                raise AccountDeletionError()
            if operation is Op.REQUEST:
                authorizations = request.headers.getlist("authorization")
                if len(authorizations) != 1:
                    raise _error("authentication_required", 401)
                scheme, separator, bearer = authorizations[0].partition(" ")
                if separator != " " or scheme.lower() != "bearer" or not bearer:
                    raise _error("authentication_required", 401)
                if authenticate is None:
                    raise _error("device_contract_unavailable", 503)
                actor, claims = authenticate(bearer)
                if type(actor) is not Principal or type(claims) is not AccessTokenClaims:
                    raise _error("authentication_required", 401)
                result = service.request(actor, claims, secrets[0], raw)
            else:
                handler = {
                    Op.REQUEST_RECEIPT: service.request_receipt,
                    Op.REQUEST_RESOLVE: service.request_resolve,
                    Op.PREVIEW: service.preview,
                    Op.CANCEL: service.cancel,
                    Op.CANCEL_OUTCOME: service.cancel_outcome,
                }[operation]
                result = handler(secrets[0], raw)
            if len(rfc8785.dumps(result)) > 65536:
                raise _error("device_contract_unavailable", 503)
            return JSONResponse(result, headers=_NO_STORE)
        except DeletionV2Error as error:
            raise _error(error.code, error.http_status) from None
        except DeviceCompatibilityError as error:
            raise _error(error.code, error.status) from None
        except InvalidAccessTokenError:
            raise _error("authentication_required", 401) from None
        except (AccountDeletionError, AccountRecoveryError, ResourceAdmissionError) as error:
            raise deletion_error(error.code) from None
        except DeletionEvidenceError:
            raise deletion_error("deletion_evidence_unavailable", 503) from None
        except SQLAlchemyError:
            raise _error("device_contract_unavailable", 503) from None
        except ApiError as error:
            raise replace(error, headers={**(error.headers or {}), **_NO_STORE}) from None

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

    @router.post("/requests")
    async def request_deletion(request: Request) -> JSONResponse:
        return await dispatch(Op.REQUEST, request)

    @router.post("/request-receipt")
    async def request_receipt(request: Request) -> JSONResponse:
        return await dispatch(Op.REQUEST_RECEIPT, request)

    @router.post("/request-resolve")
    async def request_resolve(request: Request) -> JSONResponse:
        return await dispatch(Op.REQUEST_RESOLVE, request)

    @router.post("/cancel/preview")
    async def preview(request: Request) -> JSONResponse:
        return await dispatch(Op.PREVIEW, request)

    @router.post("/cancel/commit")
    async def cancel(request: Request) -> JSONResponse:
        return await dispatch(Op.CANCEL, request)

    @router.post("/cancel/outcome")
    async def cancel_outcome(request: Request) -> JSONResponse:
        return await dispatch(Op.CANCEL_OUTCOME, request)

    return router


__all__ = ("create_device_deletion_v2_router",)
