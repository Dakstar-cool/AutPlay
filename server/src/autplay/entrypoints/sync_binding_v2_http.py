"""Exactly three closed B1 operations and raw ingress precedence."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autplay.adapters.postgresql.sync_binding_v2 import BindingDatabaseRuntime
from autplay.application.sync_binding_v2 import SyncBindingV2Service
from autplay.domain.auth import (
    AccessAuthenticationUnavailableError,
    AccessTokenClaims,
    InvalidAccessTokenError,
    Principal,
)
from autplay.domain.canonical_json import canonical_json_bytes
from autplay.domain.sync_binding_v2 import MAX_BODY_BYTES, SyncBindingV2Error, parse_binding_request
from autplay.ports.sync_binding_v2 import BindingAccessAuthenticator
from autplay.runtime.http import MAX_REQUEST_BODY_FRAMES, ApiError, error_response
from autplay.runtime.request_context import normalize_or_create_request_id

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PREFIX: Final = "/api/v1/sync-binding/v2"
_METHODS: Final = {"/discovery": "GET", "/capabilities": "GET", "/bind": "POST"}


class SyncBindingV2IngressMiddleware:
    """Installed outside runtime buffering, so wire frames cannot be hidden.

    The registry is closed before body admission. Trailing slashes and unknown
    operations get 404 instead of the framework's automatic 307 redirect.
    Existing runtime admission still runs for every admitted request.
    """

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
        status, code, message, headers = 0, "", "", {}
        expected = _METHODS.get(path[len(_PREFIX) :])
        if expected is None:
            status, code, message = 404, "not_found", "The requested resource was not found."
        elif scope.get("method") != expected:
            status, code, message = 405, "method_not_allowed", "The request method is not allowed."
            headers = {"Allow": expected}
        body, frames = bytearray(), 0
        lengths = [
            value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
        ]
        if not status:
            try:
                if lengths and (
                    len(lengths) != 1
                    or not lengths[0].isdigit()
                    or int(lengths[0]) > MAX_BODY_BYTES
                ):
                    raise ValueError("bound")
                while True:
                    frame = await receive()
                    if frame["type"] == "http.disconnect":
                        return
                    frames += 1
                    if frame["type"] != "http.request" or frames > MAX_REQUEST_BODY_FRAMES:
                        raise ValueError("frame")
                    body.extend(frame.get("body", b""))
                    if len(body) > MAX_BODY_BYTES:
                        raise ValueError("bound")
                    if not frame.get("more_body", False):
                        break
            except ValueError:
                status, code, message = (
                    413,
                    "request_body_too_large",
                    "The sync binding request could not be applied.",
                )
        if status:
            ids = [
                value for name, value in scope.get("headers", []) if name.lower() == b"x-request-id"
            ]
            try:
                incoming = ids[0].decode("ascii") if len(ids) == 1 else None
            except UnicodeDecodeError:
                incoming = None
            request_id = normalize_or_create_request_id(incoming)
            response = error_response(
                request_id=request_id,
                code=code,
                message=message,
                status_code=status,
                retryable=False,
                headers={**headers, "X-Request-ID": request_id},
            )
            await response(scope, receive, send)
            return
        consumed = False

        async def buffered() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, buffered, send)


def create_sync_binding_v2_router(
    service: SyncBindingV2Service | None,
    *,
    authenticate: BindingAccessAuthenticator | None,
    database: BindingDatabaseRuntime | None = None,
    authenticate_sessions: Callable[[sessionmaker[Session]], BindingAccessAuthenticator]
    | None = None,
    preflight_access: Callable[[str], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/sync-binding/v2", redirect_slashes=False)

    def shape(kind: str, request: Request, raw: bytes) -> str | None:
        types = request.headers.getlist("content-type")
        if request.headers.getlist("content-encoding") or (
            kind == "bind"
            and (
                len(types) != 1
                or re.fullmatch(
                    r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?', types[0], re.I
                )
                is None
            )
        ):
            raise SyncBindingV2Error("sync_binding_media_type_invalid", 415)
        if request.scope.get("query_string", b"") or (kind != "bind" and raw):
            raise SyncBindingV2Error()
        if kind == "discovery":
            return None
        authorizations = request.headers.getlist("authorization")
        if len(authorizations) != 1:
            raise SyncBindingV2Error("authentication_required", 401)
        scheme, separator, bearer = authorizations[0].partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not bearer
            or any(char.isspace() for char in bearer)
        ):
            raise SyncBindingV2Error("authentication_required", 401)
        return bearer

    def invoke(
        kind: str, request: Request, raw: bytes, sessions: sessionmaker[Session] | None = None
    ) -> JSONResponse:
        try:
            bearer = shape(kind, request, raw)
            current_service = (
                service.using_sessions(sessions)
                if service is not None and sessions is not None
                else service
            )
            current_authenticate = (
                authenticate_sessions(sessions)
                if sessions is not None and authenticate_sessions is not None
                else authenticate
            )
            principal: Principal | None = None
            claims: AccessTokenClaims | None = None
            if kind != "discovery":
                if bearer is None:
                    raise SyncBindingV2Error("authentication_required", 401)
                if current_authenticate is None:
                    raise SyncBindingV2Error("sync_binding_unavailable", 503)
                principal, claims = current_authenticate(bearer)
                if type(principal) is not Principal or type(claims) is not AccessTokenClaims:
                    raise SyncBindingV2Error("authentication_required", 401)
            parsed = parse_binding_request(raw) if kind == "bind" else None
            if current_service is None:
                raise SyncBindingV2Error("sync_binding_unavailable", 503)
            if kind == "discovery":
                result = current_service.discovery()
            elif kind == "capabilities":
                if principal is None or claims is None:
                    raise SyncBindingV2Error("authentication_required", 401)
                result = current_service.capabilities(principal, claims)
            else:
                if principal is None or claims is None or parsed is None:
                    raise SyncBindingV2Error()
                result = current_service.bind(principal, claims, parsed)
            maximum = 65536 if kind == "capabilities" else 16384
            if len(canonical_json_bytes(result)) > maximum:
                raise SyncBindingV2Error("sync_binding_unavailable", 503)
            return JSONResponse(result, headers=_NO_STORE)
        except AccessAuthenticationUnavailableError:
            raise _error(SyncBindingV2Error("sync_binding_unavailable", 503)) from None
        except InvalidAccessTokenError:
            raise _error(SyncBindingV2Error("authentication_required", 401)) from None
        except SyncBindingV2Error as error:
            raise _error(error) from None
        except SQLAlchemyError, RuntimeError, ValueError, TypeError:
            raise _error(SyncBindingV2Error("sync_binding_unavailable", 503)) from None

    async def dispatch(kind: str, request: Request) -> JSONResponse:
        body = bytearray()
        frames = 0
        async for chunk in request.stream():
            frames += 1
            if len(body) + len(chunk) > MAX_BODY_BYTES or frames > MAX_REQUEST_BODY_FRAMES:
                raise _error(SyncBindingV2Error("request_body_too_large", 413))
            body.extend(chunk)
        try:
            bearer = shape(kind, request, bytes(body))
            if bearer is not None and preflight_access is not None:
                # Pure verification preserves malformed/expired JWT precedence
                # even when admission is full. No candidate/claims are returned.
                # R1 receipt authority is read ONLY by the real authenticator
                # inside the two-slot runtime and its rebound owned Sessions.
                preflight_access(bearer)
        except InvalidAccessTokenError:
            raise _error(SyncBindingV2Error("authentication_required", 401)) from None
        except SyncBindingV2Error as error:
            raise _error(error) from None
        except RuntimeError, ValueError, TypeError:
            raise _error(SyncBindingV2Error("sync_binding_unavailable", 503)) from None
        if database is None:
            # Parser-only/injected adapters; production composition always
            # supplies the bounded DB owner, including its real authenticator.
            return await run_in_threadpool(invoke, kind, request, bytes(body))
        try:
            return await database.run(
                lambda sessions, budget: invoke(kind, request, bytes(body), sessions)
            )
        except TimeoutError, SyncBindingV2Error:
            raise _error(SyncBindingV2Error("sync_binding_unavailable", 503)) from None

    @router.get("/discovery")
    async def discovery(request: Request) -> JSONResponse:
        return await dispatch("discovery", request)

    @router.get("/capabilities")
    async def capabilities(request: Request) -> JSONResponse:
        return await dispatch("capabilities", request)

    @router.post("/bind")
    async def bind(request: Request) -> JSONResponse:
        return await dispatch("bind", request)

    return router


def _error(error: SyncBindingV2Error) -> ApiError:
    return ApiError(
        code=error.code,
        status_code=error.status,
        retryable=error.retryable,
        message="Authentication is required."
        if error.status == 401
        else "The sync binding request could not be applied.",
        headers={**_NO_STORE, **({"WWW-Authenticate": "Bearer"} if error.status == 401 else {})},
        details={"bootstrap_required": True} if error.code == "DEVICE_RESET_REQUIRED" else None,
    )
