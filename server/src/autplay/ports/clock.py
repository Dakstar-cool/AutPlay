"""Clock boundary for application code."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Return a timezone-aware current instant."""

    def now(self) -> datetime:
        """Return the current instant."""

        ...


__all__ = ("Clock",)
