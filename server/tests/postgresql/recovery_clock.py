"""Advance expiry time only inside one guarded, disposable recovery-test database."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import psycopg
import pytest
from sqlalchemy import Engine, event, text

from .conftest import _guard_generated_name
from .test_self_device_pairing import PairingHarness


@dataclass(frozen=True)
class RecoveryClock:
    engine: Engine

    def advance(self, elapsed: timedelta) -> None:
        assert timedelta(0) < elapsed <= timedelta(days=3)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE recovery_test_clock.offset SET elapsed = elapsed + :elapsed"),
                {"elapsed": elapsed},
            )
            difference = connection.scalar(
                text("SELECT clock_timestamp() - pg_catalog.clock_timestamp()")
            )
            assert isinstance(difference, timedelta)
            assert abs(difference - elapsed) < timedelta(seconds=1)


def _set_clock_path(dbapi: object, record: object, proxy: object) -> None:
    assert isinstance(dbapi, psycopg.Connection)
    previous = dbapi.autocommit
    try:
        dbapi.autocommit = True
        with dbapi.cursor() as cursor:
            cursor.execute("SET SESSION search_path = recovery_test_clock, pg_catalog, public")
    finally:
        dbapi.autocommit = previous


@pytest.fixture
def recovery_clock(pair: PairingHarness) -> Iterator[RecoveryClock]:
    engine = pair.engine
    with engine.begin() as connection:
        name = connection.scalar(text("SELECT current_database()"))
        assert isinstance(name, str)
        _guard_generated_name(name)
        connection.execute(text("CREATE SCHEMA recovery_test_clock"))
        connection.execute(
            text("CREATE TABLE recovery_test_clock.offset (elapsed interval NOT NULL)")
        )
        connection.execute(text("INSERT INTO recovery_test_clock.offset VALUES (interval '0')"))
        connection.execute(
            text("""
            CREATE FUNCTION recovery_test_clock.clock_timestamp() RETURNS timestamptz
            LANGUAGE sql VOLATILE AS $$
                SELECT pg_catalog.clock_timestamp() + elapsed
                FROM recovery_test_clock.offset
            $$
        """)
        )
    # Checkout covers already-pooled connections as well as newly opened ones.
    # Production functions/triggers remain installed and enforce the shifted clock.
    event.listen(engine, "checkout", _set_clock_path)
    try:
        yield RecoveryClock(engine)
    finally:
        event.remove(engine, "checkout", _set_clock_path)
        engine.dispose()
        with engine.begin() as connection:
            connection.execute(text("DROP FUNCTION recovery_test_clock.clock_timestamp()"))
            connection.execute(text("DROP TABLE recovery_test_clock.offset"))
            connection.execute(text("DROP SCHEMA recovery_test_clock"))
