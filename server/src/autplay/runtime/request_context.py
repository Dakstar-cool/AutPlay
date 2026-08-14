"""Per-request correlation state without framework imports."""

from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import UUID, uuid7

_request_id: ContextVar[str | None] = ContextVar("autplay_request_id", default=None)


def normalize_or_create_request_id(value: str | None) -> str:
    """Accept one canonical UUID request ID or create a UUIDv7."""

    if value is not None and len(value) == 36:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            canonical = str(parsed)
            if value == canonical:
                return canonical
    return str(uuid7())


def bind_request_id(value: str) -> Token[str | None]:
    """Bind a validated request ID for the current execution context."""

    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request ID context that preceded ``bind_request_id``."""

    _request_id.reset(token)


def current_request_id() -> str | None:
    """Return the active request ID, if execution is inside an HTTP request."""

    return _request_id.get()


__all__ = (
    "bind_request_id",
    "current_request_id",
    "normalize_or_create_request_id",
    "reset_request_id",
)
