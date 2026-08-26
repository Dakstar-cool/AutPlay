"""PostgreSQL connectivity and migration compatibility readiness probe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import Engine, text
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
    TimeoutError,
)

EXPECTED_MIGRATION_HEAD: Final = "0026_s1d_guest_room_access"


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Sanitized readiness state safe to return over HTTP."""

    ready: bool
    component: str
    code: str | None = None


class ReadinessProbe(Protocol):
    """Required-dependency probe injected into an API process."""

    def check(self) -> ReadinessResult:
        """Return current readiness without raising expected dependency errors."""

        ...


@dataclass(frozen=True, slots=True)
class PostgreSQLReadinessProbe:
    """Require a responsive PostgreSQL database at the single expected head."""

    engine: Engine
    expected_head: str = EXPECTED_MIGRATION_HEAD

    def check(self) -> ReadinessResult:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                try:
                    observed_heads = frozenset(
                        connection.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalars()
                    )
                except SQLAlchemyError as error:
                    if _is_database_unavailable(error):
                        return _unavailable()
                    return _migration_mismatch()
        except SQLAlchemyError:
            return _unavailable()
        if observed_heads != {self.expected_head}:
            return _migration_mismatch()
        return ReadinessResult(ready=True, component="postgresql")


def _is_database_unavailable(error: SQLAlchemyError) -> bool:
    if _is_missing_migration_table(error):
        return False
    return isinstance(
        error,
        (DisconnectionError, InterfaceError, OperationalError, TimeoutError),
    ) or (isinstance(error, DBAPIError) and error.connection_invalidated)


def _is_missing_migration_table(error: SQLAlchemyError) -> bool:
    if not isinstance(error, DBAPIError):
        return False
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return sqlstate == "42P01" or "no such table: alembic_version" in str(original).lower()


def _unavailable() -> ReadinessResult:
    return ReadinessResult(
        ready=False,
        component="postgresql",
        code="database_unavailable",
    )


def _migration_mismatch() -> ReadinessResult:
    return ReadinessResult(
        ready=False,
        component="postgresql_schema",
        code="database_migration_mismatch",
    )


__all__ = (
    "EXPECTED_MIGRATION_HEAD",
    "PostgreSQLReadinessProbe",
    "ReadinessProbe",
    "ReadinessResult",
)
