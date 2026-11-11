"""Four C1 private read routes with an actual ten-second retained DB owner."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthRepository
from autplay.adapters.postgresql.catalog_view import PostgresCatalogViewRead
from autplay.adapters.postgresql.sync_binding_v2 import BindingDatabaseRuntime, DatabaseDeadline
from autplay.application.auth import AuthService
from autplay.application.catalog_view import CatalogViewService
from autplay.domain.auth import InvalidAccessTokenError
from autplay.domain.catalog_view import CatalogViewError, CatalogViewKind, CatalogViewQuery
from autplay.domain.sync_binding_v2 import SyncBindingV2Error
from autplay.runtime.http import MAX_REQUEST_BODY_FRAMES, error_response
from autplay.runtime.request_context import normalize_or_create_request_id

_PREFIX = "/api/v1/catalog-view"
_PATH = re.compile(
    r"/api/v1/catalog-view/(recordings|releases)/([0-9a-f-]{36})/(detail|releases|tracks)\Z"
)
_KINDS: dict[tuple[str, str], CatalogViewKind] = {
    ("recordings", "detail"): "recording_detail",
    ("recordings", "releases"): "recording_releases",
    ("releases", "detail"): "release_detail",
    ("releases", "tracks"): "release_tracks",
}
_HEADERS = {"Cache-Control": "private, no-store", "Pragma": "no-cache", "Vary": "Authorization"}


def _error(error: CatalogViewError, request_id: str) -> Response:
    headers = dict(_HEADERS)
    if error.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if error.status == 503:
        headers["Retry-After"] = "5"
    return error_response(
        request_id=request_id,
        code=error.code,
        message="The requested catalog view is unavailable.",
        status_code=error.status,
        retryable=error.status == 503,
        headers=headers,
    )


class CatalogViewDatabaseRuntime(BindingDatabaseRuntime):
    """Separate read-only pool; never uses B1/M1 resources or TTL exceptions."""

    def __init__(self, authoritative_url: URL) -> None:
        super().__init__(authoritative_url, read_only=True, application_name="autplay-catalog-view")


class CatalogViewIngressMiddleware:
    """Start before runtime buffering; reject ambiguous raw paths/query/framing."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        root = str(scope.get("root_path", ""))
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        if scope["type"] != "http" or not (path == _PREFIX or path.startswith(_PREFIX + "/")):
            await self._app(scope, receive, send)
            return
        started = monotonic()
        response_started = False
        state = scope.setdefault("state", {})
        request_id = normalize_or_create_request_id(None)
        state["request_id"], state["catalog_view_started"] = request_id, started
        try:
            match = _PATH.fullmatch(path)
            if match is None or (match[1], match[3]) not in _KINDS:
                raise CatalogViewError("not_found", 404)
            if scope.get("method") != "GET" or scope.get("raw_path", b"") != (root + path).encode(
                "ascii"
            ):
                raise CatalogViewError("request_validation_failed", 400)
            raw_query = scope.get("query_string", b"")
            if len(raw_query) > 1024:
                raise CatalogViewError("request_validation_failed", 400)
            # Only the closed ASCII query grammar is accepted. Percent aliases,
            # plus/space decoding and duplicate fields do not acquire authority.
            if any(char not in b"abcdefghijklmnopqrstuvwxyz0123456789_=-&" for char in raw_query):
                raise CatalogViewError("request_validation_failed", 400)
            try:
                pairs = parse_qsl(
                    raw_query.decode("ascii"),
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=7,
                )
            except ValueError:
                raise CatalogViewError("request_validation_failed", 400) from None
            state["catalog_view_query"] = CatalogViewQuery.parse(
                _KINDS[match[1], match[3]], match[2], pairs
            )
            headers = scope.get("headers", [])
            lengths = [value for name, value in headers if name.lower() == b"content-length"]
            if (
                sum(len(name) + len(value) + 4 for name, value in headers) > 16384
                or (lengths and lengths != [b"0"])
                or any(
                    name.lower() in {b"transfer-encoding", b"content-encoding"}
                    for name, _ in headers
                )
            ):
                raise CatalogViewError("request_validation_failed", 400)
            with_body = False
            async with asyncio.timeout(1):
                for _ in range(MAX_REQUEST_BODY_FRAMES):
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request" or message.get("body", b""):
                        raise CatalogViewError("request_validation_failed", 400)
                    if not message.get("more_body", False):
                        break
                else:
                    raise CatalogViewError("request_validation_failed", 400)

            async def empty() -> Message:
                nonlocal with_body
                if with_body:
                    return await receive()
                with_body = True
                return {"type": "http.request", "body": b"", "more_body": False}

            async def admitted_send(message: Message) -> None:
                nonlocal response_started
                if (
                    message["type"] == "http.response.start"
                    and message["status"] == 200
                    and monotonic() - started >= 10
                ):
                    raise CatalogViewError()
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

            async with asyncio.timeout(max(0.001, 10 - (monotonic() - started))):
                await self._app(scope, empty, admitted_send)
        except CatalogViewError as error:
            if response_started:
                raise
            await _error(error, request_id)(scope, receive, send)
        except TimeoutError, ValueError, UnicodeError:
            if response_started:
                raise  # never emit a second response after a stalled send
            await _error(CatalogViewError(), request_id)(scope, receive, send)


