"""Bounded redaction for subprocess and SDK diagnostics."""

from __future__ import annotations

import re
from pathlib import Path

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|secret|password)\b(\s*[:=]\s*)([^\s,;]+)"
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^/@\s]+)@")


class Redactor:
    """Remove secrets and local absolute path prefixes from bounded text."""

    def __init__(self, repo_root: Path, *, max_chars: int = 8_000) -> None:
        self.repo_root = str(repo_root.resolve())
        self.home = str(Path.home().resolve())
        self.max_chars = max_chars

    def text(self, value: str) -> str:
        redacted = _BEARER.sub("Bearer <redacted>", value)
        redacted = _KEY_VALUE.sub(r"\1\2<redacted>", redacted)
        redacted = _URL_CREDENTIALS.sub(r"\1<redacted>@", redacted)
        redacted = _replace_path(redacted, self.repo_root, "<repo>")
        redacted = _replace_path(redacted, self.home, "<home>")
        if len(redacted) <= self.max_chars:
            return redacted
        half = max(1, (self.max_chars - 32) // 2)
        return f"{redacted[:half]}\n... <output truncated> ...\n{redacted[-half:]}"


def _replace_path(value: str, raw_path: str, replacement: str) -> str:
    result = value.replace(raw_path, replacement)
    return result.replace(raw_path.replace("\\", "/"), replacement)
