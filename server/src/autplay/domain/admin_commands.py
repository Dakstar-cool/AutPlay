"""Typed M6-C administrative commands; browser actors never become Android principals."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .web_admin import WebActor


@dataclass(frozen=True, slots=True)
class AdminCommand:
    actor: WebActor
    operation_id: UUID
    target_id: UUID
    request_sha256: bytes
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if len(self.request_sha256) != 32:
            raise ValueError("request_sha256 must contain 32 bytes")
        if self.reason_code is not None and not 1 <= len(self.reason_code) <= 64:
            raise ValueError("reason_code must contain 1..64 characters")


__all__ = ("AdminCommand",)
