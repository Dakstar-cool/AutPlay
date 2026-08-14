"""Structured log shape and redaction tests."""

from __future__ import annotations

import io
import json
import logging

from autplay.runtime.logging import JsonLogFormatter, sanitize_log_value


def test_json_formatter_redacts_secrets_urls_and_personal_paths() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("autplay.tests.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "password=hunter2 Authorization=Bearer.secret "
        "https://alice:pw@example.test/private?token=value "
        r"C:\Users\alice\Music\private.flac",
        extra={"reason_code": "access_token=raw-token"},
    )

    document = json.loads(stream.getvalue())
    rendered = json.dumps(document)
    assert document["event"]
    assert "hunter2" not in rendered
    assert "Bearer.secret" not in rendered
    assert "alice:pw" not in rendered
    assert "token=value" not in rendered
    assert "raw-token" not in rendered
    assert "private.flac" not in rendered
    assert "[REDACTED]" in rendered


def test_recursive_redaction_uses_sensitive_field_names() -> None:
    sanitized = sanitize_log_value(
        {
            "nested": {"refresh_token": "secret-token", "safe": "reason"},
            "database_url": "postgresql://user:password@localhost/db",
        }
    )

    assert sanitized == {
        "nested": {"refresh_token": "[REDACTED]", "safe": "reason"},
        "database_url": "[REDACTED]",
    }
