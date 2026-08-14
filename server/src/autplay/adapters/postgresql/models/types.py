"""Shared JSON annotation for PostgreSQL JSONB columns."""

from __future__ import annotations

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

__all__ = ("JsonValue",)
