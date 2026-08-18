"""HTTP correlation, stable errors, metrics, and access-log middleware."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .metrics import RuntimeMetrics
from .request_context import (
    bind_request_id,
    normalize_or_create_request_id,
    reset_request_id,
)

REQUEST_ID_HEADER: Final = "X-Request-ID"
# P09 sync push is a separately validated protocol body with an 8 MiB contract cap.
MAX_REQUEST_BODY_BYTES: Final = 8_388_608
MAX_REQUEST_BODY_FRAMES: Final = 1_024
_REQUEST_ID_HEADER_BYTES: Final = b"x-request-id"
_CONTENT_LENGTH_HEADER_BYTES: Final = b"content-length"
_HTTP_METHODS: Final = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
_LOGGER = logging.getLogger("autplay.http")


class _RequestBodyTooLarge(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    """An expected API failure with a stable public contract."""

    code: str
    message: str
    status_code: int
    retryable: bool = False
    headers: dict[str, str] | None = None
    details: dict[str, object] | None = None


def error_response(
    *,
    request_id: str,
    code: str,
    message: str,
    status_code: int,
    retryable: bool,
    headers: dict[str, str] | None = None,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    """Build the uniform, user-safe error envelope."""

    response_headers = {"Cache-Control": "no-store"}
    if headers is not None:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "request_id": request_id,
            }
            | (details or {})
        },
        headers=response_headers,
    )


class RequestRuntimeMiddleware:
    """Pure ASGI middleware safe for future streaming response bodies."""

    def __init__(self, app: ASGIApp, *, metrics: RuntimeMetrics) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = normalize_or_create_request_id(_incoming_request_id(scope))
        scope.setdefault("state", {})["request_id"] = request_id
        token = bind_request_id(request_id)
        started = perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _REQUEST_ID_HEADER_BYTES
                ]
                headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            try:
                if _declared_body_too_large(scope):
                    raise _RequestBodyTooLarge
                buffered_messages = await _read_bounded_request(receive)
                next_message = 0

                async def receive_buffered() -> Message:
                    nonlocal next_message
                    if next_message < len(buffered_messages):
                        message = buffered_messages[next_message]
                        next_message += 1
                        return message
                    return await receive()

                await self._app(scope, receive_buffered, send_with_request_id)
            except _RequestBodyTooLarge:
                if response_started:
                    raise
                response = error_response(
                    request_id=request_id,
                    code="request_body_too_large",
                    message="The request body is too large.",
                    status_code=413,
                    retryable=False,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send_with_request_id)
            except Exception as error:
                _LOGGER.error(
                    "unhandled_request_error",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"error_code": "internal_error", "request_id": request_id},
                )
                if response_started:
                    raise
                response = error_response(
                    request_id=request_id,
                    code="internal_error",
                    message="An internal error occurred.",
                    status_code=500,
                    retryable=False,
                )
                await response(scope, receive, send_with_request_id)
        finally:
            duration_seconds = max(0.0, perf_counter() - started)
            method = _method_label(scope)
            route = _route_template(scope)
            self._metrics.observe_http(
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            _LOGGER.info(
                "request_completed",
                extra={
                    "duration_ms": round(duration_seconds * 1_000, 3),
                    "method": method,
                    "request_id": request_id,
                    "route": route,
                    "status_code": status_code,
                },
            )
            reset_request_id(token)


def install_error_handlers(app: FastAPI) -> None:
    """Install stable handlers for expected framework/application failures."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(
            request_id=_request_id(request),
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            retryable=error.retryable,
            headers=error.headers,
            details=error.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del error
        return error_response(
            request_id=_request_id(request),
            code="request_validation_failed",
            message="The request is invalid.",
            status_code=422,
            retryable=False,
        )

    @app.exception_handler(StarletteHttpException)
    async def handle_http_error(request: Request, error: StarletteHttpException) -> JSONResponse:
        code, message = _http_error_contract(error.status_code)
        safe_headers = _safe_http_headers(error.headers)
        return error_response(
            request_id=_request_id(request),
            code=code,
            message=message,
            status_code=error.status_code,
            retryable=error.status_code in {429, 502, 503, 504},
            headers=safe_headers,
        )


def _incoming_request_id(scope: Scope) -> str | None:
    values = [
        cast(bytes, value)
        for name, value in scope.get("headers", [])
        if name.lower() == _REQUEST_ID_HEADER_BYTES
    ]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _declared_body_too_large(scope: Scope) -> bool:
    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == _CONTENT_LENGTH_HEADER_BYTES
    ]
    if not values:
        return False
    if len(values) != 1:
        return True
    try:
        declared = int(values[0].decode("ascii"))
    except UnicodeDecodeError, ValueError:
        return True
    return declared < 0 or declared > MAX_REQUEST_BODY_BYTES


async def _read_bounded_request(receive: Receive) -> list[Message]:
    body = bytearray()
    frame_count = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            if not body:
                return [message]
            return [
                {"type": "http.request", "body": bytes(body), "more_body": True},
                message,
            ]
        if message["type"] != "http.request":
            raise RuntimeError("unexpected ASGI request message")
        frame_count += 1
        chunk = message.get("body", b"")
        if frame_count > MAX_REQUEST_BODY_FRAMES or len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
            raise _RequestBodyTooLarge
        body.extend(chunk)
        if not message.get("more_body", False):
            return [{"type": "http.request", "body": bytes(body), "more_body": False}]


def _method_label(scope: Scope) -> str:
    method = str(scope.get("method", "")).upper()
    return method if method in _HTTP_METHODS else "OTHER"


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/") and len(template) <= 500:
        return template
    return "unmatched"


def _request_id(request: Request) -> str:
    state_value = getattr(request.state, "request_id", None)
    if isinstance(state_value, str):
        return state_value
    return normalize_or_create_request_id(None)


def _http_error_contract(status_code: int) -> tuple[str, str]:
    if status_code == 404:
        return "not_found", "The requested resource was not found."
    if status_code == 405:
        return "method_not_allowed", "The request method is not allowed."
    if status_code == 401:
        return "authentication_required", "Authentication is required."
    if status_code == 403:
        return "forbidden", "The operation is not permitted."
    return "http_error", "The request could not be completed."


def _safe_http_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    allowed = {
        key: value
        for key, value in headers.items()
        if key.lower() in {"allow", "retry-after", "www-authenticate"}
    }
    return allowed or None


__all__ = (
    "MAX_REQUEST_BODY_BYTES",
    "MAX_REQUEST_BODY_FRAMES",
    "REQUEST_ID_HEADER",
    "ApiError",
    "RequestRuntimeMiddleware",
    "error_response",
    "install_error_handlers",
)
