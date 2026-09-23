"""Deterministic development point metrics for preregistered Face segments.

This module computes no annotation reliability or final confidence bounds. A
separate rater authority must supply a valid ordinal-alpha result before its
outputs enter the development-only candidate selector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from autplay.application.face_bakeoff import (
    FACE_BAKEOFF_AXES,
    FaceAxisDevelopmentMetrics,
)

MAX_DEVELOPMENT_SEGMENTS = 10_000


class FacePointMetricError(ValueError):
    """Point metrics are undefined or the observation set is invalid."""


@dataclass(frozen=True, slots=True)
class FaceSegmentObservation:
    axis: str
    track_id: str
    segment_id: str
    reference_median: float
    model_value: float | None
    confidence: float

    def __post_init__(self) -> None:
        if (
            type(self.axis) is not str
            or self.axis not in FACE_BAKEOFF_AXES
            or type(self.track_id) is not str
            or type(self.segment_id) is not str
            or not 1 <= len(self.track_id) <= 128
            or not 1 <= len(self.segment_id) <= 128
            or type(self.reference_median) is not float
            or not math.isfinite(self.reference_median)
            or not -1.0 <= self.reference_median <= 1.0
            or type(self.confidence) is not float
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
            or (
                self.model_value is not None
                and (
                    type(self.model_value) is not float
                    or not math.isfinite(self.model_value)
                    or not -1.0 <= self.model_value <= 1.0
                )
            )
            or (self.model_value is None and self.confidence != 0.0)
        ):
            raise FacePointMetricError("invalid Face segment observation")


@dataclass(frozen=True, slots=True)
class FaceAxisPointValues:
    coverage: float
    spearman_rho: float | None
    directional_balanced_accuracy: float | None
    ece: float
    negative_reference_count: int
    positive_reference_count: int


def calculate_face_axis_point_values(
    observations: tuple[FaceSegmentObservation, ...],
) -> FaceAxisPointValues:
    """Shared point formulas; duplicate track blocks are valid bootstrap inputs."""

    if type(observations) is not tuple or not 2 <= len(observations) <= MAX_DEVELOPMENT_SEGMENTS:
        raise FacePointMetricError("Face segment count invalid")
    if any(not isinstance(row, FaceSegmentObservation) for row in observations):
        raise FacePointMetricError("invalid Face segment observation")
    axis = observations[0].axis
    if any(row.axis != axis for row in observations):
        raise FacePointMetricError("Face observations must share one axis")

    references = [row.reference_median for row in observations]
    predictions = [row.model_value if row.model_value is not None else 0.0 for row in observations]
    rho = _spearman(references, predictions)
    negative = 0
    positive = 0
    correct_negative = 0
    correct_positive = 0
    bins: list[list[float]] = [[] for _ in range(10)]
    for row, predicted in zip(observations, predictions, strict=True):
        abstained = row.model_value is None
        if row.reference_median <= -0.20:
            negative += 1
            correct = not abstained and predicted <= -0.15
            correct_negative += int(correct)
        elif row.reference_median >= 0.20:
            positive += 1
            correct = not abstained and predicted >= 0.15
            correct_positive += int(correct)
        else:
            correct = abs(predicted - row.reference_median) <= 0.20
        bin_number = min(int(row.confidence * 10), 9)
        bins[bin_number].append(float(correct))
    balanced_accuracy = (
        (correct_negative / negative + correct_positive / positive) / 2.0
        if negative and positive
        else None
    )
    count = len(observations)
    ece = math.fsum(
        len(values)
        / count
        * abs(math.fsum(values) / len(values) - _mean_confidence(observations, bin_number))
        for bin_number, values in enumerate(bins)
        if values
    )
    coverage = math.fsum(row.model_value is not None for row in observations) / count
    return FaceAxisPointValues(coverage, rho, balanced_accuracy, ece, negative, positive)


def calculate_face_development_axis_metrics(
    observations: tuple[FaceSegmentObservation, ...],
    *,
    krippendorff_alpha: float,
) -> FaceAxisDevelopmentMetrics:
    """Compute the frozen §6.2 point rules from one complete axis observation set.

    The caller must prove that observations contain every preregistered segment
    and that alpha was computed from retained independent raw ratings. Missing
    poles or a constant rank series are explicit undefined reports, never passes.
    """

    if type(observations) is not tuple or not 2 <= len(observations) <= MAX_DEVELOPMENT_SEGMENTS:
        raise FacePointMetricError("Face development segment count invalid")
    if any(not isinstance(row, FaceSegmentObservation) for row in observations):
        raise FacePointMetricError("invalid Face segment observation")
    axis = observations[0].axis
    if any(row.axis != axis for row in observations):
        raise FacePointMetricError("Face observations must share one axis")
    if len({(row.track_id, row.segment_id) for row in observations}) != len(observations):
        raise FacePointMetricError("duplicate Face development segment")
    if type(krippendorff_alpha) is not float or not math.isfinite(krippendorff_alpha):
        raise FacePointMetricError("invalid annotation reliability")

    values = calculate_face_axis_point_values(observations)
    if values.spearman_rho is None:
        raise FacePointMetricError("undefined Face Spearman point estimate")
    if values.directional_balanced_accuracy is None:
        raise FacePointMetricError("undefined Face directional balanced accuracy")
    return FaceAxisDevelopmentMetrics(
        axis=axis,
        coverage=values.coverage,
        spearman_rho=values.spearman_rho,
        directional_balanced_accuracy=values.directional_balanced_accuracy,
        ece=values.ece,
        krippendorff_alpha=krippendorff_alpha,
        negative_reference_count=values.negative_reference_count,
        positive_reference_count=values.positive_reference_count,
    )


def _mean_confidence(observations: tuple[FaceSegmentObservation, ...], bin_number: int) -> float:
    values = [
        row.confidence for row in observations if min(int(row.confidence * 10), 9) == bin_number
    ]
    return math.fsum(values) / len(values)


def _spearman(first: list[float], second: list[float]) -> float | None:
    first_ranks = _average_ranks(first)
    second_ranks = _average_ranks(second)
    first_mean = math.fsum(first_ranks) / len(first_ranks)
    second_mean = math.fsum(second_ranks) / len(second_ranks)
    first_delta = [value - first_mean for value in first_ranks]
    second_delta = [value - second_mean for value in second_ranks]
    first_sq = math.fsum(value * value for value in first_delta)
    second_sq = math.fsum(value * value for value in second_delta)
    if first_sq == 0.0 or second_sq == 0.0:
        return None
    rho = math.fsum(left * right for left, right in zip(first_delta, second_delta, strict=True))
    rho /= math.sqrt(first_sq * second_sq)
    return min(1.0, max(-1.0, rho))


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[ordered[position]] = rank
        cursor = end
    return ranks
