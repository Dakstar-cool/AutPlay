"""Bounded SQLAlchemy engine construction for runtime processes."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from autplay.runtime.settings import ApiSettings, StreamSettings, WorkerSettings

RuntimeSettings = ApiSettings | StreamSettings | WorkerSettings


def create_runtime_engine(settings: RuntimeSettings) -> Engine:
    """Create a lazy CPU-only PostgreSQL engine without logging its URL."""

    connect_timeout = max(1, round(settings.database_connect_timeout_seconds))
    statement_timeout = settings.database_statement_timeout_ms
    return create_engine(
        settings.database_url.get_secret_value(),
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.database_connect_timeout_seconds,
        pool_recycle=1_800,
        connect_args={
            "connect_timeout": connect_timeout,
            "options": f"-c statement_timeout={statement_timeout}",
        },
    )


__all__ = ("RuntimeSettings", "create_runtime_engine")