def create_catalog_view_router(
    authentication: AuthService,
    database: CatalogViewDatabaseRuntime,
    *,
    configure_sessions: Callable[[sessionmaker[Session]], None],
) -> APIRouter:
    router = APIRouter(prefix="/catalog-view", redirect_slashes=False)

    async def read(request: Request) -> Response:
        request_id = normalize_or_create_request_id(getattr(request.state, "request_id", None))
        try:
            query = getattr(request.state, "catalog_view_query", None)
            started = getattr(request.state, "catalog_view_started", None)
            if type(query) is not CatalogViewQuery or type(started) is not float:
                raise CatalogViewError()
            authorizations = request.headers.getlist("authorization")
            if len(authorizations) != 1:
                raise CatalogViewError("authentication_required", 401)
            scheme, separator, token = authorizations[0].partition(" ")
            if (
                scheme.lower() != "bearer"
                or separator != " "
                or not 1 <= len(token) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in token)
            ):
                raise CatalogViewError("authentication_required", 401)
            # Normal private API codec, WITHOUT authenticate_access's R1 catch.
            authentication.access_tokens.decode(token, now=authentication.clock.now())

            def operation(sessions: sessionmaker[Session], budget: DatabaseDeadline) -> bytes:
                configure_sessions(sessions)

                @contextmanager
                def transaction(credential: str) -> Iterator[PostgresCatalogViewRead]:
                    claims = authentication.access_tokens.decode(
                        credential, now=authentication.clock.now()
                    )
                    with sessions() as session, session.begin():
                        session.execute(
                            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                        )
                        milliseconds = max(1, int(budget.remaining() * 1000))
                        session.execute(
                            text("SET LOCAL transaction_timeout = '" + str(milliseconds) + "ms'")
                        )
                        principal = SqlAlchemyAuthRepository(session).load_active_principal(
                            user_id=claims.user_id,
                            device_id=claims.device_id,
                            session_id=claims.session_id,
                            now=authentication.clock.now(),
                        )
                        if principal is None:
                            raise InvalidAccessTokenError()
                        yield PostgresCatalogViewRead(session, principal, budget.check)
                        authentication.access_tokens.decode(
                            credential, now=authentication.clock.now()
                        )
                        # Reapply time-dependent admission at the final read
                        # in this same snapshot, including session expiry.
                        if (
                            SqlAlchemyAuthRepository(session).load_active_principal(
                                user_id=claims.user_id,
                                device_id=claims.device_id,
                                session_id=claims.session_id,
                                now=authentication.clock.now(),
                            )
                            != principal
                        ):
                            raise InvalidAccessTokenError()

                return CatalogViewService(transaction, budget.check).prepare(token, query)

            remaining = 10 - (monotonic() - started)
            if remaining <= 0:
                raise CatalogViewError()
            body = await database.run(operation, timeout_seconds=remaining)
            authentication.access_tokens.decode(token, now=authentication.clock.now())
            if monotonic() - started >= 10:
                raise CatalogViewError()
            return Response(body, media_type="application/json", headers=_HEADERS)
        except InvalidAccessTokenError:
            return _error(CatalogViewError("authentication_required", 401), request_id)
        except CatalogViewError as error:
            return _error(error, request_id)
        except (
            SQLAlchemyError,
            SyncBindingV2Error,
            TimeoutError,
            RuntimeError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            return _error(CatalogViewError(), request_id)

    router.add_api_route(
        "/recordings/{recording_id}/detail", read, methods=["GET"], response_model=None
    )
    router.add_api_route(
        "/recordings/{recording_id}/releases", read, methods=["GET"], response_model=None
    )
    router.add_api_route(
        "/releases/{release_id}/detail", read, methods=["GET"], response_model=None
    )
    router.add_api_route(
        "/releases/{release_id}/tracks", read, methods=["GET"], response_model=None
    )
    return router
