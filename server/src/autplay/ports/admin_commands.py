"""Application port for bounded M6-C owner-scoped browser commands."""

from __future__ import annotations

from typing import Protocol

from autplay.domain.admin_commands import AdminCommand


class AdminCommandRepository(Protocol):
    def execute(
        self, command: AdminCommand, *, action: str, target_type: str
    ) -> dict[str, object]: ...


__all__ = ("AdminCommandRepository",)
