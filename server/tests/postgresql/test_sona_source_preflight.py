"""Real PostgreSQL 0026 query and transaction-boundary evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol
from uuid import UUID

import pytest
from autplay.adapters.postgresql.sona_source_historical import (
    SqlAlchemySona0026HistoricalInputReader,
)
from autplay.adapters.postgresql.sona_source_planning import (
    SqlAlchemySonaSourcePlanningReader,
)
from autplay.adapters.postgresql.sona_source_preflight import (
    SqlAlchemySonaSourceAggregateReader,
)
from autplay.adapters.postgresql.sona_source_reconstruction import (
    SqlAlchemySona0026SourceEventReader,
)
from autplay.application.sona_source_acceptance import (
    build_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_preflight import SONA_SOURCE_EXPECTED_ALEMBIC_HEAD
from sqlalchemy import create_engine, text


class _DatabaseHarness(Protocol):
    def create_database(self, *, template: str | None = None) -> str: ...

    def upgrade(self, database_name: str, revision: str = "head") -> None: ...

    def database_url(self, database_name: str) -> str: ...

    def drop_database(self, database_name: str) -> None: ...


@contextmanager
def _revision_0026_database(harness: _DatabaseHarness) -> Iterator[str]:
    name = harness.create_database()
    try:
        harness.upgrade(name, SONA_SOURCE_EXPECTED_ALEMBIC_HEAD)
        yield harness.database_url(name)
    finally:
        harness.drop_database(name)


def test_empty_0026_restore_runs_the_complete_aggregate_query_and_fails_closed(
    database_harness: _DatabaseHarness,
) -> None:
    with _revision_0026_database(database_harness) as database_url:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    snapshot = SqlAlchemySonaSourceAggregateReader(connection).read(
                        source_captured_at_ms=1_788_260_694_001
                    )
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()

    assert snapshot.observed_alembic_head == SONA_SOURCE_EXPECTED_ALEMBIC_HEAD
    assert snapshot.transaction_read_only
    assert snapshot.counts.replay_request_count == 0
    assert snapshot.counts.fully_bound_request_count == 0


def test_0026_reader_refuses_default_read_write_transaction(
    database_harness: _DatabaseHarness,
) -> None:
    with _revision_0026_database(database_harness) as database_url:
        engine = create_engine(database_url)
        try:
            with (
                engine.connect() as connection,
                connection.begin(),
                pytest.raises(RuntimeError, match="not_read_only"),
            ):
                SqlAlchemySonaSourceAggregateReader(connection).read(
                    source_captured_at_ms=1_788_260_694_001
                )
        finally:
            engine.dispose()


def test_empty_0026_restore_runs_complete_owner_planning_query(
    database_harness: _DatabaseHarness,
) -> None:
    with _revision_0026_database(database_harness) as database_url:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    reader = SqlAlchemySonaSourcePlanningReader(connection)
                    observations = reader.read(source_captured_at_ms=1_788_260_694_001)
                    records = reader.read_records(source_captured_at_ms=1_788_260_694_001)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()

    assert observations == ()
    assert records == ()


def test_empty_0026_restore_runs_exact_owner_event_reconstruction_query(
    database_harness: _DatabaseHarness,
) -> None:
    with _revision_0026_database(database_harness) as database_url:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    events = SqlAlchemySona0026SourceEventReader(connection).read(
                        owner_user_id=UUID(int=1),
                        cutoff_at_ms=1_788_260_694_001,
                        interaction_watermark=0,
                    )
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()

    assert events == ()


def test_empty_0026_restore_assembles_no_historical_inputs(
    database_harness: _DatabaseHarness,
) -> None:
    with _revision_0026_database(database_harness) as database_url:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    cases = SqlAlchemySona0026HistoricalInputReader(connection).read(
                        source_captured_at_ms=1_788_260_694_001,
                        provenance_acceptance=build_sona_source_provenance_acceptance(
                            generation_id="empty-0026-test",
                            encrypted_archive_sha256="a" * 64,
                            recorded_at_ms=1_788_260_694_001,
                        ),
                    )
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()

    assert cases == ()
