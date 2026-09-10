"""Exact contract tests for reconstructed normalized evidence from schema 0026."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from autplay.application.sona_source_acceptance import (
    build_sona_source_provenance_acceptance,
    derive_reconstructed_server_profile_id,
)
from autplay.application.sona_source_reconstruction import (
    Sona0026AcceptedEvent,
    classify_sona_0026_listening_outcome,
    normalize_sona_0026_event,
    normalize_sona_0026_events,
)
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalSignal,
)

OWNER = UUID("00000000-0000-7000-8000-000000000001")
DEVICE = UUID("00000000-0000-7000-8000-000000000002")
RECORDING = UUID("00000000-0000-7000-8000-000000000003")
REQUEST = UUID("00000000-0000-7000-8000-000000000004")
IMPRESSION = UUID("00000000-0000-7000-8000-000000000005")
LISTENING_EVENT = UUID("00000000-0000-7000-8000-000000000101")
DIMENSIONS = (
    TemporalDimension(DimensionKind.BOUNDED_METADATA_TOKEN, "rock"),
    TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, "artist-1"),
)
ACCEPTANCE = build_sona_source_provenance_acceptance(
    generation_id="0026-source-test",
    encrypted_archive_sha256="a" * 64,
    recorded_at_ms=1,
)


def _listening(
    *,
    source_event_id: UUID = LISTENING_EVENT,
    event_origin: str = "ORGANIC",
    played_ms: int = 60_000,
    completion_ratio: float | None = 0.9,
    excluded_from_taste: bool = False,
) -> Sona0026AcceptedEvent:
    recommendation_request_id = REQUEST if event_origin == "RECOMMENDED" else None
    impression_event_id = IMPRESSION if event_origin == "RECOMMENDED" else None
    recommendation_source_rank = 3 if event_origin == "RECOMMENDED" else None
    return Sona0026AcceptedEvent(
        source_event_id=source_event_id,
        owner_user_id=OWNER,
        device_id=DEVICE,
        device_sequence=11,
        server_sequence=21,
        source_event_type="LISTENING_EVENT_RECORDED",
        recording_id=RECORDING,
        dimensions=DIMENSIONS,
        occurred_at_ms=1_000,
        received_at_ms=2_000,
        source_request_sha256="b" * 64,
        event_origin=event_origin,
        played_ms=played_ms,
        completion_ratio=completion_ratio,
        excluded_from_taste=excluded_from_taste,
        recommendation_request_id=recommendation_request_id,
        impression_event_id=impression_event_id,
        recommendation_source_rank=recommendation_source_rank,
    )


def _preference(
    preference: str,
    *,
    excluded_from_taste: bool = False,
) -> Sona0026AcceptedEvent:
    return Sona0026AcceptedEvent(
        source_event_id=UUID("00000000-0000-7000-8000-000000000102"),
        owner_user_id=OWNER,
        device_id=DEVICE,
        device_sequence=12,
        server_sequence=22,
        source_event_type="USER_TRACK_PREFERENCE_SET",
        recording_id=RECORDING,
        dimensions=DIMENSIONS,
        occurred_at_ms=2_000,
        received_at_ms=3_000,
        source_request_sha256="c" * 64,
        preference=preference,
        excluded_from_taste=excluded_from_taste,
    )


def _feedback(feedback_type: str) -> Sona0026AcceptedEvent:
    return Sona0026AcceptedEvent(
        source_event_id=UUID("00000000-0000-7000-8000-000000000103"),
        owner_user_id=OWNER,
        device_id=DEVICE,
        device_sequence=13,
        server_sequence=23,
        source_event_type="RECOMMENDATION_FEEDBACK_RECORDED",
        recording_id=RECORDING,
        dimensions=DIMENSIONS,
        occurred_at_ms=3_000,
        received_at_ms=4_000,
        source_request_sha256="d" * 64,
        feedback_type=feedback_type,
        recommendation_request_id=REQUEST,
        impression_event_id=IMPRESSION,
        recommendation_source_rank=3,
    )


def test_default_runtime_policy_matches_frozen_contract_classifier_and_weights() -> None:
    policy = DEFAULT_ADAPTIVE_FEATURE_POLICY

    assert (
        policy.completion_threshold,
        policy.skip_completion_threshold,
        policy.skip_played_ms_threshold,
    ) == (0.8, 0.2, 30_000)
    assert dict(policy.signal_weights) == {
        TemporalSignal.EXPLICIT_LIKE: 1.0,
        TemporalSignal.FINALIZED_ORGANIC_LISTEN: 0.4,
        TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN: 0.2,
        TemporalSignal.RECOMMENDATION_SELECTED: 0.25,
        TemporalSignal.RECOMMENDATION_DISMISSED: -0.35,
        TemporalSignal.FINALIZED_COMPLETION: 0.3,
        TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP: -0.45,
    }


@pytest.mark.parametrize(
    ("excluded", "played_ms", "ratio", "expected"),
    (
        (True, 120_000, 1.0, TemporalSignal.EXCLUDE_FROM_TASTE),
        (False, 30_000, 0.2, TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP),
        (False, 29_999, 0.5, TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP),
        (False, 30_000, 0.5, None),
        (False, 30_000, 0.8, TemporalSignal.FINALIZED_COMPLETION),
        (False, 60_000, 0.79, None),
        (False, 29_999, None, TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP),
        (False, 60_000, None, None),
    ),
)
def test_outcome_classifier_matches_every_frozen_boundary(
    excluded: bool,
    played_ms: int,
    ratio: float | None,
    expected: TemporalSignal | None,
) -> None:
    event = _listening(
        excluded_from_taste=excluded,
        played_ms=played_ms,
        completion_ratio=ratio,
    )

    assert classify_sona_0026_listening_outcome(event) is expected


def test_recommended_completion_expands_with_exact_causal_group_and_weights() -> None:
    event = _listening(event_origin="RECOMMENDED")

    evidence = normalize_sona_0026_event(event, acceptance=ACCEPTANCE)

    assert {value.signal for value in evidence} == {
        TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN,
        TemporalSignal.FINALIZED_COMPLETION,
    }
    assert {value.signed_strength for value in evidence} == {0.2, 0.3}
    assert all(value.origin_lane is OriginLane.RECOMMENDATION for value in evidence)
    assert all(value.recommendation_request_id == REQUEST for value in evidence)
    assert all(value.impression_event_id == IMPRESSION for value in evidence)
    assert all(value.recommendation_source_rank == 3 for value in evidence)
    assert all(value.quality_weight == 1.0 for value in evidence)
    assert all(
        value.server_profile_id == derive_reconstructed_server_profile_id(ACCEPTANCE, OWNER)
        for value in evidence
    )


def test_preference_exclusion_wins_and_neutral_emits_no_stale_affinity() -> None:
    excluded = normalize_sona_0026_event(
        _preference("LIKED", excluded_from_taste=True),
        acceptance=ACCEPTANCE,
    )
    neutral = normalize_sona_0026_event(_preference("NEUTRAL"), acceptance=ACCEPTANCE)

    assert len(excluded) == 1
    assert excluded[0].signal is TemporalSignal.EXCLUDE_FROM_TASTE
    assert excluded[0].origin_lane is OriginLane.EXCLUSION
    assert excluded[0].signed_strength == excluded[0].quality_weight == 0.0
    assert neutral == ()


@pytest.mark.parametrize(
    ("feedback_type", "signal", "weight"),
    (
        ("SELECTED", TemporalSignal.RECOMMENDATION_SELECTED, 0.25),
        ("DISMISSED", TemporalSignal.RECOMMENDATION_DISMISSED, -0.35),
    ),
)
def test_feedback_requires_and_preserves_exact_impression_causality(
    feedback_type: str,
    signal: TemporalSignal,
    weight: float,
) -> None:
    evidence = normalize_sona_0026_event(_feedback(feedback_type), acceptance=ACCEPTANCE)

    assert len(evidence) == 1
    assert evidence[0].signal is signal
    assert evidence[0].signed_strength == weight
    assert evidence[0].recommendation_request_id == REQUEST
    assert evidence[0].impression_event_id == IMPRESSION


def test_batch_normalization_is_order_independent_and_deduplicates_exact_transport_retry() -> None:
    listen = _listening()
    preference = _preference("LIKED")

    first = normalize_sona_0026_events(
        (listen, preference, listen),
        acceptance=ACCEPTANCE,
    )
    second = normalize_sona_0026_events(
        (preference, listen),
        acceptance=ACCEPTANCE,
    )

    assert first == second
    assert len({value.evidence_id for value in first}) == len(first)


def test_batch_normalization_rejects_changed_source_under_same_transport_identity() -> None:
    listen = _listening()

    with pytest.raises(ValueError, match="source event identity conflict"):
        normalize_sona_0026_events(
            (listen, replace(listen, source_request_sha256="e" * 64)),
            acceptance=ACCEPTANCE,
        )


def test_recommended_event_without_complete_impression_link_is_rejected() -> None:
    with pytest.raises(ValueError, match="causal references are incomplete"):
        replace(_listening(event_origin="RECOMMENDED"), impression_event_id=None)
