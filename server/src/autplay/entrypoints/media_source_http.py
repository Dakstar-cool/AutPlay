"""M1 exact read-only route; raw ingress must wrap runtime body buffering.

Integration is intentionally explicit: mount create_media_source_router under
/api/v1 and install MediaSourceIngressMiddleware outside RequestRuntimeMiddleware.
No resource admission, settings reload, identity initializer or stream resolver.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Final

from anyio import CancelScope, fail_after, to_thread
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autplay.adapters.postgresql.media_source_runtime import MediaSourceDatabaseRuntime
from autplay.adapters.postgresql.sync_binding_v2 import DatabaseDeadline
from autplay.application.media_source import MediaSourceService, PreparedMediaSourceReply, elapsed
from autplay.domain.auth import AccessTokenClaims, InvalidAccessTokenError, Principal
from autplay.domain.media_source import (
    MAX_RESPONSE_BYTES,
    MediaSourceError,
    MediaSourceSnapshotRequestV1,
)
from autplay.domain.sync_binding_v2 import SyncBindingV2Error
from autplay.ports.media_source import (
    CurrentMediaSourcePolicy,
    ElapsedClock,
    ElapsedMark,
    MediaSourceAuthenticator,
    MediaSourcePolicy,
)
from autplay.runtime.http import MAX_REQUEST_BODY_FRAMES, error_response
from autplay.runtime.request_context import normalize_or_create_request_id

PREFIX: Final = "/api/v1/media-source"
_HEADERS: Final = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Authorization",
}
_MESSAGES: Final = {
    "request_validation_failed": "The request is invalid.",
    "authentication_required": "Authentication is required.",
    "session_revoked": "The session is no longer active.",
    "not_found": "The requested resource was not found.",
    "media_source_unavailable": "The media source is temporarily unavailable.",
}


def _error(error: MediaSourceError, request_id: str) -> Response:
    headers = dict(_HEADERS)
    if error.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if error.status_code == 503:
        headers["Retry-After"] = "5"
    response = error_response(
        request_id=request_id,
        code=error.code,
        message=_MESSAGES[error.code],
        status_code=error.status_code,
        retryable=error.status_code == 503,
        headers=headers,
    )
    # IDs are normalized UUIDs and all other fields above are closed literals.
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("media source error envelope bound")
    return response


class MediaSourceIngressMiddleware:
    """Reject forbidden raw framing before buffering or authentication.

    The total timer begins here. Worker timeout returns safe 503; an abandoned
    read worker retains its finally/connection ownership and cannot send 200.
    """

    def __init__(self, app: ASGIApp, *, clock: ElapsedClock) -> None:
        self._app, self._clock = app, clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        root = str(scope.get("root_path", ""))
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        if scope["type"] != "http" or not (path == PREFIX or path.startswith(PREFIX + "/")):
            await self._app(scope, receive, send)
            return
        started = self._clock.mark()
        state = scope.setdefault("state", {})
        state["media_source_started"] = started
        request_id = normalize_or_create_request_id(None)
        state["request_id"] = request_id
        try:
            prefix, suffix = PREFIX + "/user-tracks/", "/snapshot"
            if not path.startswith(prefix) or not path.endswith(suffix):
                raise MediaSourceError("not_found")
            target = path[len(prefix) : -len(suffix)]
            MediaSourceSnapshotRequestV1(target)
            if (
                scope.get("raw_path", (root + path).encode("ascii"))
                != (root + path).encode("ascii")
                or scope.get("method") != "GET"
                or scope.get("query_string", b"")
            ):
                raise MediaSourceError("request_validation_failed")
            headers = scope.get("headers", [])
            if sum(len(name) + len(value) + 4 for name, value in headers) > 16_384:
                raise MediaSourceError("request_validation_failed")
            lengths = [value for name, value in headers if name.lower() == b"content-length"]
            if (lengths and lengths != [b"0"]) or any(
                name.lower() in {b"content-encoding", b"transfer-encoding"} for name, _ in headers
            ):
                raise MediaSourceError("request_validation_failed")
            with fail_after(1):
                for _ in range(MAX_REQUEST_BODY_FRAMES):
                    frame = await receive()
                    if frame["type"] == "http.disconnect":
                        return
                    if frame["type"] != "http.request" or frame.get("body", b""):
                        raise MediaSourceError("request_validation_failed")
                    if not frame.get("more_body", False):
                        break
                else:
                    raise MediaSourceError("request_validation_failed")
            elapsed(started, self._clock.mark())
        except (MediaSourceError, TimeoutError, UnicodeError) as error:
            failure = error if isinstance(error, MediaSourceError) else MediaSourceError()
            await _error(failure, request_id)(scope, receive, send)
            return
        consumed = False

        async def empty_body() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, empty_body, send)


class _FinalResponse(Response):
    def __init__(self, prepared: PreparedMediaSourceReply, request_id: str) -> None:
        super().__init__(prepared.body, media_type="application/json", headers=_HEADERS)
        self._prepared, self._request_id = prepared, request_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            self._prepared.admit()  # fresh final mark; no blocking work follows
        except MediaSourceError as error:
            await _error(error, self._request_id)(scope, receive, send)
            return
        except RuntimeError, TypeError, ValueError, OverflowError:
            await _error(MediaSourceError(), self._request_id)(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


async def _prepare_until_disconnect(
    request: Request,
    database: MediaSourceDatabaseRuntime,
    operation: Callable[[sessionmaker[Session], DatabaseDeadline], PreparedMediaSourceReply],
    *,
    remaining: float,
) -> PreparedMediaSourceReply:
    async def disconnected() -> None:
        # Runtime buffering may still hold one validated empty frame. Consume
        # it, then wait for disconnect without polling or another work queue.
        for _ in range(MAX_REQUEST_BODY_FRAMES):
            frame = await request.receive()
            if frame["type"] == "http.disconnect":
                return
            if (
                frame["type"] != "http.request"
                or frame.get("body", b"")
                or frame.get("more_body", False)
            ):
                raise MediaSourceError("request_validation_failed")
        raise MediaSourceError("request_validation_failed")

    work = asyncio.create_task(database.run(operation, timeout_seconds=remaining))
    watch = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            watch.result()
            raise MediaSourceError()
        return work.result()
    finally:
        if not work.done():
            # Cancels only run's observer. The runtime stops that budget and
            # retains its actual DB/DNS task, connection and slot until settled.
            work.cancel()
        watch.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(work, watch, return_exceptions=True)


def create_media_source_router(
    service: MediaSourceService | None,
    *,
    authenticate: MediaSourceAuthenticator | None,
    clock: ElapsedClock,
    database: MediaSourceDatabaseRuntime | None = None,
    authenticate_sessions: Callable[[sessionmaker[Session]], MediaSourceAuthenticator]
    | None = None,
    preflight_access: Callable[[str], None] | None = None,
    current_policy: CurrentMediaSourcePolicy | None = None,
    isolated_fixture: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/media-source", redirect_slashes=False)

    @router.get("/user-tracks/{user_track_ref_id}/snapshot", response_model=None)
    async def snapshot(user_track_ref_id: str, request: Request) -> Response:
        request_id = normalize_or_create_request_id(getattr(request.state, "request_id", None))
        try:
            parsed = MediaSourceSnapshotRequestV1(user_track_ref_id)
            started = getattr(request.state, "media_source_started", None)
            # Omitted raw middleware is an integration failure, never a relaxed
            # alternate ingress which silently hides body framing/path decoding.
            if not isinstance(started, ElapsedMark):
                raise MediaSourceError()
            authorizations = request.headers.getlist("authorization")
            if len(authorizations) != 1:
                raise MediaSourceError("authentication_required")
            scheme, separator, token = authorizations[0].partition(" ")
            if (
                scheme.lower() != "bearer"
                or separator != " "
                or not 1 <= len(token) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in token)
            ):
                raise MediaSourceError("authentication_required")
            if preflight_access is not None:
                # Pure crypto/metadata rejection only. A portable TTL candidate
                # is still unauthenticated and MUST enter this owner's UoW.
                preflight_access(token)
            if service is None:
                raise MediaSourceError()
            if current_policy is not None:
                configured = current_policy()
                if (
                    type(configured) is not MediaSourcePolicy
                    or configured.media_source_snapshot_v1_enabled is not True
                    or configured.identity is None
                    or type(configured.m5_capability_revision) is not int
                ):
                    raise MediaSourceError()

            def prepare(
                current_authenticate: MediaSourceAuthenticator | None,
            ) -> PreparedMediaSourceReply:
                if current_authenticate is None:
                    raise MediaSourceError()
                authentication_policy = current_policy() if current_policy is not None else None
                try:
                    principal, claims = current_authenticate(token)
                except InvalidAccessTokenError:
                    if current_policy is not None and current_policy() is not authentication_policy:
                        raise MediaSourceError() from None
                    raise
                if type(principal) is not Principal or type(claims) is not AccessTokenClaims:
                    raise MediaSourceError("authentication_required")
                prepared = service.prepare(principal, claims, parsed, started=started)
                # Pin the committed M5 configuration through R1's public
                # identity callback, auth UoW and relationship observation.
                # These are pure reference checks after the final DB clock.
                if current_policy is not None and (
                    prepared.policy is not authentication_policy
                    or current_policy() is not authentication_policy
                ):
                    raise MediaSourceError()
                return prepared

            def prepare_owned(
                sessions: sessionmaker[Session], budget: DatabaseDeadline
            ) -> PreparedMediaSourceReply:
                if authenticate_sessions is None:
                    raise MediaSourceError()
                try:
                    return prepare(authenticate_sessions(sessions))
                except InvalidAccessTokenError:
                    if budget.database_failed:
                        raise MediaSourceError() from None
                    raise

            remaining = 15 - elapsed(started, clock.mark()) / 1_000_000_000
            if remaining <= 0:
                raise MediaSourceError()
            with fail_after(remaining):
                if database is not None:
                    if authenticate_sessions is None:
                        raise MediaSourceError()
                    # The ACTUAL authenticator, including R1's lookup, receives
                    # this owner's sessions. Never fall back to shared auth/UoW
                    # when this pool is full, closed, missing or timing out.
                    prepared = await _prepare_until_disconnect(
                        request, database, prepare_owned, remaining=remaining
                    )
                elif isolated_fixture:
                    # Only explicitly isolated fake reader/auth fixtures use
                    # this seam. create_app never enables it for real services.
                    prepared = await to_thread.run_sync(
                        prepare, authenticate, abandon_on_cancel=True
                    )
                else:
                    raise MediaSourceError()
            return _FinalResponse(prepared, request_id)
        except InvalidAccessTokenError:
            return _error(MediaSourceError("authentication_required"), request_id)
        except MediaSourceError as error:
            return _error(error, request_id)
        except (
            SQLAlchemyError,
            SyncBindingV2Error,
            TimeoutError,
            RuntimeError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            return _error(MediaSourceError(), request_id)

    return router
