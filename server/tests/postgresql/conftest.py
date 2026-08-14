"""Disposable PostgreSQL fixtures for P02 integration tests.

The canonical check scripts own Docker Compose and provide
``AUTPLAY_TEST_DATABASE_URL``.  This module never starts Docker and never drops
the supplied database.  It creates and removes only randomly named databases
under the ``autplay_p02_`` namespace.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import Connection, sql
from sqlalchemy.engine import URL, make_url

SERVER_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = SERVER_ROOT.parent
ALEMBIC_CONFIG_PATH = SERVER_ROOT / "alembic.ini"
REFERENCE_DDL_PATH = REPOSITORY_ROOT / "docs" / "design" / "AutPlay_PostgreSQL_Schema_v1.sql"

DATABASE_PREFIX = "autplay_p02_"
DATABASE_NAME_PATTERN = re.compile(r"^autplay_p02_[a-f0-9]{16,32}$")
ALLOWED_DRIVER_NAMES = frozenset({"postgresql", "postgresql+psycopg"})
ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _guard_root_url(value: str) -> URL:
    """Reject any DSN that could point the destructive fixture at a real database."""
    url = make_url(value)
    if url.drivername not in ALLOWED_DRIVER_NAMES:
        raise pytest.UsageError("AUTPLAY_TEST_DATABASE_URL must use PostgreSQL/psycopg")
    if (url.host or "").lower() not in ALLOWED_HOSTS:
        raise pytest.UsageError("AUTPLAY_TEST_DATABASE_URL must use a loopback host")
    if url.database != "autplay":
        raise pytest.UsageError("AUTPLAY_TEST_DATABASE_URL database must be exactly 'autplay'")
    if url.username != "autplay" or not url.password:
        raise pytest.UsageError("AUTPLAY_TEST_DATABASE_URL must use the disposable autplay login")
    if url.port is None or not 1 <= url.port <= 65535:
        raise pytest.UsageError("AUTPLAY_TEST_DATABASE_URL must include the published test port")
    return url.set(drivername="postgresql+psycopg")


def _new_database_name() -> str:
    return f"{DATABASE_PREFIX}{uuid.uuid4().hex[:24]}"


def _guard_generated_name(database_name: str) -> None:
    if DATABASE_NAME_PATTERN.fullmatch(database_name) is None:
        raise RuntimeError(f"refusing database operation outside {DATABASE_PREFIX!r} namespace")


@dataclass
class DatabaseHarness:
    """Create isolated databases through one guarded disposable admin database."""

    root_url: URL
    created_databases: set[str] = field(default_factory=set, init=False)

    def _conninfo(self, database_name: str | None = None) -> str:
        url = self.root_url
        if database_name is not None:
            _guard_generated_name(database_name)
            url = url.set(database=database_name)
        return url.set(drivername="postgresql").render_as_string(hide_password=False)

    def database_url(self, database_name: str) -> str:
        """Return a SQLAlchemy psycopg URL for a generated database."""
        _guard_generated_name(database_name)
        return self.root_url.set(database=database_name).render_as_string(hide_password=False)

    def connect(
        self, database_name: str | None = None, *, autocommit: bool = False
    ) -> Connection[Any]:
        """Open a connection with bounded waits suitable for invariant tests."""
        connection: Connection[Any] = psycopg.connect(
            self._conninfo(database_name), autocommit=autocommit
        )
        if not autocommit:
            connection.execute("SET TIME ZONE 'UTC'")
            connection.execute("SET lock_timeout = '5s'")
            connection.execute("SET statement_timeout = '30s'")
        return connection

    def verify_root(self) -> None:
        """Verify the runtime reached the exact disposable pinned database."""
        with self.connect(autocommit=True) as connection:
            row = connection.execute(
                "SELECT current_database(), current_setting('server_version')"
            ).fetchone()
        if row is None:
            raise pytest.UsageError("disposable PostgreSQL version query returned no row")
        current_database, server_version = row
        if current_database != "autplay" or not str(server_version).startswith("18.4"):
            raise pytest.UsageError(
                "AUTPLAY_TEST_DATABASE_URL did not reach disposable PostgreSQL 18.4/autplay"
            )

    def create_database(self, *, template: str | None = None) -> str:
        """Create a fresh generated database, optionally cloned from a closed template."""
        database_name = _new_database_name()
        _guard_generated_name(database_name)
        if template is not None:
            _guard_generated_name(template)
        with self.connect(autocommit=True) as connection:
            exists_row = connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
                (database_name,),
            ).fetchone()
            if exists_row is None:
                raise RuntimeError("database existence query returned no row")
            exists = bool(exists_row[0])
            if exists:
                raise RuntimeError(f"generated test database already exists: {database_name}")
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            if template is not None:
                statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
            connection.execute(statement)
        self.created_databases.add(database_name)
        return database_name

    def drop_database(self, database_name: str) -> None:
        """Force-drop only a generated disposable database."""
        _guard_generated_name(database_name)
        with self.connect(autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )
        self.created_databases.discard(database_name)

    def alembic_config(self, database_name: str) -> Config:
        """Build an Alembic config without writing a machine-local URL to disk."""
        if not ALEMBIC_CONFIG_PATH.is_file():
            raise RuntimeError(f"missing Alembic configuration: {ALEMBIC_CONFIG_PATH}")
        config = Config(str(ALEMBIC_CONFIG_PATH))
        config.set_main_option(
            "sqlalchemy.url", self.database_url(database_name).replace("%", "%%")
        )
        return config

    @contextmanager
    def _migration_environment(self, database_name: str) -> Iterator[None]:
        """Point env.py at one generated DB and restore the caller environment."""
        _guard_generated_name(database_name)
        variable = "AUTPLAY_DATABASE_URL"
        previous = os.environ.get(variable)
        os.environ[variable] = self.database_url(database_name)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous

    def upgrade(self, database_name: str, revision: str = "head") -> None:
        """Upgrade a generated database to an Alembic revision."""
        with self._migration_environment(database_name):
            command.upgrade(self.alembic_config(database_name), revision)

    def downgrade(self, database_name: str, revision: str) -> None:
        """Downgrade a generated database to an Alembic revision."""
        with self._migration_environment(database_name):
            command.downgrade(self.alembic_config(database_name), revision)


@pytest.fixture(scope="session")
def database_harness() -> Iterator[DatabaseHarness]:
    """Expose the guarded database harness after validating the supplied DSN."""
    raw_url = os.environ.get("AUTPLAY_TEST_DATABASE_URL")
    if raw_url is None:
        raise pytest.UsageError(
            "AUTPLAY_TEST_DATABASE_URL is required; use the canonical disposable DB check"
        )
    harness = DatabaseHarness(_guard_root_url(raw_url))
    harness.verify_root()
    yield harness
    if harness.created_databases:
        raise RuntimeError(
            f"P02 test databases leaked after cleanup: {sorted(harness.created_databases)}"
        )


@pytest.fixture(scope="session")
def migrated_template_database(
    database_harness: DatabaseHarness,
) -> Iterator[str]:
    """Create one closed Alembic-head template for cheap per-test isolation."""
    database_name = database_harness.create_database()
    try:
        database_harness.upgrade(database_name)
        with database_harness.connect(database_name) as connection:
            row = connection.execute(
                """
                SELECT current_setting('server_version'),
                       (SELECT extversion FROM pg_extension WHERE extname = 'vector'),
                       (SELECT count(*) FROM identity.match_policy_activation)
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("migrated template verification returned no row")
        server_version, vector_version, activation_count = row
        if not str(server_version).startswith("18.4") or vector_version != "0.8.6":
            raise RuntimeError("migrated template does not use PostgreSQL 18.4/pgvector 0.8.6")
        if activation_count != 0:
            raise RuntimeError("initial identity policy activation history is not empty")
        yield database_name
    finally:
        database_harness.drop_database(database_name)


