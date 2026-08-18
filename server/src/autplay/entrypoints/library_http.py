"""Read-only P07 HTTP projections; mutations remain exclusively sync-owned."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import JSONResponse

from autplay.adapters.postgresql.models import (
    LibraryEntryRow,
    ListeningEventRow,
    PlaylistRow,
)
from autplay.domain.auth import Principal
from autplay.runtime.http import ApiError

_MAX_CURSOR_BYTES = 200


class LibraryQueryService(Protocol):
    def query_library(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[object]: ...
    def query_playlists(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[object]: ...
    def query_history(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[object]: ...
    def query_search(self, principal: Principal, query: str, limit: int) -> Sequence[object]: ...


def create_library_router(
    service: LibraryQueryService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    """Return bounded, owner-filtered query routes with safe cursor fields."""
    router = APIRouter(prefix="/library", dependencies=[Depends(authenticated)])

    @router.get("/entries", response_model=None)
    def entries(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=_MAX_CURSOR_BYTES),
    ) -> JSONResponse:
        before, before_id = _decode_cursor(cursor)
        rows = service.query_library(_principal(request), limit + 1, before, before_id)
        return _keyset_page(
            [_entry(cast(LibraryEntryRow, row)) for row in rows],
            limit,
            "added_at",
            "library_entry_id",
        )

    @router.get("/search", response_model=None)
    def search(
        request: Request,
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(50, ge=1, le=100),
    ) -> JSONResponse:
        query = q.strip()
        if not query:
            raise ApiError("search_query_invalid", "The search query is invalid.", 422)
        rows = service.query_search(_principal(request), query, limit)
        return _page([_entry(cast(LibraryEntryRow, row)) for row in rows])

    @router.get("/playlists", response_model=None)
    def playlists(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=_MAX_CURSOR_BYTES),
    ) -> JSONResponse:
        before, before_id = _decode_cursor(cursor)
        rows = service.query_playlists(_principal(request), limit + 1, before, before_id)
        return _keyset_page(
            [_playlist(cast(PlaylistRow, row)) for row in rows],
            limit,
            "updated_at",
            "playlist_id",
        )

    @router.get("/history", response_model=None)
    def history(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=_MAX_CURSOR_BYTES),
    ) -> JSONResponse:
        before, before_id = _decode_cursor(cursor)
        rows = service.query_history(_principal(request), limit + 1, before, before_id)
        return _keyset_page(
            [_history(cast(ListeningEventRow, row)) for row in rows],
            limit,
            "started_at",
            "listening_event_id",
        )

    return router


def _principal(request: Request) -> Principal:
    value = request.state.principal
    if not isinstance(value, Principal):
        raise RuntimeError("authenticated request is missing its principal")
    return value


def _page(items: list[dict[str, object]]) -> JSONResponse:
    return JSONResponse(
        {"items": items, "next_cursor": None}, headers={"Cache-Control": "no-store"}
    )


def _keyset_page(
    items: list[dict[str, object]], limit: int, timestamp_field: str, id_field: str
) -> JSONResponse:
    page = items[:limit]
    next_cursor = None
    if len(items) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(str(last[timestamp_field]), str(last[id_field]))
    return JSONResponse(
        {"items": page, "next_cursor": next_cursor}, headers={"Cache-Control": "no-store"}
    )


def _encode_cursor(timestamp: str, row_id: str) -> str:
    raw = json.dumps({"t": timestamp, "i": row_id}, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        document = json.loads(base64.urlsafe_b64decode(padded.encode()))
        timestamp = datetime.fromisoformat(document["t"])
        row_id = UUID(document["i"])
    except ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError:
        raise ApiError("cursor_invalid", "The cursor is invalid.", 422) from None
    if timestamp.tzinfo is None:
        raise ApiError("cursor_invalid", "The cursor is invalid.", 422)
    return timestamp, row_id


def _entry(row: LibraryEntryRow) -> dict[str, object]:
    return {
        "library_entry_id": str(row.library_entry_id),
        "user_track_ref_id": str(row.user_track_ref_id),
        "source": row.source,
        "availability_status": row.availability_status,
        "added_at": row.added_at.isoformat(),
        "row_version": row.row_version,
    }


def _playlist(row: PlaylistRow) -> dict[str, object]:
    return {
        "playlist_id": str(row.playlist_id),
        "name": row.name,
        "description": row.description,
        "updated_at": row.updated_at.isoformat(),
        "row_version": row.row_version,
    }


def _history(row: ListeningEventRow) -> dict[str, object]:
    return {
        "listening_event_id": str(row.listening_event_id),
        "user_track_ref_id": str(row.user_track_ref_id),
        "recording_id": _optional_uuid(row.recording_id),
        "started_at": row.started_at.isoformat(),
        "played_ms": row.played_ms,
        "event_origin": row.event_origin,
    }


def _optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


__all__ = ("LibraryQueryService", "create_library_router")
