"""Golden statistical primitives before any final fixture is unsealed."""

from fractions import Fraction
from itertools import islice

import pytest

from autplay.application.face_bakeoff_metrics import FaceSegmentObservation
from autplay.application.face_final_statistics import (
    AXIS_ONE_SIDED_ALPHA,
    FaceFinalStatisticsError,
    FaceTrackPreferenceVotes,
    FaceTrackTransitions,
    face_axis_bootstrap_bounds,
    face_bootstrap_seed,
    face_empirical_type1_quantile,
    face_micro_transition_f1,
    face_preference_bootstrap_bounds,
    face_track_block_draws,
    face_transition_bootstrap_bounds,
    face_transition_match_counts,
)


def test_bootstrap_seed_and_track_draws_are_deterministic() -> None:
    manifest = "0" * 64
    tracks = tuple(f"track-{index}" for index in range(15))
    first = tuple(islice(face_track_block_draws(manifest, "spearman", tracks), 2))
    repeated = tuple(islice(face_track_block_draws(manifest, "spearman", tracks), 2))
    assert first == repeated
    assert face_bootstrap_seed(manifest, "spearman") == 13199858542458043024
    assert first[0] == (2, 5, 8, 6, 7, 10, 14, 2, 6, 12, 0, 1, 14, 13, 5)
    assert len(first) == 2 and all(len(row) == 15 for row in first)
    assert face_bootstrap_seed(manifest, "spearman") != face_bootstrap_seed(manifest, "ece")


def test_type1_quantile_uses_exact_bonferroni_index() -> None:
    values = tuple(float(index) for index in range(10_000))
    assert face_empirical_type1_quantile(values, AXIS_ONE_SIDED_ALPHA) == 83.0
    assert face_empirical_type1_quantile(values, 1 - AXIS_ONE_SIDED_ALPHA) == 9916.0
    assert face_empirical_type1_quantile(values, Fraction(1, 20)) == 499.0


def test_transition_matching_is_one_to_one_at_closed_three_second_boundary() -> None:
    counts = face_transition_match_counts((0, 6_000, 20_000), (3_000, 9_000, 20_001, 40_000))
    assert counts == (3, 3, 4)
    assert face_micro_transition_f1((counts, (1, 2, 1))) == pytest.approx(8 / 10)


def test_empty_reference_cannot_make_vacuous_pass() -> None:
    with pytest.raises(FaceFinalStatisticsError, match="insufficient"):
        face_micro_transition_f1(((0, 0, 0),))


def test_duplicate_transition_marks_fail_closed() -> None:
    with pytest.raises(FaceFinalStatisticsError, match="invalid"):
        face_transition_match_counts((1, 1), (1,))


def test_perfect_axis_uses_all_track_blocks_and_three_simultaneous_families() -> None:
    tracks = tuple(f"track-{index}" for index in range(15))
    references = (-1.0, -0.8, -0.6, -0.4, -0.1, -0.05, 0.05, 0.1, 0.4, 0.6, 0.8, 1.0)
    rows = tuple(
        FaceSegmentObservation(
            "POSITIVE_MELANCHOLIC", track, f"segment-{position}", reference, reference, 1.0
        )
        for track in tracks
        for position, reference in enumerate(references)
    )
    result = face_axis_bootstrap_bounds("a" * 64, tracks, rows, krippendorff_alpha=1.0)
    assert result.passed
    assert result.point.negative_reference_count == 60
    assert result.point.positive_reference_count == 60
    assert result.spearman_lower == 1.0
    assert result.balanced_accuracy_lower == 1.0
    assert result.ece_upper == 0.0
    assert (
        len(result.spearman_draws)
        == len(result.balanced_accuracy_draws)
        == len(result.ece_draws)
        == 10_000
    )


def test_final_axis_requires_every_preregistered_segment() -> None:
    tracks = tuple(f"track-{index}" for index in range(15))
    with pytest.raises(FaceFinalStatisticsError, match="invalid final axis"):
        face_axis_bootstrap_bounds("a" * 64, tracks, (), krippendorff_alpha=1.0)


def test_transition_and_blinded_preference_singleton_families() -> None:
    tracks = tuple(f"track-{index}" for index in range(15))
    transitions = tuple(
        FaceTrackTransitions(
            track, (10_000, 20_000, 30_000, 40_000), (10_000, 20_000, 30_000, 40_000)
        )
        for track in tracks
    )
    transition_result = face_transition_bootstrap_bounds("a" * 64, tracks, transitions)
    assert transition_result.passed
    assert transition_result.reference_count == 60
    assert transition_result.lower_f1 == 1.0
    assert len(transition_result.draws) == 10_000

    preferences = tuple(
        FaceTrackPreferenceVotes(track, (("rater-1", True), ("rater-2", True), ("rater-3", True)))
        for track in tracks
    )
    preference_result = face_preference_bootstrap_bounds("a" * 64, tracks, preferences)
    assert preference_result.passed
    assert preference_result.vote_count == 45
    assert preference_result.lower_preference == 1.0
    assert len(preference_result.draws) == 10_000


def test_transition_reference_sufficiency_does_not_pass_vacuously() -> None:
    tracks = tuple(f"track-{index}" for index in range(15))
    rows = tuple(FaceTrackTransitions(track, (10_000,), (10_000,)) for track in tracks)
    with pytest.raises(FaceFinalStatisticsError, match="insufficient reference"):
        face_transition_bootstrap_bounds("a" * 64, tracks, rows)
