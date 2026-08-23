"""Authenticated bounded HTTP surface for P10 imports and manual review."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from autplay.adapters.postgresql.import_runtime import (
    ImportJobReport,
    ImportNotFoundError,
    ImportReviewConflictError,
    ImportReviewResult,
    ImportStartResult,
    ImportStateConflictError,
)
from autplay.domain.auth import Principal
from autplay.domain.import_identity import MAX_IMPORT_BYTES, ImportEnvelopeError
from autplay.domain.jobs import CancelRequestResult
from autplay.ports.jobs import JobIdempotencyConflict
from autplay.runtime.http import ApiError

_NO_STORE = {"Cache-Control": "no-store"}


class ImportHttpService(Protocol):
    """Narrow application boundary consumed by the HTTP router."""

    def start(
        self,
        principal: Principal,
        *,
        payload: bytes,
        format_name: str,
        schema_version: str,
        mode: str,
    ) -> ImportStartResult: ...

    def report(
        self,
        principal: Principal,
        import_job_id: UUID,
        *,
        limit: int = 200,
        after: str | None = None,
    ) -> ImportJobReport: ...

    def cancel(self, principal: Principal, import_job_id: UUID) -> CancelRequestResult: ...

    def resume(self, principal: Principal, import_job_id: UUID) -> ImportStartResult: ...

    def review(
        self,
        principal: Principal,
        import_job_id: UUID,
        import_entry_id: UUID,
        *,
        predecessor_decision_id: UUID,
        action: str,
        selected_rank: int | None,
        idempotency_key: str,
    ) -> ImportReviewResult: ...


class ReviewRequest(BaseModel):
    """One explicit manual review command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    predecessor_decision_id: UUID
    action: str = Field(pattern="^(ACCEPT|REJECT|KEEP_UNRESOLVED|CREATE_RECORDING)$")
    selected_rank: int | None = Field(default=None, ge=1, le=100)
    idempotency_key: str = Field(min_length=1, max_length=120)


def create_import_router(
    service: ImportHttpService,
    *,
    authenticated: Callable[[Request], None],
) -> APIRouter:
    """Build owner-scoped import routes with bounded raw request bodies."""

    router = APIRouter(prefix="/imports", dependencies=[Depends(authenticated)])

    @router.post("", response_model=None, status_code=202)
    async def start_import(
        request: Request,
        format_name: str = Query(alias="format", pattern="^(CSV|JSON|HTML|TXT)$"),
        schema_version: str = Query(default="1", min_length=1, max_length=20),
        mode: str = Query(default="LIBRARY_ONLY", pattern="^(LIBRARY_ONLY|MATERIALIZE)$"),
    ) -> JSONResponse:
        length = request.headers.get("content-length")
        if length is not None:
            try:
                declared_length = int(length)
            except ValueError:
                declared_length = MAX_IMPORT_BYTES + 1
            if not 1 <= declared_length <= MAX_IMPORT_BYTES:
                raise ApiError("import.input_size_invalid", "The import body is invalid.", 413)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_IMPORT_BYTES:
                raise ApiError("import.input_size_invalid", "The import body is invalid.", 413)
            body.extend(chunk)
        payload = bytes(body)
        if not payload:
            raise ApiError("import.input_size_invalid", "The import body is invalid.", 413)
        try:
            result = service.start(
                _principal(request),
                payload=payload,
                format_name=format_name,
                schema_version=schema_version,
                mode=mode,
            )
        except ImportEnvelopeError as error:
            raise ApiError(str(error), "The import envelope is invalid.", 422) from error
        except JobIdempotencyConflict as error:
            raise ApiError("import.idempotency_conflict", "The import conflicts.", 409) from error
        return JSONResponse(_start_result(result), status_code=202, headers=_NO_STORE)

    @router.get("/{import_job_id}", response_model=None)
    def report(
        import_job_id: UUID,
        request: Request,
        limit: int = Query(default=200, ge=1, le=1_000),
        after: str | None = Query(default=None, min_length=1, max_length=1_000),
    ) -> JSONResponse:
        try:
            result = service.report(_principal(request), import_job_id, limit=limit, after=after)
        except ImportNotFoundError as error:
            raise ApiError("not_found", "The requested resource was not found.", 404) from error
        return JSONResponse(_report(result), headers=_NO_STORE)

    @router.post("/{import_job_id}/cancel", response_model=None)
    def cancel(import_job_id: UUID, request: Request) -> JSONResponse:
        result = service.cancel(_principal(request), import_job_id)
        if result is CancelRequestResult.NOT_FOUND:
            raise ApiError("not_found", "The requested resource was not found.", 404)
        return JSONResponse({"result": result.value}, headers=_NO_STORE)

    @router.post("/{import_job_id}/resume", response_model=None, status_code=202)
    def resume(import_job_id: UUID, request: Request) -> JSONResponse:
        try:
            result = service.resume(_principal(request), import_job_id)
        except ImportNotFoundError as error:
            raise ApiError("not_found", "The requested resource was not found.", 404) from error
        except ImportStateConflictError as error:
            raise ApiError("import.state_conflict", "The import cannot be resumed.", 409) from error
        return JSONResponse(_start_result(result), status_code=202, headers=_NO_STORE)

    @router.post("/{import_job_id}/entries/{import_entry_id}/review", response_model=None)
    def review(
        import_job_id: UUID,
        import_entry_id: UUID,
        body: ReviewRequest,
        request: Request,
    ) -> JSONResponse:
        try:
            result = service.review(
                _principal(request),
                import_job_id,
                import_entry_id,
                predecessor_decision_id=body.predecessor_decision_id,
                action=body.action,
                selected_rank=body.selected_rank,
                idempotency_key=body.idempotency_key,
            )
        except ImportNotFoundError as error:
            raise ApiError("not_found", "The requested resource was not found.", 404) from error
        except ImportReviewConflictError as error:
            raise ApiError("import.review_conflict", "The review conflicts.", 409) from error
        return JSONResponse(
            {
                "decision_id": str(result.decision_id),
                "import_entry_id": str(result.import_entry_id),
                "status": result.status,
                "recording_id": (
                    str(result.recording_id) if result.recording_id is not None else None
                ),
                "replayed": result.replayed,
            },
            headers=_NO_STORE,
        )

    return router


def _start_result(result: ImportStartResult) -> dict[str, object]:
    return {
        "import_job_id": str(result.import_job_id),
        "delivery_job_id": str(result.delivery_job_id),
        "replayed": result.replayed,
    }


def _report(result: ImportJobReport) -> dict[str, object]:
    return {
        "import_job_id": str(result.import_job_id),
        "delivery_job_id": str(result.delivery_job_id),
        "state": result.state,
        "progress_current": result.progress_current,
        "progress_total": result.progress_total,
        "adapter_id": result.adapter_id,
        "adapter_version": result.adapter_version,
        "input_schema_version": result.input_schema_version,
        "counts": result.counts,
        "entries": [
            {
                "source_row_key": entry.source_row_key,
                "import_entry_id": str(entry.import_entry_id),
                "status": entry.status,
                "resolver_state": entry.resolver_state,
                "decision_id": str(entry.decision_id) if entry.decision_id else None,
                "candidate_count": entry.candidate_count,
                "unknown_field_count": entry.unknown_field_count,
                "error_code": entry.error_code,
            }
            for entry in result.entries
        ],
        "next_after": result.next_after,
    }


def _principal(request: Request) -> Principal:
    value = request.state.principal
    if not isinstance(value, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return value


__all__ = (
    "ImportHttpService",
    "ReviewRequest",
    "create_import_router",
)
