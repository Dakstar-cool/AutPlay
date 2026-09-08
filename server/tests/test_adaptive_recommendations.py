"""Focused R1B deterministic temporal-profile projection evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    DimensionKind,
    Horizon,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalProjectionError,
    TemporalSignal,
    TimeClassification,
    classify_event_time,
    maturity_from_observation,
    normalized_view_coefficients,
    project_temporal_profile,
)

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "recommendations" / "v1"
CUTOFF_MS = 1_788_375_600_000
OWNER = UUID("00000000-0000-7000-8000-000000000001")
PROFILE = UUID("00000000-0000-7000-8000-000000000011")
DEVICE = UUID("00000000-0000-7000-8000-000000000201")
RECORDING = UUID("00000000-0000-7000-8000-000000000301")
ARTIST = TemporalDimension(
    DimensionKind.CANONICAL_ARTIST_ID,
    "00000000-0000-7000-8000-000000000401",
)
HASH = "b" * 64


def _event(
    index: int,
    *,
    signal: TemporalSignal = TemporalSignal.FINALIZED_ORGANIC_LISTEN,
    origin: OriginLane = OriginLane.ORGANIC,
    strength: float = 0.4,
    effective_at_ms: int = CUTOFF_MS - 60_000,
    received_at_ms: int | None = None,
    server_sequence: int | None = None,
    classification: TimeClassification = TimeClassification.TRUSTED_EVENT_TIME,
    owner: UUID = OWNER,
    recommendation: bool = False,
) -> TemporalEvidence:
    received = effective_at_ms + 10_000 if received_at_ms is None else received_at_ms
    sequence = index if server_sequence is None else server_sequence
    source_event_id = UUID(f"00000000-0000-7000-8000-{index:012x}")
    evidence_id = UUID(f"00000000-0000-7000-9000-{index:012x}")
    return TemporalEvidence(
        evidence_id=evidence_id,
        source_event_id=source_event_id,
        owner_user_id=owner,
        server_profile_id=PROFILE,
        device_id=DEVICE,
        device_sequence=index,
        server_sequence=sequence,
        source_event_type=(
            "RECOMMENDATION_FEEDBACK_RECORDED"
            if signal
            in {TemporalSignal.RECOMMENDATION_SELECTED, TemporalSignal.RECOMMENDATION_DISMISSED}
            else "LISTENING_EVENT_RECORDED"
        ),
        signal=signal,
        derivation_key=(
            "RECOMMENDATION_FEEDBACK_V1"
            if signal
            in {TemporalSignal.RECOMMENDATION_SELECTED, TemporalSignal.RECOMMENDATION_DISMISSED}
            else "BASE_LISTEN_V1"
        ),
        recording_id=RECORDING,
        dimensions=(ARTIST,),
        occurred_at_ms=effective_at_ms,
        received_at_ms=received,
        effective_at_ms=effective_at_ms,
        time_classification=classification,
        origin_lane=origin,
        signed_strength=strength,
        quality_weight=1.0,
        excluded_from_taste=False,
        source_request_sha256=HASH,
        recommendation_request_id=(
            UUID("00000000-0000-7000-8000-000000000501") if recommendation else None
        ),
        impression_event_id=(
            UUID("00000000-0000-7000-8000-000000000502") if recommendation else None
        ),
        recommendation_source_rank=1 if recommendation else None,
    )


def test_event_time_classifier_matches_accepted_boundary_fixture() -> None:
    document = json.loads((FIXTURES / "event-time-boundaries.json").read_text(encoding="utf-8"))
    for case in document["cases"]:
        decision = classify_event_time(case["occurred_at_ms"], document["received_at_ms"])
        assert decision.effective_at_ms == case["expected_effective_at_ms"], case["case_id"]
        assert decision.classification == case["expected_classification"], case["case_id"]
        assert decision.recent_eligible is case["expected_recent_eligible"], case["case_id"]


def test_active_overlapping_views_conserve_one_event_mass() -> None:
    all_views = normalized_view_coefficients(tuple(Horizon))
    assert all_views == {
        Horizon.EPISODE: pytest.approx(0.30),
        Horizon.H12: pytest.approx(0.25),
        Horizon.H24: pytest.approx(0.20),
        Horizon.D3: pytest.approx(0.15),
        Horizon.D7: pytest.approx(0.10),
    }
    partial = normalized_view_coefficients((Horizon.D3, Horizon.D7))
    assert partial == {Horizon.D3: pytest.approx(0.6), Horizon.D7: pytest.approx(0.4)}
    assert sum(partial.values()) == pytest.approx(1.0)


def test_maturity_and_plasticity_match_accepted_cold_start_vector() -> None:
    scenario = json.loads((FIXTURES / "scenario-vectors.json").read_text(encoding="utf-8"))
    case = next(value for value in scenario["cases"] if value["case_id"].startswith("cold-start"))
    maturity = maturity_from_observation(**case["input"])
    assert maturity.score == pytest.approx(case["expected"]["maturity"])
    assert maturity.plasticity_multiplier == pytest.approx(
        case["expected"]["plasticity_multiplier"]
    )


def test_projection_is_owner_cutoff_watermark_scoped_and_input_order_independent() -> None:
    included_first = _event(1, effective_at_ms=CUTOFF_MS - 120_000)
    included_second = _event(2, effective_at_ms=CUTOFF_MS - 60_000)
    after_cutoff = _event(
        3,
        effective_at_ms=CUTOFF_MS - 30_000,
        received_at_ms=CUTOFF_MS + 1,
    )
    after_watermark = _event(4, server_sequence=101)
    first = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(after_watermark, included_second, after_cutoff, included_first),
    )
    second = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(included_first, after_cutoff, included_second, after_watermark),
    )
    assert first == second
    assert first.source_event_ids == (included_first.evidence_id, included_second.evidence_id)
    assert first.dimensions[0].source_event_ids == first.source_event_ids


def test_cross_owner_hash_conflict_and_local_unsynced_evidence_fail_closed() -> None:
    with pytest.raises(TemporalProjectionError, match="cross-owner"):
        project_temporal_profile(
            owner_user_id=OWNER,
            cutoff_at_ms=CUTOFF_MS,
            interaction_watermark=100,
            evidence=(_event(1, owner=UUID(int=9)),),
        )

    original = _event(2)
    conflict = replace(
        _event(3),
        source_event_id=original.source_event_id,
        source_request_sha256="a" * 64,
    )
    with pytest.raises(TemporalProjectionError, match="hash conflict"):
        project_temporal_profile(
            owner_user_id=OWNER,
            cutoff_at_ms=CUTOFF_MS,
            interaction_watermark=100,
            evidence=(original, conflict),
        )

    local = replace(
        _event(4),
        server_sequence=None,
        time_classification=TimeClassification.LOCAL_UNSYNCED_TIME,
    )
    profile = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(local,),
    )
    assert profile.source_event_ids == ()
    assert profile.dimensions == ()


def test_recommendation_causal_chain_is_bounded_and_never_becomes_organic_mass() -> None:
    selected = _event(
        10,
        signal=TemporalSignal.RECOMMENDATION_SELECTED,
        origin=OriginLane.RECOMMENDATION,
        strength=0.8,
        recommendation=True,
    )
    skipped = replace(
        _event(
            11,
            signal=TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
            origin=OriginLane.RECOMMENDATION,
            strength=-0.7,
            recommendation=True,
        ),
        source_event_id=selected.source_event_id,
        source_request_sha256=selected.source_request_sha256,
    )
    profile = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(selected, skipped),
    )
    assert profile.maturity.effective_signal_mass == pytest.approx(0.35)
    assert profile.maturity.organic_share == 0.0
    assert profile.dimensions[0].fatigue > 0.0


def test_temporary_skip_pressure_does_not_delete_durable_affinity() -> None:
    liked = replace(
        _event(
            20,
            signal=TemporalSignal.EXPLICIT_LIKE,
            origin=OriginLane.EXPLICIT,
            strength=1.0,
        ),
        source_event_type="USER_TRACK_PREFERENCE_SET",
        derivation_key="PREFERENCE_TRANSITION_V1",
    )
    skipped = _event(
        21,
        signal=TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
        origin=OriginLane.ORGANIC,
        strength=-0.45,
    )
    profile = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(liked, skipped),
    )
    dimension = profile.dimensions[0]
    assert dimension.long_term.score == 1.0
    assert dimension.long_term.evidence_mass == 1.0
    assert dimension.fatigue > 0.0
    assert dimension.recent_adjustment > -DEFAULT_ADAPTIVE_FEATURE_POLICY.total_absolute_cap


def test_old_negative_and_current_positive_evidence_create_positive_momentum() -> None:
    old_skip = _event(
        30,
        signal=TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
        strength=-0.45,
        effective_at_ms=CUTOFF_MS - 500_000_000,
    )
    current_completion = _event(
        31,
        signal=TemporalSignal.FINALIZED_COMPLETION,
        strength=0.3,
        effective_at_ms=CUTOFF_MS - 60_000,
    )
    profile = project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=100,
        evidence=(old_skip, current_completion),
    )
    assert profile.dimensions[0].momentum > 0.0