@pytest.fixture
def database_name(
    database_harness: DatabaseHarness, migrated_template_database: str
) -> Iterator[str]:
    """Clone a fully migrated database for one test and always remove it."""
    name = database_harness.create_database(template=migrated_template_database)
    try:
        yield name
    finally:
        database_harness.drop_database(name)


@pytest.fixture
def database_url(database_harness: DatabaseHarness, database_name: str) -> str:
    """Return the isolated test database URL."""
    return database_harness.database_url(database_name)


@pytest.fixture
def database_connection(
    database_harness: DatabaseHarness, database_name: str
) -> Iterator[Connection[Any]]:
    """Open a bounded connection to an isolated migrated database."""
    connection = database_harness.connect(database_name)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def empty_database_name(database_harness: DatabaseHarness) -> Iterator[str]:
    """Create an empty database for migration or reference-DDL tests."""
    name = database_harness.create_database()
    try:
        yield name
    finally:
        database_harness.drop_database(name)


@pytest.fixture
def reference_database_name(database_harness: DatabaseHarness) -> Iterator[str]:
    """Execute the normative reference DDL in a fresh disposable database."""
    if not REFERENCE_DDL_PATH.is_file():
        raise RuntimeError(f"missing reference DDL: {REFERENCE_DDL_PATH}")
    name = database_harness.create_database()
    try:
        ddl = REFERENCE_DDL_PATH.read_text(encoding="utf-8")
        with database_harness.connect(name, autocommit=True) as connection:
            connection.execute(ddl, prepare=False)
        yield name
    finally:
        database_harness.drop_database(name)
