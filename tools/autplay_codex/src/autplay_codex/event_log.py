"""Allowlisted structured event logging for local harness diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import utc_now
from .redaction import Redactor

_ALLOWED_FIELDS = {
    "task_id",
    "milestone_id",
    "state",
    "task_class",
    "model",
    "reasoning",
    "thread_id",
    "check",
    "status",
    "finding_count",
    "attempt",
    "reason",
    "routing_reason",
}

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "off": 100}


class EventLogger:
    """Append small JSON records without logging prompts or arbitrary payloads."""

    def __init__(self, state_dir: Path, repo_root: Path, *, log_level: str = "info") -> None:
        self.path = state_dir / "events.jsonl"
        self.redactor = Redactor(repo_root, max_chars=1_000)
        try:
            self.minimum_level = _LEVELS[log_level]
        except KeyError as exc:
            raise ValueError(f"unsupported event log level: {log_level}") from exc

    def emit(self, event: str, *, level: str = "info", **fields: object) -> None:
        try:
            event_level = _LEVELS[level]
        except KeyError as exc:
            raise ValueError(f"unsupported event level: {level}") from exc
        if event_level < self.minimum_level:
            return
        unexpected = set(fields) - _ALLOWED_FIELDS
        if unexpected:
            raise ValueError(f"event fields are not allowlisted: {sorted(unexpected)}")
        document: dict[str, Any] = {
            "timestamp": utc_now(),
            "event": event,
            "level": level,
        }
        for key, value in fields.items():
            if value is None or isinstance(value, bool | int | float):
                document[key] = value
            elif isinstance(value, str):
                document[key] = self.redactor.text(value)
            else:
                raise ValueError(f"unsupported event field type for {key}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n"
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
