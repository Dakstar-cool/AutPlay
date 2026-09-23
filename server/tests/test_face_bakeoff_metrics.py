"""Point metrics preserve the frozen abstention, pole and confidence rules."""

from __future__ import annotations

from dataclasses import replace

import pytest

from autplay.application.face_bakeoff_metrics import (
    FacePointMetricError,
    FaceSegmentObservation,
    calculate_face_development_axis_metrics,
)

AXIS = "POSITIVE_MELANCHOLIC"


def _observation(
    index: int,
    reference: float,
    predicted: float | None,
    confidence: float,
) -> FaceSegmentObservation:
    return FaceSegmentObservation(
        AXIS, "track-a", f"segment-{index}", reference, predicted, confidence
    )


def test_perfect_directional_points_and_tied_spearman_ranks() -> None:
    rows = tuple(
        _observation(index, -1.0 if index < 20 else 1.0, -0.8 if index < 20 else 0.8, 1.0)
        for index in range(40)
    )
    metrics = calculate_face_development_axis_metrics(rows, krippendorff_alpha=0.75)
    assert metrics.coverage == 1.0
    assert metrics.spearman_rho == pytest.approx(1.0)
    assert metrics.directional_balanced_accuracy == 1.0
    assert metrics.ece == 0.0
    assert (metrics.negative_reference_count, metrics.positive_reference_count) == (20, 20)


def test_abstention_is_zero_for_rank_and_ece_and_wrong_for_direction() -> None:
    rows = (
        _observation(0, -1.0, -0.8, 1.0),
        _observation(1, -1.0, None, 0.0),
        _observation(2, 1.0, 0.8, 0.9),
        _observation(3, 1.0, -0.8, 0.9),
    )
    metrics = calculate_face_development_axis_metrics(rows, krippendorff_alpha=0.6)
    assert metrics.coverage == 0.75
    assert metrics.directional_balanced_accuracy == 0.5
    assert metrics.ece == pytest.approx(0.2)


def test_neutral_abstention_counts_as_ece_correct_under_absolute_error_rule() -> None:
    rows = (
        _observation(0, -1.0, -0.7, 1.0),
        _observation(1, 0.0, None, 0.0),
        _observation(2, 1.0, 0.7, 1.0),
    )
    metrics = calculate_face_development_axis_metrics(rows, krippendorff_alpha=0.6)
    assert metrics.ece == pytest.approx(1 / 3)
    assert metrics.coverage == pytest.approx(2 / 3)


def test_undefined_or_malformed_inputs_fail_explicitly() -> None:
    rows = (_observation(0, -1.0, -0.8, 0.9), _observation(1, 1.0, 0.8, 0.9))
    with pytest.raises(FacePointMetricError, match="duplicate"):
        calculate_face_development_axis_metrics((rows[0], rows[0]), krippendorff_alpha=0.6)
    with pytest.raises(FacePointMetricError, match="Spearman"):
        calculate_face_development_axis_metrics(
            (replace(rows[0], model_value=0.0), replace(rows[1], model_value=0.0)),
            krippendorff_alpha=0.6,
        )
    with pytest.raises(FacePointMetricError, match="balanced accuracy"):
        calculate_face_development_axis_metrics(
            (rows[0], replace(rows[1], reference_median=0.0)), krippendorff_alpha=0.6
        )
    with pytest.raises(FacePointMetricError, match="invalid Face segment"):
        replace(rows[0], model_value=None)
    with pytest.raises(FacePointMetricError, match="invalid Face segment"):
        replace(rows[0], confidence=float("nan"))
