"""Component-appropriate database readiness tests."""

from __future__ import annotations

from typing import Any

import pytest
from autplay.adapters.postgresql.readiness import (
    EXPECTED_MIGRATION_HEAD,
    PostgreSQLReadinessProbe,
)
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError


def test_readiness_requires_exact_single_migration_head() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num text NOT NULL)"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": EXPECTED_MIGRATION_HEAD},
            )

        assert PostgreSQLReadinessProbe(engine).check().ready is True

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '0009_constraints_triggers'")
            )
        mismatch = PostgreSQLReadinessProbe(engine).check()
        assert mismatch.ready is False
        assert mismatch.code == "database_migration_mismatch"
    finally:
        engine.dispose()


def test_missing_migration_table_is_schema_mismatch() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        result = PostgreSQLReadinessProbe(engine).check()
    finally:
        engine.dispose()

    assert result.ready is False
    assert result.component == "postgresql_schema"
    assert result.code == "database_migration_mismatch"


def test_readiness_checks_connectivity_and_head_on_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num text NOT NULL)"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": EXPECTED_MIGRATION_HEAD},
            )
        original_connect = engine.connect
        connect_count = 0

        def connect_once() -> Connection:
            nonlocal connect_count
            connect_count += 1
            if connect_count > 1:
                raise AssertionError("readiness opened a second connection")
            return original_connect()

        monkeypatch.setattr(engine, "connect", connect_once)
        result = PostgreSQLReadinessProbe(engine).check()
    finally:
        engine.dispose()

    assert result.ready is True
    assert connect_count == 1


def test_transient_failure_during_head_query_is_database_unavailable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    def fail_head_query(
        connection: object,
        cursor: object,
        statement: str,
        parameters: Any,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, context, executemany
        if "alembic_version" in statement:
            raise OperationalError(statement, parameters, OSError("connection dropped"))

    event.listen(engine, "before_cursor_execute", fail_head_query)
    try:
        result = PostgreSQLReadinessProbe(engine).check()
    finally:
        engine.dispose()

    assert result.ready is False
    assert result.component == "postgresql"
    assert result.code == "database_unavailable"
