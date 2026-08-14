"""Standard-library implementations of small runtime ports."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid7


class SystemClock:
    """UTC wall clock for application timestamps.

    PostgreSQL remains authoritative for durable job leases so process clock
    skew cannot revive or prematurely expire a lease.
    """

    def now(self) -> datetime:
        """Return a timezone-aware UTC instant."""

        return datetime.now(UTC)


class Uuid7Generator:
    """Generate time-ordered UUIDv7 application identifiers."""

    def new(self) -> UUID:
        """Return one UUIDv7 value."""

        return uuid7()


__all__ = ("SystemClock", "Uuid7Generator")
