"""Identifier-generation boundary for application code."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class IdGenerator(Protocol):
    """Generate one opaque time-ordered application identifier."""

    def new(self) -> UUID:
        """Return a new identifier."""

        ...


__all__ = ("IdGenerator",)
