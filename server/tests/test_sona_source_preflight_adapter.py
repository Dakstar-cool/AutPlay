"""Fail-closed adapter boundary tests for source preflight."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from autplay.adapters.postgresql.sona_source_preflight import (
    SqlAlchemySonaSourceAggregateReader,
)
from sqlalchemy import Connection


class _ScalarOnlyConnection:
    def __init__(self, values: Iterator[object]) -> None:
        self._values = values
        self.execute_called = False

    def scalar(self, _statement: object) -> object:
        return next(self._values)

    def execute(self, _statement: object, _parameters: object = None) -> Any:
        self.execute_called = True
        raise AssertionError("aggregate query must not run")


def test_reader_refuses_non_read_only_transaction_before_any_aggregate_query() -> None:
    connection = _ScalarOnlyConnection(iter(("off", "repeatable read")))
    reader = SqlAlchemySonaSourceAggregateReader(cast(Connection, connection))

    with pytest.raises(RuntimeError, match="not_read_only"):
        reader.read(source_captured_at_ms=1)

    assert not connection.execute_called


def test_reader_returns_zero_counts_for_an_unsupported_schema_without_querying_tables() -> None:
    connection = _ScalarOnlyConnection(iter(("on", "repeatable read", "0025_other")))
    reader = SqlAlchemySonaSourceAggregateReader(cast(Connection, connection))

    snapshot = reader.read(source_captured_at_ms=1)

    assert snapshot.observed_alembic_head == "0025_other"
    assert snapshot.counts.replay_request_count == 0
    assert not connection.execute_called
