"""Alembic environment for the AutPlay PostgreSQL schema."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from autplay.adapters.postgresql.metadata import metadata
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

REFERENCE_SCHEMAS = frozenset(
    {
        "account",
        "audit",
        "catalog",
        "identity",
        "importing",
        "jobs",
        "library",
        "ml",
        "playlist",
        "sync",
        "vault",
        "wave",
    }
)

target_metadata = metadata

_P09_AUTOGENERATE_EXCLUDED = {
    "bootstrap_session",
    "bootstrap_snapshot_item",
    "user_interaction_event",
    "sync_event",
    "room",
    "member",
    "invitation",
    "queue_entry",
    "command",
    "preflight",
    "timing_report",
}


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """P09 physical runtime DDL is additive and audited by dedicated integration tests."""
    del object_, reflected, compare_to
    return not (type_ == "table" and name in _P09_AUTOGENERATE_EXCLUDED)


def _database_url() -> str:
    """Return the explicitly supplied disposable/deployment database URL."""
    url = os.getenv("AUTPLAY_DATABASE_URL") or os.getenv("AUTPLAY_TEST_DATABASE_URL")
    if not url:
        message = (
            "AUTPLAY_DATABASE_URL or AUTPLAY_TEST_DATABASE_URL is required; "
            "no database URL is stored in alembic.ini"
        )
        raise RuntimeError(message)
    return url


def _include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    """Limit reflection to the twelve reference data schemas."""
    if type_ == "schema":
        return name in REFERENCE_SCHEMAS
    schema_name = parent_names.get("schema_name")
    if schema_name is not None and schema_name not in REFERENCE_SCHEMAS:
        return False
    if type_ == "table":
        qualified_name = parent_names.get("schema_qualified_table_name")
        return qualified_name in target_metadata.tables
    return True


def _configure(connection: object | None, *, url: str | None = None) -> None:
    options: dict[str, object] = {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "include_name": _include_name,
        "compare_type": True,
        "compare_server_default": True,
        "version_table_schema": None,
        "include_object": _include_object,
    }
    if connection is not None:
        options["connection"] = connection
    if url is not None:
        options["url"] = url
        options["literal_binds"] = True
        options["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**options)


def run_migrations_offline() -> None:
    """Render migrations without creating an Engine."""
    _configure(None, url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations through a short-lived SQLAlchemy Engine."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
