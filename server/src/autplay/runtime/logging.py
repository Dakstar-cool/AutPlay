"""Structured JSON logging with bounded fields and defense-in-depth redaction."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from .request_context import current_request_id

_REDACTED: Final = "[REDACTED]"
_REDACTED_PATH: Final = "[REDACTED_PATH]"
_SENSITIVE_KEY: Final = re.compile(
    r"(?:^|_)(?:access|auth|refresh)?_?token(?:$|_)|"
    r"(?:^|_)(?:api_key|authorization|cookie|password|passwd|secret|credential|"
    r"database_url|private_url|raw_path|source_uri|provider_payload|raw_payload)(?:$|_)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET: Final = re.compile(
    r"(?i)\b(password|passwd|(?:access_|refresh_|auth_)?token|secret|authorization|cookie)="
    r"([^\s,;]+)"
)
_BEARER_SECRET: Final = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO: Final = re.compile(r"(?i)(https?://)[^/@\s]+@")
_URL_QUERY: Final = re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+")
_WINDOWS_USER_PATH: Final = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+(?:\\[^\s,;]+)?")
_POSIX_USER_PATH: Final = re.compile(r"(?:(?<=\s)|^)/(?:home|Users)/[^\s,;]+")
_ALLOWED_EXTRA_FIELDS: Final = frozenset(
    {
        "adapter_id",
        "component",
        "device_id",
        "duration_ms",
        "error_code",
        "exception_type",
        "job_id",
        "job_type",
        "method",
        "outcome",
        "reason_code",
        "recording_id",
        "request_id",
        "route",
        "service",
        "status_code",
        "trace_id",
        "user_id_hash",
    }
)


def sanitize_log_value(value: object, *, key: str | None = None) -> object:
    """Return a JSON-safe redacted value suitable for normal logs."""

    if key is not None and _SENSITIVE_KEY.search(key):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_log_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value[:100]]
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    bounded = value[:4_096]
    bounded = _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}={_REDACTED}", bounded)
    bounded = _BEARER_SECRET.sub(f"Bearer {_REDACTED}", bounded)
    bounded = _URL_USERINFO.sub(lambda match: f"{match.group(1)}{_REDACTED}@", bounded)
    bounded = _URL_QUERY.sub(lambda match: f"{match.group(1)}?{_REDACTED}", bounded)
    bounded = _WINDOWS_USER_PATH.sub(_REDACTED_PATH, bounded)
    return _POSIX_USER_PATH.sub(_REDACTED_PATH, bounded)


class JsonLogFormatter(logging.Formatter):
    """Render an allowlisted LogRecord as one compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or current_request_id()
        document: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": sanitize_log_value(record.getMessage()),
        }
        if request_id is not None:
            document["request_id"] = sanitize_log_value(request_id)
        for field_name in sorted(_ALLOWED_EXTRA_FIELDS - {"request_id"}):
            if hasattr(record, field_name):
                document[field_name] = sanitize_log_value(
                    getattr(record, field_name), key=field_name
                )
        if record.exc_info is not None and record.exc_info[0] is not None:
            document["exception_type"] = record.exc_info[0].__name__
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def configure_json_logging(*, service: str, level: str) -> None:
    """Replace process root handlers with the AutPlay JSON log contract."""

    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(_ServiceFilter(service))
    root.handlers[:] = [handler]
    root.setLevel(level)


class _ServiceFilter(logging.Filter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "service"):
            record.service = self._service
        return True


__all__ = (
    "JsonLogFormatter",
    "configure_json_logging",
    "sanitize_log_value",
)
