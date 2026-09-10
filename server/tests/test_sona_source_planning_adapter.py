"""Fail-closed adapter tests for transient source-planning observations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

import pytest
import rfc8785
from autplay.adapters.postgresql.sona_source_planning import (
    SqlAlchemySonaSourcePlanningReader,
)
from autplay.domain.recommendations import JsonValue
from sqlalchemy import Connection


class _Rows:
    def __init__(self, rows: tuple[Mapping[str, object], ...]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def __iter__(self) -> Iterator[Mapping[str, object]]:
        return iter(self._rows)


class _Connection:
    def __init__(
        self,
        scalars: Iterator[object],
        rows: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        self._scalars = scalars
        self._rows = rows
        self.execute_called = False

    def scalar(self, _statement: object) -> object:
        return next(self._scalars)

    def execute(self, _statement: object, _parameters: object = None) -> Any:
        self.execute_called = True
        return _Rows(self._rows)


def test_reader_refuses_wrong_schema_before_reading_owner_rows() -> None:
    connection = _Connection(iter(("on", "repeatable read", "0025_other")))
    reader = SqlAlchemySonaSourcePlanningReader(cast(Connection, connection))

    with pytest.raises(RuntimeError, match="schema_not_0026"):
        reader.read(source_captured_at_ms=1)

    assert not connection.execute_called


def test_reader_parses_exact_p11_identity_and_owner_observation() -> None:
    owner = UUID("00000000-0000-7000-8000-000000000001")
    request_document: dict[str, JsonValue] = {"schema_version": 1, "owner": str(owner)}
    snapshot_document: dict[str, JsonValue] = {"schema_version": 1, "tracks": []}
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (
            {
                "request_sha256": sha256(rfc8785.dumps(request_document)).hexdigest(),
                "request_document": request_document,
                "input_snapshot_sha256": sha256(rfc8785.dumps(snapshot_document)).hexdigest(),
                "snapshot_document": snapshot_document,
                "user_id": owner,
                "cutoff_at_ms": 1,
                "observed_at_ms": 2,
            },
        ),
    )

    observations = SqlAlchemySonaSourcePlanningReader(cast(Connection, connection)).read(
        source_captured_at_ms=3
    )

    assert len(observations) == 1
    assert observations[0].request_sha256 == sha256(rfc8785.dumps(request_document)).hexdigest()
    assert observations[0].owner_user_id == owner


def test_reader_returns_transient_exact_snapshot_and_outcome_record() -> None:
    owner = UUID("00000000-0000-7000-8000-000000000001")
    snapshot_id = UUID("00000000-0000-7000-8000-000000000002")
    outcome_id = UUID("00000000-0000-7000-8000-000000000003")
    recording_id = UUID("00000000-0000-7000-8000-000000000004")
    request_document: dict[str, JsonValue] = {"schema_version": 1, "owner": str(owner)}
    snapshot_document: dict[str, JsonValue] = {"schema_version": 1, "tracks": []}
    request_hash = sha256(rfc8785.dumps(request_document)).hexdigest()
    snapshot_hash = sha256(rfc8785.dumps(snapshot_document)).hexdigest()
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (
            {
                "request_sha256": request_hash,
                "request_document": request_document,
                "input_snapshot_sha256": snapshot_hash,
                "snapshot_document": snapshot_document,
                "baseline_snapshot_id": snapshot_id,
                "interaction_watermark": 7,
                "catalog_snapshot": 8,
                "availability_snapshot": "availability",
                "policy_snapshot_sha256": "a" * 64,
                "retained_until": datetime(2026, 9, 30, tzinfo=UTC),
                "user_id": owner,
                "cutoff_at_ms": 1,
                "observed_at_ms": 2,
                "outcome_source_event_id": outcome_id,
                "outcome_source_request_sha256": "b" * 64,
                "outcome_recording_id": recording_id,
                "outcome_played_ms": 30_000,
                "outcome_completion_ratio": 0.8,
                "outcome_excluded_from_taste": False,
            },
        ),
    )

    records = SqlAlchemySonaSourcePlanningReader(cast(Connection, connection)).read_records(
        source_captured_at_ms=3
    )

    assert len(records) == 1
    assert records[0].observation.request_sha256 == request_hash
    assert records[0].baseline_snapshot_id == snapshot_id
    assert records[0].outcome_source_event_id == outcome_id
    assert records[0].outcome_recording_id == recording_id


def test_reader_rejects_invalid_result_without_leaking_row_content() -> None:
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (
            {
                "request_sha256": "not-a-hash",
                "request_document": {},
                "input_snapshot_sha256": sha256(rfc8785.dumps({})).hexdigest(),
                "snapshot_document": {},
                "user_id": UUID("00000000-0000-7000-8000-000000000001"),
                "cutoff_at_ms": 1,
                "observed_at_ms": 2,
            },
        ),
    )

    with pytest.raises(RuntimeError, match="planning_result_invalid"):
        SqlAlchemySonaSourcePlanningReader(cast(Connection, connection)).read(
            source_captured_at_ms=3
        )


def test_reader_rejects_tampered_canonical_document() -> None:
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (
            {
                "request_sha256": "a" * 64,
                "request_document": {"tampered": True},
                "input_snapshot_sha256": sha256(rfc8785.dumps({})).hexdigest(),
                "snapshot_document": {},
                "user_id": UUID("00000000-0000-7000-8000-000000000001"),
                "cutoff_at_ms": 1,
                "observed_at_ms": 2,
            },
        ),
    )

    with pytest.raises(RuntimeError, match="canonical_hash_mismatch"):
        SqlAlchemySonaSourcePlanningReader(cast(Connection, connection)).read(
            source_captured_at_ms=3
        )
