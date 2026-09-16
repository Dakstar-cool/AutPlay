"""Authorized direct HTTP streaming routes for immutable Vault bytes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse

from autplay.application.vault_streaming import AuthorizedStream
from autplay.domain.auth import OwnedObjectNotFoundError, Principal
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import ByteRange, VaultError
from autplay.entrypoints.resource_admission_http import resource_admission_error
from autplay.entrypoints.resource_io_http import IoScopedResponse, require_resource_io_headers
from autplay.entrypoints.resource_stream_http import ProcessStreamGateway
from autplay.entrypoints.stream import select_range
from autplay.ports.vault import RangeReader, VaultStorage
from autplay.runtime.http import ApiError


class StreamLookup(Protocol):
    """Application boundary that authorizes an audio variant before I/O."""

    def resolve(self, principal: Principal, audio_variant_id: UUID) -> AuthorizedStream:
        """Return an authorized immutable representation or a masked miss."""


def create_stream_router(
    lookup: StreamLookup,
    storage: VaultStorage | None,
    *,
    authenticated: Callable[[Request], None],
    process_gateway: ProcessStreamGateway | None = None,
) -> APIRouter:
    """Build streaming routes; parsing never occurs before authorization."""

    router = APIRouter(dependencies=[Depends(authenticated)])

    @router.api_route("/stream/audio-variants/{audio_variant_id}", methods=["GET", "HEAD"])
    async def stream_audio_variant(audio_variant_id: UUID, request: Request) -> Response:
        principal = _principal(request)
        try:
            authorized = await run_in_threadpool(lookup.resolve, principal, audio_variant_id)
        except OwnedObjectNotFoundError as error:
            raise _not_found() from error
        except Exception as error:
            if getattr(error, "code", None) == "vault_resource_not_found":
                raise _not_found() from error
            raise
        etag = f'"sha256-{authorized.sha256.hex}"'
        try:
            selected = select_range(
                range_header=request.headers.get("range"),
                if_range=request.headers.get("if-range"),
                etag=etag,
                size=authorized.byte_size,
            )
        except ValueError as error:
            raise ApiError(
                code="range_not_satisfiable",
                message="The requested byte range is not satisfiable.",
                status_code=416,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes */{authorized.byte_size}",
                    "Cache-Control": "private, no-store",
                },
            ) from error
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Length": str(selected.length),
            "ETag": etag,
        }
        status_code = 206 if selected.partial else 200
        if selected.partial:
            headers["Content-Range"] = (
                f"bytes {selected.start}-{selected.end}/{authorized.byte_size}"
            )
        if request.method == "HEAD":
            return Response(
                status_code=status_code,
                headers=headers,
                media_type=authorized.media_type,
            )
        try:
            if process_gateway is not None:
                resource_headers = require_resource_io_headers(
                    request, allowed=frozenset({"PLAY_INSTANCE", "DOWNLOAD_INTENT"})
                )
                body = await process_gateway.open(
                    principal,
                    resource_headers,
                    audio_variant_id,
                    authorized,
                    ByteRange(start=selected.start, end=selected.end),
                )
                return IoScopedResponse(
                    StreamingResponse(
                        body,
                        status_code=status_code,
                        headers=headers,
                        media_type=authorized.media_type,
                    ),
                    body.io,
                )
            if storage is None:
                raise RuntimeError("stream storage dependency is missing")
            reader = storage.open_range(
                authorized.storage_key,
                ByteRange(start=selected.start, end=selected.end),
                expected_size=authorized.byte_size,
                verified_at=authorized.verified_at,
            )
        except ResourceAdmissionError as error:
            raise resource_admission_error(error.code) from None
        except SQLAlchemyError:
            raise resource_admission_error("resource_service_unavailable") from None
        except VaultError as error:
            raise ApiError(
                code="vault_stream_unavailable",
                message="The requested audio representation is temporarily unavailable.",
                status_code=503,
            ) from error
        return StreamingResponse(
            _stream(reader, request),
            status_code=status_code,
            headers=headers,
            media_type=authorized.media_type,
        )

    return router


async def _stream(reader: RangeReader, request: Request) -> AsyncIterator[bytes]:
    try:
        for payload in reader:
            if await request.is_disconnected():
                break
            yield payload
    finally:
        reader.close()


def _principal(request: Request) -> Principal:
    principal = request.state.principal
    if not isinstance(principal, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return principal


def _not_found() -> ApiError:
    return ApiError(
        code="not_found",
        message="The requested resource was not found.",
        status_code=404,
    )


__all__ = ("AuthorizedStream", "StreamLookup", "create_stream_router")
