"""Cross-runtime fixed-point score vectors for the future serving v2 cutover."""

from __future__ import annotations

import json
from decimal import Decimal
from math import inf, nextafter
from pathlib import Path
from uuid import UUID

import pytest

from autplay.application.recommendations import PreferenceCandidateGenerator
from autplay.domain.recommendations import SnapshotTrack
from autplay.domain.score_e8 import (
    SCORE_E8_MAX,
    SCORE_E8_MIN,
    ScoreE8Error,
    score_e8_decimal,
    score_e8_from_binary64,
    score_e8_json_number,
)

_VECTORS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ml" / "score-e8-v1.json"


def test_frozen_binary64_rounding_and_exact_json_number_vectors() -> None:
    fixture = json.loads(_VECTORS.read_text(encoding="utf-8"))
    assert (fixture["schema_version"], fixture["kind"]) == (1, "SCORE_E8_V1_GOLDEN")
    for case in fixture["cases"]:
        raw = float.fromhex(case["binary64_hex"])
        units = score_e8_from_binary64(raw)
        assert units == case["units"], case["name"]
        token = score_e8_json_number(units)
        assert token == case["json_number"], case["name"]
        assert Decimal(token) == score_e8_decimal(units)


@pytest.mark.parametrize("raw", [float("nan"), inf, -inf, 1e100, -1e100])
def test_nonfinite_and_out_of_range_scores_fail_closed(raw: float) -> None:
    with pytest.raises(ScoreE8Error):
        score_e8_from_binary64(raw)


def test_int64_edges_render_exactly_and_reject_invalid_units() -> None:
    assert score_e8_json_number(SCORE_E8_MIN) == "-92233720368.54775808"
    assert score_e8_json_number(SCORE_E8_MAX) == "92233720368.54775807"
    for value in (SCORE_E8_MIN - 1, SCORE_E8_MAX + 1, True, 1.0):
        with pytest.raises(ScoreE8Error):
            score_e8_json_number(value)  # type: ignore[arg-type]


def _track(number: int) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=UUID(int=number),
        user_track_ref_id=None,
        artist_key="artist",
        release_key=None,
        metadata_tokens=(),
        availability="VAULT",
        authorized=True,
        identity_status="ACTIVE",
        preference="LIKED",
        excluded=False,
        play_count=0,
        organic_play_count=0,
        recommended_play_count=0,
        last_played_at_ms=None,
        added_at_ms=0,
        release_date_ordinal=None,
    )


def test_legacy_generator_budget_selects_raw_score_before_e8_tie() -> None:
    low_id, high_id = _track(1), _track(2)
    raw_low, raw_high = 1.0, nextafter(1.0, inf)
    assert score_e8_from_binary64(raw_low) == score_e8_from_binary64(raw_high)
    selected = PreferenceCandidateGenerator()._batch(
        [(low_id, raw_low, {}), (high_id, raw_high, {})], 1
    )
    assert selected[0].recording_id == high_id.recording_id
    assert selected[0].contributions[0].raw_score == 1.0
