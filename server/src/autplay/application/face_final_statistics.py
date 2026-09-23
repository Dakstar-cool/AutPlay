"""Frozen track bootstrap and transition primitives for Face final qualification.

The final report must retain all 10,000 draws and metric implementation hashes.
These functions have no authority to unseal fixtures or approve a model.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from autplay.application.face_bakeoff_metrics import (
    FaceAxisPointValues,
    FaceSegmentObservation,
    calculate_face_axis_point_values,
)

BOOTSTRAP_DRAWS = 10_000
AXIS_ONE_SIDED_ALPHA = Fraction(1, 120)
SINGLETON_ONE_SIDED_ALPHA = Fraction(1, 20)
BOOTSTRAP_DOMAIN = b"autplay.face.final-bootstrap.v1\0"
BOOTSTRAP_FAMILIES = frozenset(
    {"spearman", "balanced_accuracy", "ece", "transition_f1", "blinded_preference"}
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class FaceFinalStatisticsError(ValueError):
    """The frozen statistical protocol cannot be applied to these inputs."""


@dataclass(frozen=True, slots=True)
class FaceAxisBootstrapBounds:
    axis: str
    krippendorff_alpha: float
    point: FaceAxisPointValues
    spearman_lower: float
    balanced_accuracy_lower: float
    ece_upper: float
    spearman_draws: tuple[float, ...]
    balanced_accuracy_draws: tuple[float, ...]
    ece_draws: tuple[float, ...]

    @property
    def passed(self) -> bool:
        rho = self.point.spearman_rho
        balanced = self.point.directional_balanced_accuracy
        return (
            self.krippendorff_alpha >= 0.50
            and self.point.coverage >= 0.90
            and self.point.negative_reference_count >= 20
            and self.point.positive_reference_count >= 20
            and rho is not None
            and rho >= 0.45
            and self.spearman_lower > 0.20
            and balanced is not None
            and balanced >= 0.65
            and self.balanced_accuracy_lower > 0.55
            and self.point.ece <= 0.10
            and self.ece_upper <= 0.15
        )


@dataclass(frozen=True, slots=True)
class FaceTrackTransitions:
    track_id: str
    reference_ms: tuple[int, ...]
    prediction_ms: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FaceTransitionBootstrapBounds:
    reference_count: int
    reference_track_count: int
    point_f1: float
    lower_f1: float
    draws: tuple[float, ...]

    @property
    def passed(self) -> bool:
        return (
            self.reference_count >= 60
            and self.reference_track_count >= 10
            and self.point_f1 >= 0.60
            and self.lower_f1 > 0.50
        )


@dataclass(frozen=True, slots=True)
class FaceTrackPreferenceVotes:
    track_id: str
    votes: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class FacePreferenceBootstrapBounds:
    vote_count: int
    point_preference: float
    lower_preference: float
    draws: tuple[float, ...]

    @property
    def passed(self) -> bool:
        return self.point_preference >= 0.65 and self.lower_preference > 0.50


def face_bootstrap_seed(qualification_manifest_sha256: str, family_name: str) -> int:
    """Use the first unsigned big-endian 64 bits of the domain-separated hash."""

    if (
        type(qualification_manifest_sha256) is not str
        or _HEX.fullmatch(qualification_manifest_sha256) is None
        or type(family_name) is not str
        or family_name not in BOOTSTRAP_FAMILIES
    ):
        raise FaceFinalStatisticsError("invalid sealed bootstrap identity")
    digest = hashlib.sha256(
        BOOTSTRAP_DOMAIN
        + qualification_manifest_sha256.encode("ascii")
        + family_name.encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def face_track_block_draws(
    qualification_manifest_sha256: str,
    family_name: str,
    track_ids: tuple[str, ...],
) -> Iterator[tuple[int, ...]]:
    """Yield exactly 10,000 PCG64 track-resample index tuples in sealed order."""

    seed = face_bootstrap_seed(qualification_manifest_sha256, family_name)
    if (
        type(track_ids) is not tuple
        or not 15 <= len(track_ids) <= 1_000
        or any(type(value) is not str or not 1 <= len(value) <= 128 for value in track_ids)
        or len(set(track_ids)) != len(track_ids)
    ):
        raise FaceFinalStatisticsError("invalid sealed final track order")
    generator = np.random.Generator(np.random.PCG64(seed))
    for _ in range(BOOTSTRAP_DRAWS):
        yield tuple(int(value) for value in generator.integers(0, len(track_ids), len(track_ids)))


def face_empirical_type1_quantile(values: tuple[float, ...], probability: Fraction) -> float:
    """Return element max(1, ceil(p*10000)) in one-based sorted order."""

    if (
        type(values) is not tuple
        or len(values) != BOOTSTRAP_DRAWS
        or any(type(value) is not float or not math.isfinite(value) for value in values)
        or type(probability) is not Fraction
        or not 0 < probability < 1
    ):
        raise FaceFinalStatisticsError("invalid final bootstrap quantile")
    one_based = max(
        1,
        (probability.numerator * BOOTSTRAP_DRAWS + probability.denominator - 1)
        // probability.denominator,
    )
    return sorted(values)[one_based - 1]


def face_axis_bootstrap_bounds(
    qualification_manifest_sha256: str,
    track_ids: tuple[str, ...],
    observations: tuple[FaceSegmentObservation, ...],
    *,
    krippendorff_alpha: float,
) -> FaceAxisBootstrapBounds:
    """Calculate one axis's three preregistered one-sided bootstrap bounds.

    Reliability is supplied by the independent raw-rater collation authority;
    this pure primitive neither validates rater consent nor approves a model.
    """

    if (
        type(observations) is not tuple
        or type(track_ids) is not tuple
        or not 15 <= len(track_ids) <= min(1_000, 10_000 // 12)
        or len(set(track_ids)) != len(track_ids)
        or len(observations) != len(track_ids) * 12
        or any(not isinstance(row, FaceSegmentObservation) for row in observations)
        or type(krippendorff_alpha) is not float
        or not math.isfinite(krippendorff_alpha)
        or not -1.0 <= krippendorff_alpha <= 1.0
    ):
        raise FaceFinalStatisticsError("invalid final axis observations")
    axis = observations[0].axis
    if any(row.axis != axis for row in observations):
        raise FaceFinalStatisticsError("mixed final Face axes")
    grouped: dict[str, list[FaceSegmentObservation]] = {track_id: [] for track_id in track_ids}
    for row in observations:
        if row.track_id not in grouped:
            raise FaceFinalStatisticsError("observation outside sealed track order")
        grouped[row.track_id].append(row)
    if any(
        len(rows) != 12 or len({row.segment_id for row in rows}) != 12 for rows in grouped.values()
    ):
        raise FaceFinalStatisticsError("final track must contain twelve unique segments")
    blocks = tuple(tuple(grouped[track_id]) for track_id in track_ids)
    point = calculate_face_axis_point_values(observations)
    outputs: dict[str, tuple[float, ...]] = {}
    for family_name in ("spearman", "balanced_accuracy", "ece"):
        draw_values: list[float] = []
        for indices in face_track_block_draws(
            qualification_manifest_sha256, family_name, track_ids
        ):
            sampled = tuple(row for index in indices for row in blocks[index])
            values = calculate_face_axis_point_values(sampled)
            if family_name == "spearman":
                draw_values.append(values.spearman_rho if values.spearman_rho is not None else -1.0)
            elif family_name == "balanced_accuracy":
                draw_values.append(
                    values.directional_balanced_accuracy
                    if values.directional_balanced_accuracy is not None
                    else 0.0
                )
            else:
                draw_values.append(values.ece)
        outputs[family_name] = tuple(draw_values)
    return FaceAxisBootstrapBounds(
        axis=axis,
        krippendorff_alpha=krippendorff_alpha,
        point=point,
        spearman_lower=face_empirical_type1_quantile(outputs["spearman"], AXIS_ONE_SIDED_ALPHA),
        balanced_accuracy_lower=face_empirical_type1_quantile(
            outputs["balanced_accuracy"], AXIS_ONE_SIDED_ALPHA
        ),
        ece_upper=face_empirical_type1_quantile(outputs["ece"], 1 - AXIS_ONE_SIDED_ALPHA),
        spearman_draws=outputs["spearman"],
        balanced_accuracy_draws=outputs["balanced_accuracy"],
        ece_draws=outputs["ece"],
    )


def face_transition_match_counts(
    reference_ms: tuple[int, ...], prediction_ms: tuple[int, ...]
) -> tuple[int, int, int]:
    """Maximum-cardinality one-to-one matching of ordered times within 3 s.

    For points on a line with a fixed symmetric tolerance, advancing the
    earlier unmatched point when the pair is too far apart preserves a maximum
    matching; otherwise the pair is matched and both cursors advance.
    """

    for values in (reference_ms, prediction_ms):
        if (
            type(values) is not tuple
            or len(values) > 10_000
            or any(type(value) is not int or not 0 <= value <= 86_400_000 for value in values)
            or tuple(sorted(set(values))) != values
        ):
            raise FaceFinalStatisticsError("invalid transition time series")
    left = right = matched = 0
    while left < len(reference_ms) and right < len(prediction_ms):
        delta = reference_ms[left] - prediction_ms[right]
        if abs(delta) <= 3_000:
            matched += 1
            left += 1
            right += 1
        elif delta < 0:
            left += 1
        else:
            right += 1
    return matched, len(reference_ms), len(prediction_ms)


def face_micro_transition_f1(counts: tuple[tuple[int, int, int], ...]) -> float:
    """Micro F1 from matched, reference, and prediction counts by track."""

    if (
        type(counts) is not tuple
        or not 1 <= len(counts) <= 1_000
        or any(
            type(row) is not tuple
            or len(row) != 3
            or any(type(value) is not int or value < 0 for value in row)
            or row[0] > min(row[1], row[2])
            for row in counts
        )
    ):
        raise FaceFinalStatisticsError("invalid transition count rows")
    matched = sum(row[0] for row in counts)
    references = sum(row[1] for row in counts)
    predictions = sum(row[2] for row in counts)
    if references == 0:
        raise FaceFinalStatisticsError("insufficient reference transitions")
    return 2 * matched / (references + predictions)


def face_transition_bootstrap_bounds(
    qualification_manifest_sha256: str,
    track_ids: tuple[str, ...],
    tracks: tuple[FaceTrackTransitions, ...],
) -> FaceTransitionBootstrapBounds:
    """Track-block lower bound after the frozen positive-reference sufficiency gate."""

    if (
        type(tracks) is not tuple
        or not 15 <= len(tracks) <= 1_000
        or len(tracks) != len(track_ids)
        or any(not isinstance(row, FaceTrackTransitions) for row in tracks)
        or tuple(row.track_id for row in tracks) != track_ids
    ):
        raise FaceFinalStatisticsError("invalid final transition tracks")
    counts = tuple(
        face_transition_match_counts(row.reference_ms, row.prediction_ms) for row in tracks
    )
    reference_count = sum(row[1] for row in counts)
    reference_track_count = sum(row[1] > 0 for row in counts)
    if reference_count < 60 or reference_track_count < 10:
        raise FaceFinalStatisticsError("insufficient reference transitions")
    point = face_micro_transition_f1(counts)
    draws: list[float] = []
    for indices in face_track_block_draws(
        qualification_manifest_sha256, "transition_f1", track_ids
    ):
        sampled = tuple(counts[index] for index in indices)
        try:
            draws.append(face_micro_transition_f1(sampled))
        except FaceFinalStatisticsError:
            draws.append(0.0)
    values = tuple(draws)
    return FaceTransitionBootstrapBounds(
        reference_count,
        reference_track_count,
        point,
        face_empirical_type1_quantile(values, SINGLETON_ONE_SIDED_ALPHA),
        values,
    )


def face_preference_bootstrap_bounds(
    qualification_manifest_sha256: str,
    track_ids: tuple[str, ...],
    tracks: tuple[FaceTrackPreferenceVotes, ...],
) -> FacePreferenceBootstrapBounds:
    """One timeline-vs-shuffled vote per independent rater and track."""

    if (
        type(tracks) is not tuple
        or not 15 <= len(tracks) <= 1_000
        or len(tracks) != len(track_ids)
        or any(not isinstance(row, FaceTrackPreferenceVotes) for row in tracks)
        or tuple(row.track_id for row in tracks) != track_ids
        or any(
            type(row.votes) is not tuple
            or not 3 <= len(row.votes) <= 20
            or any(
                type(vote) is not tuple
                or len(vote) != 2
                or type(vote[0]) is not str
                or not 1 <= len(vote[0]) <= 128
                or type(vote[1]) is not bool
                for vote in row.votes
            )
            or len({vote[0] for vote in row.votes}) != len(row.votes)
            for row in tracks
        )
    ):
        raise FaceFinalStatisticsError("invalid blinded preference votes")
    blocks = tuple((sum(vote for _, vote in row.votes), len(row.votes)) for row in tracks)
    wins = sum(row[0] for row in blocks)
    votes = sum(row[1] for row in blocks)
    point = wins / votes
    draws: list[float] = []
    for indices in face_track_block_draws(
        qualification_manifest_sha256, "blinded_preference", track_ids
    ):
        sampled_wins = sum(blocks[index][0] for index in indices)
        sampled_votes = sum(blocks[index][1] for index in indices)
        draws.append(sampled_wins / sampled_votes)
    values = tuple(draws)
    return FacePreferenceBootstrapBounds(
        votes,
        point,
        face_empirical_type1_quantile(values, SINGLETON_ONE_SIDED_ALPHA),
        values,
    )
