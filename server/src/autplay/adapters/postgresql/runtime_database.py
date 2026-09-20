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


def create_resource_control_engine(settings: RuntimeSettings) -> Engine:
    """Independent short-transaction pool; retained upload UoWs never check it out.

    These local process bounds do not substitute for measured global admission.
    The HTTP monotonic deadline also applies when the network/database cannot stop.
    """
    return create_engine(
        settings.database_url.get_secret_value(),
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=0,
        pool_timeout=0.25,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 2,
            "application_name": "autplay-resource-control",
            "options": "-c statement_timeout=1000 -c lock_timeout=500",
        },
    )


__all__ = ("RuntimeSettings", "create_resource_control_engine", "create_runtime_engine")
