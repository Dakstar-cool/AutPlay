"""Owner-scoped resumable Vault upload HTTP contract."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response

from autplay.domain.auth import OwnedObjectNotFoundError, Principal
from autplay.domain.vault import ChunkIntegrityError, UploadLimitError, UploadOffsetError
from autplay.runtime.http import ApiError

_MAX_OBJECT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_CHUNK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class CreateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: UUID
    expected_size: int = Field(ge=1, le=_MAX_OBJECT_BYTES)
    declared_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class UploadView:
    """Safe resumable-session projection returned by the application layer."""

    upload_id: UUID
    offset: int
    expected_size: int
    state: str


class UploadService(Protocol):
    """Application ownership boundary for resumable upload state."""

    def create(
        self,
        principal: Principal,
        *,
        recording_id: UUID,
        expected_size: int,
        declared_sha256: str | None,
        idempotency_key: str,
    ) -> tuple[UploadView, bool]: ...

    def status(self, principal: Principal, upload_id: UUID) -> UploadView: ...

    def append(
        self,
        principal: Principal,
        upload_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        payload: bytes,
        payload_sha256: str,
    ) -> int: ...

    def complete(self, principal: Principal, upload_id: UUID) -> UploadView: ...

    def cancel(self, principal: Principal, upload_id: UUID) -> None: ...


def create_vault_router(
    service: UploadService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    """Build bounded, no-store upload routes around one application service."""

    router = APIRouter(prefix="/vault", dependencies=[Depends(authenticated)])

    @router.post("/uploads", response_model=None)
    def create_upload(body: CreateUploadRequest, request: Request) -> JSONResponse:
        principal = _principal(request)
        key = _single_header(request, "idempotency-key", max_length=200)
        try:
            view, created = service.create(
                principal,
                recording_id=body.recording_id,
                expected_size=body.expected_size,
                declared_sha256=body.declared_sha256,
                idempotency_key=key,
            )
        except Exception as error:
            _raise_upload_error(error)
        return JSONResponse(
            _view_document(view), status_code=201 if created else 200, headers=_no_store()
        )

    @router.head("/uploads/{upload_id}", response_model=None)
    def head_upload(upload_id: UUID, request: Request) -> Response:
        try:
            view = service.status(_principal(request), upload_id)
        except Exception as error:
            _raise_upload_error(error)
        return Response(status_code=204, headers={**_no_store(), **_offset_headers(view)})

    @router.patch("/uploads/{upload_id}", status_code=204, response_model=None)
    async def append_upload(upload_id: UUID, request: Request) -> Response:
        _require_content_type(request, "application/offset+octet-stream")
        content_length = _content_length(request)
        if content_length > _MAX_CHUNK_BYTES:
            raise _limit_error()
        offset = _nonnegative_header(request, "upload-offset")
        chunk_index = _nonnegative_header(request, "upload-chunk-index")
        chunk_hash = _single_header(request, "x-chunk-sha256", max_length=64)
        if _SHA256.fullmatch(chunk_hash) is None:
            raise _chunk_error()
        payload = await request.body()
        if len(payload) != content_length:
            raise _chunk_error()
        if hashlib.sha256(payload).hexdigest() != chunk_hash:
            raise _chunk_error()
        try:
            next_offset = service.append(
                _principal(request),
                upload_id,
                offset=offset,
                chunk_index=chunk_index,
                payload=payload,
                payload_sha256=chunk_hash,
            )
        except Exception as error:
            _raise_upload_error(error)
        return Response(status_code=204, headers={**_no_store(), "Upload-Offset": str(next_offset)})

    @router.post("/uploads/{upload_id}/complete", status_code=202, response_model=None)
    def complete_upload(upload_id: UUID, request: Request) -> JSONResponse:
        try:
            view = service.complete(_principal(request), upload_id)
        except Exception as error:
            _raise_upload_error(error)
        return JSONResponse(_view_document(view), status_code=202, headers=_no_store())

    @router.get("/uploads/{upload_id}", response_model=None)
    def get_upload(upload_id: UUID, request: Request) -> JSONResponse:
        try:
            view = service.status(_principal(request), upload_id)
        except Exception as error:
            _raise_upload_error(error)
        return JSONResponse(_view_document(view), headers=_no_store())

    @router.delete("/uploads/{upload_id}", status_code=204, response_model=None)
    def cancel_upload(upload_id: UUID, request: Request) -> Response:
        try:
            service.cancel(_principal(request), upload_id)
        except Exception as error:
            _raise_upload_error(error)
        return Response(status_code=204, headers=_no_store())

    return router


def _view_document(view: UploadView) -> dict[str, object]:
    return {
        "upload_id": str(view.upload_id),
        "offset": view.offset,
        "expected_size": view.expected_size,
        "state": view.state,
    }


def _offset_headers(view: UploadView) -> dict[str, str]:
    return {"Upload-Offset": str(view.offset), "Upload-Length": str(view.expected_size)}


def _principal(request: Request) -> Principal:
    principal = request.state.principal
    if not isinstance(principal, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return principal


def _single_header(request: Request, name: str, *, max_length: int) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1 or not values[0] or len(values[0]) > max_length:
        raise ApiError("request_validation_failed", "The request is invalid.", 422)
    return values[0]


def _nonnegative_header(request: Request, name: str) -> int:
    value = _single_header(request, name, max_length=20)
    try:
        number = int(value)
    except ValueError:
        raise ApiError("request_validation_failed", "The request is invalid.", 422) from None
    if number < 0:
        raise ApiError("request_validation_failed", "The request is invalid.", 422)
    return number


def _content_length(request: Request) -> int:
    return _nonnegative_header(request, "content-length")


def _require_content_type(request: Request, expected: str) -> None:
    if request.headers.get("content-type") != expected:
        raise ApiError("request_validation_failed", "The request is invalid.", 422)


def _raise_upload_error(error: Exception) -> None:
    if (
        isinstance(error, UploadOffsetError)
        or getattr(error, "code", None) == "upload_offset_mismatch"
    ):
        raise ApiError(
            "upload_offset_mismatch", "The upload offset does not match.", 409
        ) from error
    if isinstance(error, (ChunkIntegrityError,)) or getattr(error, "code", None) in {
        "upload_chunk_hash_mismatch",
        "upload_chunk_length_mismatch",
    }:
        raise _chunk_error() from error
    if (
        isinstance(error, UploadLimitError)
        or getattr(error, "code", None) == "upload_limit_exceeded"
    ):
        raise _limit_error() from error
    if isinstance(error, OwnedObjectNotFoundError) or getattr(error, "code", None) in {
        "vault_resource_not_found",
        "owned_object_not_found",
    }:
        raise ApiError("not_found", "The requested resource was not found.", 404) from error
    if getattr(error, "code", None) == "idempotency_key_conflict":
        raise ApiError("idempotency_key_conflict", "The idempotency key conflicts.", 409) from error
    if getattr(error, "code", None) == "upload_idempotency_conflict":
        raise ApiError(
            "upload_idempotency_conflict", "The idempotency key conflicts.", 409
        ) from error
    if getattr(error, "code", None) == "upload_invalid_state":
        raise ApiError(
            "upload_invalid_state", "The upload state does not allow this action.", 409
        ) from error
    if getattr(error, "code", None) == "vault_capacity_low":
        raise ApiError(
            "vault_capacity_low",
            "The Vault does not have enough free capacity.",
            507,
            retryable=True,
        ) from error
    if getattr(error, "code", None) == "vault_storage_unavailable":
        raise ApiError(
            "vault_storage_unavailable",
            "Vault storage is temporarily unavailable.",
            503,
            retryable=True,
        ) from error
    if getattr(error, "code", None) == "vault_storage_unsafe":
        raise ApiError(
            "vault_storage_unsafe",
            "Vault storage failed a safety check.",
            500,
        ) from error
    raise error


def _chunk_error() -> ApiError:
    return ApiError("upload_chunk_invalid", "The upload chunk is invalid.", 422)


def _limit_error() -> ApiError:
    return ApiError("upload_limit_exceeded", "The upload exceeds a limit.", 422)


def _no_store() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


__all__ = ("CreateUploadRequest", "UploadService", "UploadView", "create_vault_router")
