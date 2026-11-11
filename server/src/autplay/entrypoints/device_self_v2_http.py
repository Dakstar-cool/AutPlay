"""Six fixed, bounded SELF v2 routes. Terminal handlers survive both gate changes."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import rfc8785
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.device_self_service_v2 import DeviceSelfServiceV2
from autplay.domain.auth import Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_self_v2 import MAX_BODY_BYTES, SelfV2Error, parse_self_request
from autplay.domain.device_self_v2 import SelfOperation as Op
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.self_device_pairing import SelfPairingError, secret_hash
from autplay.entrypoints.device_profile_v2_http import _error
from autplay.runtime.http import ApiError

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def create_device_self_v2_router(
    service: DeviceSelfServiceV2 | None,
    *,
    authenticated: Callable[[Request], None],
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/device-compatibility/v2/self/ceremonies")

    def invoke(
        operation: Op | None, request: Request, raw: bytes, path_id: str | None
    ) -> JSONResponse:
        try:
            parsed = parse_self_request(operation, raw) if operation is not None else None
            identifier = None
            if path_id is not None:
                try:
                    identifier = UUID(path_id)
                    if str(identifier) != path_id:
                        raise ValueError("UUID")
                except ValueError:
                    raise _error("device_contract_invalid", 400) from None
                if parsed is not None and parsed.document["ceremony_id"] != path_id:
                    raise _error("device_contract_invalid", 400)
            if service is None:
                raise _error("device_contract_unavailable", 503)
            source = (
                canonical_source(request)
                if canonical_source is not None
                else (request.client.host if request.client else None)
            )
            service.rate_gate(source or "unknown")
            if operation in {None, Op.START, Op.DECISION}:
                # Duplicate Authorization is rejected before the existing bearer middleware.
                if len(request.headers.getlist("authorization")) != 1:
                    raise _error("self_pairing_unavailable", 401)
                authenticated(request)
                actor = getattr(request.state, "principal", None)
                if not isinstance(actor, Principal):
                    raise SelfPairingError()
                if operation is None:
                    if identifier is None:
                        raise SelfPairingError()
                    result = service.status(actor, identifier)
                elif operation is Op.START:
                    result = service.start(actor, raw)
                else:
                    result = service.decide(actor, raw)
            else:
                secrets = request.headers.getlist("x-autplay-pairing-secret")
                if len(secrets) != 1:
                    raise _error("self_pairing_unavailable", 401)
                try:
                    secret_hash(secrets[0])  # canonical encoded-secret spelling, never stored here
                except SelfPairingError:
                    raise _error("self_pairing_unavailable", 401) from None
                handler = {
                    Op.CLAIM: service.claim,
                    Op.POLL: service.poll,
                    Op.EXCHANGE: service.exchange,
                }[operation]
                result = handler(secrets[0], raw)
            if len(rfc8785.dumps(result)) > 65536:
                raise _error("device_contract_unavailable", 503)
            return JSONResponse(result, headers=_NO_STORE)
        except SelfV2Error as error:
            raise _error(error.code, error.http_status) from None
        except DeviceCompatibilityError as error:
            raise _error(error.code, error.status) from None
        except SelfPairingError as error:
            if error.code == "self_pairing_rate_limited":
                raise ApiError(
                    code=error.code,
                    status_code=429,
                    retryable=True,
                    message="Device pairing is unavailable.",
                    headers={**_NO_STORE, "Retry-After": "15"},
                ) from None
            status = (
                409
                if error.code
                in {
                    "operation_conflict",
                    "self_pairing_revision_conflict",
                    "self_pairing_key_already_bound",
                    "account_device_limit_reached",
                }
                else 403
            )
            raise _error(error.code, status) from None
        except SQLAlchemyError, ProfilePairingError:
            raise _error("device_contract_unavailable", 503) from None

    async def dispatch(operation: Op, request: Request, path_id: str | None = None) -> JSONResponse:
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or content_types[0].lower() != "application/json":
            raise _error("device_contract_invalid", 400)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise _error("device_contract_invalid", 413)
            raw.extend(chunk)
        return await run_in_threadpool(invoke, operation, request, bytes(raw), path_id)

    @router.post("")
    async def start(request: Request) -> JSONResponse:
        return await dispatch(Op.START, request)

    @router.get("/{ceremony_id}")
    def status(ceremony_id: str, request: Request) -> JSONResponse:
        return invoke(None, request, b"", ceremony_id)

    @router.post("/{ceremony_id}/claim")
    async def claim(ceremony_id: str, request: Request) -> JSONResponse:
        return await dispatch(Op.CLAIM, request, ceremony_id)

    @router.post("/{ceremony_id}/poll")
    async def poll(ceremony_id: str, request: Request) -> JSONResponse:
        return await dispatch(Op.POLL, request, ceremony_id)

    @router.post("/{ceremony_id}/decision")
    async def decision(ceremony_id: str, request: Request) -> JSONResponse:
        return await dispatch(Op.DECISION, request, ceremony_id)

    @router.post("/{ceremony_id}/exchange")
    async def exchange(ceremony_id: str, request: Request) -> JSONResponse:
        return await dispatch(Op.EXCHANGE, request, ceremony_id)

    return router


__all__ = ("create_device_self_v2_router",)
