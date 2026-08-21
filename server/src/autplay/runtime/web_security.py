"""M6 browser-only origin, form, cookie, and response-security primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlsplit

import rfc8785
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autplay.domain.jobs import JsonValue

MAX_FORM_BYTES: Final = 16_384
MAX_FORM_FIELDS: Final = 32
RELEASE_SESSION_COOKIE: Final = "__Host-autplay_admin"
RELEASE_PREAUTH_COOKIE: Final = "__Host-autplay_login"
DEVELOPMENT_SESSION_COOKIE: Final = "autplay_admin_dev"
DEVELOPMENT_PREAUTH_COOKIE: Final = "autplay_login_dev"

ADMIN_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class WebRequestRejected(ValueError):
    """A browser request failed a stable pre-application security check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AdminWebSecurityMiddleware:
    """Apply the accepted browser headers even to framework 404/405/422 responses."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not (path == "/admin" or path.startswith("/admin/")):
            await self._app(scope, receive, send)
            return
        static = path.startswith("/admin/static/")

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                enforced = {
                    name.lower().encode("ascii"): value.encode("ascii")
                    for name, value in ADMIN_SECURITY_HEADERS.items()
                    if not static or name not in {"Cache-Control", "Pragma"}
                }
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in enforced
                ]
                headers.extend(enforced.items())
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_secured)


@dataclass(frozen=True, slots=True)
class WebCookieProfile:
    """Exact accepted cookie names and attributes for one canonical origin."""

    session_name: str
    preauth_name: str
    secure: bool
    session_path: str
    preauth_path: str

    @classmethod
    def for_origin(cls, origin: str) -> WebCookieProfile:
        scheme = urlsplit(origin).scheme
        if scheme == "https":
            return cls(
                session_name=RELEASE_SESSION_COOKIE,
                preauth_name=RELEASE_PREAUTH_COOKIE,
                secure=True,
                session_path="/",
                preauth_path="/",
            )
        if scheme == "http":
            return cls(
                session_name=DEVELOPMENT_SESSION_COOKIE,
                preauth_name=DEVELOPMENT_PREAUTH_COOKIE,
                secure=False,
                session_path="/admin",
                preauth_path="/admin/login",
            )
        raise ValueError("unsupported admin Web origin scheme")

    def set_session(self, response: Response, bearer: str, *, max_age: int) -> None:
        response.set_cookie(
            self.session_name,
            bearer,
            max_age=max_age,
            path=self.session_path,
            secure=self.secure,
            httponly=True,
            samesite="strict",
        )

    def set_preauth(self, response: Response, bearer: str) -> None:
        response.set_cookie(
            self.preauth_name,
            bearer,
            max_age=300,
            path=self.preauth_path,
            secure=self.secure,
            httponly=True,
            samesite="strict",
        )

    def clear_session(self, response: Response) -> None:
        response.delete_cookie(
            self.session_name,
            path=self.session_path,
            secure=self.secure,
            httponly=True,
            samesite="strict",
        )

    def clear_preauth(self, response: Response) -> None:
        response.delete_cookie(
            self.preauth_name,
            path=self.preauth_path,
            secure=self.secure,
            httponly=True,
            samesite="strict",
        )


def require_exact_origin(scope: Scope, expected_origin: str) -> None:
    """Require one byte-exact Origin header before any application command."""

    values = [value for name, value in scope.get("headers", []) if name.lower() == b"origin"]
    if len(values) != 1:
        raise WebRequestRejected("origin_invalid")
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise WebRequestRejected("origin_invalid") from error
    if not hmac.compare_digest(value, expected_origin):
        raise WebRequestRejected("origin_invalid")


def parse_urlencoded_form(
    content_type: str | None,
    body: bytes,
    *,
    allowed_fields: frozenset[str],
) -> dict[str, str]:
    """Parse one small exact form without the multipart dependency or duplicate keys."""

    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type != "application/x-www-form-urlencoded" or len(body) > MAX_FORM_BYTES:
        raise WebRequestRejected("request_validation_failed")
    try:
        encoded = body.decode("ascii")
        pairs = parse_qsl(
            encoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=MAX_FORM_FIELDS,
            encoding="utf-8",
            errors="strict",
        )
    except UnicodeError, ValueError:
        raise WebRequestRejected("request_validation_failed") from None
    result: dict[str, str] = {}
    for key, value in pairs:
        if key not in allowed_fields or key in result or "\x00" in value:
            raise WebRequestRejected("request_validation_failed")
        result[key] = value
    if set(result) != allowed_fields:
        raise WebRequestRejected("request_validation_failed")
    return result


def canonical_form_request_hash(method: str, path: str, fields: Mapping[str, str]) -> bytes:
    """Hash the exact bounded command fields with the accepted RFC 8785 primitive."""

    document: dict[str, JsonValue] = {
        "fields": {key: value for key, value in fields.items()},
        "method": method.upper(),
        "path": path,
    }
    return hashlib.sha256(rfc8785.dumps(document)).digest()


def encode_request_integrity_token(value: bytes) -> str:
    """Encode one 256-bit CSRF value for a no-store same-origin HTML form."""

    if len(value) != 32:
        raise WebRequestRejected("csrf_invalid")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_request_integrity_token(value: str) -> bytes:
    """Decode the one canonical unpadded base64url CSRF representation."""

    if len(value) != 43 or any(character not in _BASE64URL for character in value):
        raise WebRequestRejected("csrf_invalid")
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii") + b"=")
    except (UnicodeEncodeError, ValueError) as error:
        raise WebRequestRejected("csrf_invalid") from error
    if len(decoded) != 32 or encode_request_integrity_token(decoded) != value:
        raise WebRequestRejected("csrf_invalid")
    return decoded


def source_rate_key(secret: bytes, source: str | None) -> bytes:
    """Return a non-reversible rate-limit key without persisting a raw network address."""

    normalized = "unknown" if source is None else source.strip()
    if not normalized or len(normalized) > 255:
        normalized = "unknown"
    return hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).digest()


def apply_admin_security_headers(response: Response) -> Response:
    """Apply the complete accepted security profile to one Web response."""

    for name, value in ADMIN_SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


_BASE64URL: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


__all__ = (
    "ADMIN_SECURITY_HEADERS",
    "MAX_FORM_BYTES",
    "AdminWebSecurityMiddleware",
    "WebCookieProfile",
    "WebRequestRejected",
    "apply_admin_security_headers",
    "canonical_form_request_hash",
    "decode_request_integrity_token",
    "encode_request_integrity_token",
    "parse_urlencoded_form",
    "require_exact_origin",
    "source_rate_key",
)
