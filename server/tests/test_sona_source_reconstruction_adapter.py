"""Fail-closed adapter tests for exact schema-0026 owner event reconstruction."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast
from uuid import UUID

import pytest
from autplay.adapters.postgresql.sona_source_reconstruction import (
    SqlAlchemySona0026SourceEventReader,
)
from autplay.domain.adaptive_recommendations import DimensionKind
from sqlalchemy import Connection

OWNER = UUID("00000000-0000-7000-8000-000000000001")
DEVICE = UUID("00000000-0000-7000-8000-000000000002")
EVENT = UUID("00000000-0000-7000-8000-000000000003")
RECORDING = UUID("00000000-0000-7000-8000-000000000004")
REQUEST = UUID("00000000-0000-7000-8000-000000000005")
IMPRESSION = UUID("00000000-0000-7000-8000-000000000006")


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


def _listening_row(*, recommended: bool = False) -> dict[str, object]:
    recommendation: dict[str, object] | None = None
    if recommended:
        recommendation = {
            "recommendation_request_id": str(REQUEST),
            "recording_id": str(RECORDING),
            "source_rank": 4,
            "source": "cpu-baseline",
            "surface": "recommendations",
            "impression_event_local_id": str(IMPRESSION),
        }
    return {
        "event_id": EVENT,
        "user_id": OWNER,
        "device_id": DEVICE,
        "device_sequence": 7,
        "server_sequence": 9,
        "event_type": "LISTENING_EVENT_RECORDED",
        "payload": {
            "interaction_type": "LISTENING_EVENT_RECORDED",
            "recording_id": str(RECORDING),
            "event_origin": "RECOMMENDED" if recommended else "ORGANIC",
            "played_ms": 60_000,
            "completion_ratio": 0.9,
            "excluded_from_taste": False,
            "recommendation": recommendation,
        },
        "occurred_at_ms": 1_000,
        "received_at_ms": 2_000,
        "source_request_sha256": "a" * 64,
        "recording_id": RECORDING,
        "artist_key": "00000000-0000-7000-8000-000000000101",
        "release_key": "00000000-0000-7000-8000-000000000102",
        "preference": None,
        "event_origin": "RECOMMENDED" if recommended else "ORGANIC",
        "played_ms": 60_000,
        "completion_ratio": 0.9,
        "excluded_from_taste": False,
        "feedback_type": None,
        "recommendation_request_id": REQUEST if recommended else None,
        "impression_event_id": IMPRESSION if recommended else None,
        "recommendation_source_rank": 4 if recommended else None,
    }


def test_reader_refuses_wrong_schema_before_reading_owner_events() -> None:
    connection = _Connection(iter(("on", "repeatable read", "0025_other")))

    with pytest.raises(RuntimeError, match="schema_not_0026"):
        SqlAlchemySona0026SourceEventReader(cast(Connection, connection)).read(
            owner_user_id=OWNER,
            cutoff_at_ms=2_000,
            interaction_watermark=9,
        )

    assert not connection.execute_called


@pytest.mark.parametrize("recommended", (False, True))
def test_reader_parses_owner_bound_listening_and_exact_dimensions(recommended: bool) -> None:
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (_listening_row(recommended=recommended),),
    )

    events = SqlAlchemySona0026SourceEventReader(cast(Connection, connection)).read(
        owner_user_id=OWNER,
        cutoff_at_ms=2_000,
        interaction_watermark=9,
    )

    assert len(events) == 1
    assert events[0].owner_user_id == OWNER
    assert tuple(value.kind for value in events[0].dimensions) == (
        DimensionKind.CANONICAL_ARTIST_ID,
        DimensionKind.CANONICAL_RELEASE_ID,
    )
    assert events[0].recommendation_request_id == (REQUEST if recommended else None)
    assert events[0].impression_event_id == (IMPRESSION if recommended else None)


def test_reader_rejects_projection_payload_mismatch_without_leaking_row() -> None:
    row = _listening_row()
    payload = cast(dict[str, object], row["payload"])
    row["payload"] = {**payload, "played_ms": 1}
    connection = _Connection(
        iter(("on", "repeatable read", "0026_s1d_guest_room_access")),
        (row,),
    )

    with pytest.raises(RuntimeError, match="source_event_invalid"):
        SqlAlchemySona0026SourceEventReader(cast(Connection, connection)).read(
            owner_user_id=OWNER,
            cutoff_at_ms=2_000,
            interaction_watermark=9,
        )


def test_reader_rejects_read_write_or_non_repeatable_transaction() -> None:
    connection = _Connection(iter(("off", "read committed")))

    with pytest.raises(RuntimeError, match="not_read_only_repeatable_read"):
        SqlAlchemySona0026SourceEventReader(cast(Connection, connection)).read(
            owner_user_id=OWNER,
            cutoff_at_ms=2_000,
            interaction_watermark=9,
        )

    assert not connection.execute_called
