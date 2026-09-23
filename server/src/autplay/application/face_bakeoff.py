"""Pure, deterministic development-only Face candidate selection.

Metric computation and final qualification remain separate gates. Inputs here are
the preregistered development point estimates, never final-set observations.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

FACE_BAKEOFF_AXES = (
    "POSITIVE_MELANCHOLIC",
    "CALM_ENERGETIC",
    "SOFT_AGGRESSIVE",
    "LIGHT_DARK",
    "RELAXED_TENSE",
    "DIRECT_ATMOSPHERIC",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_QUANTUM = Decimal("0.000001")
_MILLION = Decimal(1_000_000)


class FaceBakeoffError(ValueError):
    """The development comparison cannot be run on malformed metric reports."""


class FaceBakeoffOutcome(StrEnum):
    SELECT_FULL_CANDIDATE = "SELECT_FULL_CANDIDATE"
    SELECT_LIMITED_CANDIDATE = "SELECT_LIMITED_CANDIDATE"
    NO_DEVELOPMENT_CANDIDATE = "NO_DEVELOPMENT_CANDIDATE"


@dataclass(frozen=True, slots=True)
class FaceAxisDevelopmentMetrics:
    axis: str
    coverage: float
    spearman_rho: float
    directional_balanced_accuracy: float
    ece: float
    krippendorff_alpha: float
    negative_reference_count: int
    positive_reference_count: int

    def __post_init__(self) -> None:
        if self.axis not in FACE_BAKEOFF_AXES:
            raise FaceBakeoffError("unknown Face axis")
        for value, lower, upper in (
            (self.coverage, 0.0, 1.0),
            (self.spearman_rho, -1.0, 1.0),
            (self.directional_balanced_accuracy, 0.0, 1.0),
            (self.ece, 0.0, 1.0),
            (self.krippendorff_alpha, -1.0, 1.0),
        ):
            if type(value) is not float or not math.isfinite(value) or not lower <= value <= upper:
                raise FaceBakeoffError("invalid Face axis metric")
        if (
            type(self.negative_reference_count) is not int
            or type(self.positive_reference_count) is not int
            or not 0 <= self.negative_reference_count <= 1_000_000
            or not 0 <= self.positive_reference_count <= 1_000_000
        ):
            raise FaceBakeoffError("invalid Face directional reference counts")

    def margin_micro(self) -> int | None:
        """Return the canonical worst normalized margin for a valid axis dataset."""

        if (
            self.krippendorff_alpha < 0.50
            or self.negative_reference_count < 20
            or self.positive_reference_count < 20
            or self.coverage < 0.90
            or self.spearman_rho < 0.45
            or self.directional_balanced_accuracy < 0.65
            or self.ece > 0.10
        ):
            return None
        margins = (
            (self.coverage - 0.90) / 0.10,
            (self.spearman_rho - 0.45) / 0.55,
            (self.directional_balanced_accuracy - 0.65) / 0.35,
            (0.10 - self.ece) / 0.10,
        )
        return min(_micro(value) for value in margins)


@dataclass(frozen=True, slots=True)
class FaceDevelopmentCandidate:
    manifest_sha256: str
    tracks_per_hour: float
    axes: tuple[FaceAxisDevelopmentMetrics, ...]

    def __post_init__(self) -> None:
        if (
            type(self.manifest_sha256) is not str
            or _HEX.fullmatch(self.manifest_sha256) is None
            or type(self.tracks_per_hour) is not float
            or not math.isfinite(self.tracks_per_hour)
            or not 0.0 < self.tracks_per_hour <= 1_000_000_000.0
            or len(self.axes) != len(FACE_BAKEOFF_AXES)
            or any(not isinstance(value, FaceAxisDevelopmentMetrics) for value in self.axes)
            or {value.axis for value in self.axes} != set(FACE_BAKEOFF_AXES)
        ):
            raise FaceBakeoffError("invalid Face development candidate")


@dataclass(frozen=True, slots=True)
class FaceDevelopmentSelection:
    outcome: FaceBakeoffOutcome
    manifest_sha256: str | None
    passing_axes: tuple[str, ...]
    score_micro: int | None
    mean_ece_micro: int | None
    tracks_per_hour_micro: int | None


def select_face_development_candidate(
    candidates: tuple[FaceDevelopmentCandidate, ...],
) -> FaceDevelopmentSelection:
    """Apply the frozen full-first, then limited research, decision order."""

    if len(candidates) > 100 or len({c.manifest_sha256 for c in candidates}) != len(candidates):
        raise FaceBakeoffError("Face candidate set is duplicate or unbounded")
    ranked: list[tuple[int, int, int, int, str, tuple[str, ...]]] = []
    for candidate in candidates:
        if any(axis.krippendorff_alpha < 0.50 for axis in candidate.axes):
            raise FaceBakeoffError("Face annotation reliability requires repair")
        margins = {axis.axis: axis.margin_micro() for axis in candidate.axes}
        passing_names: list[str] = []
        passing_margins: list[int] = []
        for axis in FACE_BAKEOFF_AXES:
            margin = margins[axis]
            if margin is not None and margin >= 0:
                passing_names.append(axis)
                passing_margins.append(margin)
        passing = tuple(passing_names)
        full = len(passing) == 6
        limited = len(passing) >= 4 and any(
            axis in passing for axis in ("POSITIVE_MELANCHOLIC", "DIRECT_ATMOSPHERIC")
        )
        if not (full or limited):
            continue
        score = min(passing_margins)
        mean_ece = _micro(sum(axis.ece for axis in candidate.axes) / 6.0)
        throughput = _micro(candidate.tracks_per_hour)
        ranked.append(
            (0 if full else 1, -score, mean_ece, -throughput, candidate.manifest_sha256, passing)
        )
    if not ranked:
        return FaceDevelopmentSelection(
            FaceBakeoffOutcome.NO_DEVELOPMENT_CANDIDATE, None, (), None, None, None
        )
    tier, negative_score, mean_ece, negative_throughput, manifest, passing = min(ranked)
    return FaceDevelopmentSelection(
        FaceBakeoffOutcome.SELECT_FULL_CANDIDATE
        if tier == 0
        else FaceBakeoffOutcome.SELECT_LIMITED_CANDIDATE,
        manifest,
        passing,
        -negative_score,
        mean_ece,
        -negative_throughput,
    )


def _micro(value: float) -> int:
    if not math.isfinite(value):
        raise FaceBakeoffError("non-finite Face decision value")
    return int(Decimal.from_float(value).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN) * _MILLION)
