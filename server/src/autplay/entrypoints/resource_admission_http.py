"""Bounded device-only admission HTTP adapter; enabled with the byte enforcement rollout."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    ActivationFence,
    AdmissionState,
    AdmissionStatus,
    ResourceAdmissionError,
    ResourceKind,
    ResourceRequest,
)
from autplay.runtime.http import ApiError

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_VERSION = {"contract_version", "schema_version"}
_FENCE = {"activation_id", "generation"}
_MAX_INTEGER = 2**53 - 1


def _invalid() -> ResourceAdmissionError:
    return ResourceAdmissionError("resource_request_invalid")


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise _invalid()
    try:
        parsed = UUID(value)
    except ValueError:
        raise _invalid() from None
    if str(parsed) != value:
        raise _invalid()
    return parsed


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        raise _invalid()
    return value


def _envelope(body: dict[str, object], fields: set[str]) -> None:
    if (
        set(body) != _VERSION | fields
        or body.get("contract_version") != "v1"
        or type(body.get("schema_version")) is not int
        or body.get("schema_version") != 1
    ):
        raise _invalid()


def _fence(operation_id: UUID, body: dict[str, object]) -> ActivationFence:
    return ActivationFence(
        operation_id, _uuid(body["activation_id"]), _integer(body["generation"], minimum=1)
    )


def _acquire(body: dict[str, object]) -> ResourceRequest:
    fields = {"operation_id", "kind", "resource_type", "resource_id"}
    if body.get("kind") == "TRANSFER":
        fields.add("target_id")
    _envelope(body, fields)
    if body["kind"] not in ("PLAYBACK", "TRANSFER") or body["resource_type"] not in (
        "PLAY_INSTANCE",
        "DOWNLOAD_INTENT",
        "UPLOAD_INTENT",
    ):
        raise _invalid()
    return ResourceRequest(
        _uuid(body["operation_id"]),
        ResourceKind(str(body["kind"])),
        str(body["resource_type"]),
        _uuid(body["resource_id"]),
        _uuid(body["target_id"]) if "target_id" in body else None,
    )


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def admission_document(status: AdmissionStatus) -> dict[str, object]:
    """Expose only the authenticated operation and aggregate usage, without authority secrets."""
    operation, limits, usage = status.operation, status.limits, status.usage
    request = operation.request
    fence = operation.fence if operation.state != AdmissionState.WAITING else None
    playback = request.kind == ResourceKind.PLAYBACK
    return {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(request.operation_id),
        "kind": request.kind.value,
        "resource_type": request.resource_type,
        "resource_id": str(request.resource_id),
        "target_id": str(request.target_id) if request.target_id else None,
        "operation_sha256": operation.request_sha256.hex(),
        "state": operation.state.value,
        "activation_id": str(fence.activation_id) if fence else None,
        "generation": fence.generation if fence else None,
        "waiting_until": _timestamp(operation.waiting_until),
        "claim_until": _timestamp(operation.claim_until) if fence else None,
        "lease_until": _timestamp(operation.lease_until) if fence else None,
        "attachment_revision": operation.attachment_revision,
        "current_recording_id": str(operation.current_recording_id)
        if operation.current_recording_id
        else None,
        "next_recording_id": str(operation.next_recording_id)
        if operation.next_recording_id
        else None,
        "account_limit": limits.playbacks if playback else limits.transfers,
        "device_limit": 1 if playback else 2,
        "server_limit": status.server_limit,
        "account_usage": usage.account,
        "device_usage": usage.device,
        "server_usage": usage.server,
        "waiting_reason": status.waiting_reason,
        "retry_after_seconds": status.retry_after_seconds
        if operation.state == AdmissionState.WAITING
        else 0,
    }


def resource_admission_error(code: str) -> ApiError:
    status, retry = 403, False
    if code in {"resource_request_invalid", "resource_activation_invalid"}:
        status = 400
    elif code in {
        "resource_operation_conflict",
        "resource_activation_stale",
        "resource_revision_stale",
        "resource_purpose_mismatch",
        "resource_attachment_draining",
        "resource_io_stale",
        "resource_io_busy",
        "resource_execution_stale",
        "resource_execution_busy",
    }:
        status = 409
    elif code == "resource_queue_full":
        status, retry = 429, True
    elif code in {
        "resource_budget_unconfigured",
        "resource_service_unavailable",
        "resource_commit_busy",
        "capability_missing",
    }:
        status, retry = 503, True
    headers = {**_HEADERS, **({"Retry-After": "5"} if retry else {})}
    return ApiError(
        code=code,
        message="Resource admission is unavailable.",
        status_code=status,
        retryable=retry,
        headers=headers,
    )


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result or isinstance(value, dict | list):
            raise _invalid()
        result[key] = value
    return result


def _constant(value: str) -> None:
    del value
    raise _invalid()


def create_resource_admission_router(
    service: ResourceAdmissionService | None,
    *,
    authenticated: Callable[[Request], None],
) -> APIRouter:
    router = APIRouter(prefix="/account/resource-admissions")

    def process(
        action: str, request: Request, identifier: str | None, body: dict[str, object]
    ) -> JSONResponse:
        if service is None:
            raise resource_admission_error("capability_missing")
        try:
            authenticated(request)
            actor = getattr(request.state, "principal", None)
            if not isinstance(actor, Principal):
                raise ResourceAdmissionError()
            if action == "acquire":
                result = service.acquire(actor, _acquire(body))
            else:
                operation_id = _uuid(identifier)
                if action == "poll":
                    result = service.poll(actor, operation_id)
                elif action == "renew":
                    _envelope(body, _FENCE)
                    result = service.renew(actor, _fence(operation_id, body))
                elif action == "release":
                    if "operation_sha256" in body:
                        _envelope(body, {"operation_sha256", "reason"})
                        digest = body["operation_sha256"]
                        if (
                            body["reason"] != "CANCEL"
                            or not isinstance(digest, str)
                            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                        ):
                            raise _invalid()
                        result = service.cancel_waiting(actor, operation_id, bytes.fromhex(digest))
                    else:
                        _envelope(body, _FENCE | {"reason"})
                        if body["reason"] not in ("CANCEL", "COMPLETE"):
                            raise _invalid()
                        result = service.release(actor, _fence(operation_id, body))
                else:
                    _envelope(
                        body,
                        _FENCE
                        | {
                            "expected_attachment_revision",
                            "current_recording_id",
                            "next_recording_id",
                        },
                    )
                    result = service.attach(
                        actor,
                        _fence(operation_id, body),
                        _integer(body["expected_attachment_revision"]),
                        _uuid(body["current_recording_id"]),
                        _uuid(body["next_recording_id"])
                        if body["next_recording_id"] is not None
                        else None,
                    )
            return JSONResponse(admission_document(result), headers=_HEADERS)
        except ResourceAdmissionError as error:
            raise resource_admission_error(error.code) from None
        except SQLAlchemyError:
            raise resource_admission_error("resource_service_unavailable") from None

    async def dispatch(
        action: str, request: Request, identifier: str | None = None
    ) -> JSONResponse:
        if service is None:
            raise resource_admission_error("capability_missing")
        try:
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
                "application/json"
            ):
                raise _invalid()
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > 4096:
                    raise _invalid()
                payload.extend(chunk)
            body = json.loads(
                bytes(payload).decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
            if not isinstance(body, dict):
                raise _invalid()
        except ResourceAdmissionError, ValueError, UnicodeError, RecursionError:
            raise resource_admission_error("resource_request_invalid") from None
        # Authentication and the complete blocking PG transaction stay off the event loop.
        return await run_in_threadpool(process, action, request, identifier, body)

    @router.post("")
    async def acquire(request: Request) -> JSONResponse:
        return await dispatch("acquire", request)

    @router.get("/{operation_id}")
    def poll(operation_id: str, request: Request) -> JSONResponse:
        return process("poll", request, operation_id, {})

    @router.post("/{operation_id}/renew")
    async def renew(operation_id: str, request: Request) -> JSONResponse:
        return await dispatch("renew", request, operation_id)

    @router.post("/{operation_id}/release")
    async def release(operation_id: str, request: Request) -> JSONResponse:
        return await dispatch("release", request, operation_id)

    @router.post("/{operation_id}/attachments")
    async def attach(operation_id: str, request: Request) -> JSONResponse:
        return await dispatch("attach", request, operation_id)

    return router
