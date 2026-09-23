"""Frozen development selector cannot promote limited axes to a final Face candidate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from autplay.application.face_bakeoff import (
    FACE_BAKEOFF_AXES,
    FaceAxisDevelopmentMetrics,
    FaceBakeoffError,
    FaceBakeoffOutcome,
    FaceDevelopmentCandidate,
    select_face_development_candidate,
)


def _axes(*, coverage: float = 0.91, ece: float = 0.05) -> tuple[FaceAxisDevelopmentMetrics, ...]:
    return tuple(
        FaceAxisDevelopmentMetrics(axis, coverage, 0.60, 0.75, ece, 0.60, 25, 25)
        for axis in FACE_BAKEOFF_AXES
    )


def _candidate(
    digest: str,
    *,
    axes: tuple[FaceAxisDevelopmentMetrics, ...] | None = None,
    ece: float = 0.05,
    throughput: float = 100.0,
) -> FaceDevelopmentCandidate:
    return FaceDevelopmentCandidate(digest * 64, throughput, axes or _axes(ece=ece))


def test_six_axis_candidate_wins_before_limited_research_candidate() -> None:
    limited_axes = tuple(
        replace(axis, coverage=0.80) if axis.axis in FACE_BAKEOFF_AXES[4:] else axis
        for axis in _axes(coverage=0.95)
    )
    limited = _candidate("a", axes=limited_axes, throughput=500.0)
    full = _candidate("b")
    result = select_face_development_candidate((limited, full))
    assert result.outcome is FaceBakeoffOutcome.SELECT_FULL_CANDIDATE
    assert result.manifest_sha256 == "b" * 64
    assert result.passing_axes == FACE_BAKEOFF_AXES
    assert result.score_micro == 100_000


def test_limited_requires_four_passing_axes_and_semantic_anchor() -> None:
    four = tuple(
        replace(axis, coverage=0.80) if axis.axis in FACE_BAKEOFF_AXES[4:] else axis
        for axis in _axes()
    )
    result = select_face_development_candidate((_candidate("a", axes=four),))
    assert result.outcome is FaceBakeoffOutcome.SELECT_LIMITED_CANDIDATE
    assert result.passing_axes == FACE_BAKEOFF_AXES[:4]

    three = tuple(
        replace(axis, coverage=0.80) if axis.axis in FACE_BAKEOFF_AXES[3:] else axis
        for axis in _axes()
    )
    assert select_face_development_candidate((_candidate("b", axes=three),)).outcome is (
        FaceBakeoffOutcome.NO_DEVELOPMENT_CANDIDATE
    )
    no_anchor = tuple(
        replace(axis, coverage=0.80)
        if axis.axis in (FACE_BAKEOFF_AXES[0], FACE_BAKEOFF_AXES[5])
        else axis
        for axis in _axes()
    )
    assert select_face_development_candidate((_candidate("c", axes=no_anchor),)).outcome is (
        FaceBakeoffOutcome.NO_DEVELOPMENT_CANDIDATE
    )


def test_ties_use_mean_ece_then_throughput_then_manifest_hash() -> None:
    low_ece = _candidate("c", ece=0.04, throughput=50.0)
    high_ece = _candidate("a", ece=0.05, throughput=500.0)
    assert select_face_development_candidate((high_ece, low_ece)).manifest_sha256 == "c" * 64
    assert (
        select_face_development_candidate(
            (_candidate("c", throughput=50.0), _candidate("b", throughput=100.0))
        ).manifest_sha256
        == "b" * 64
    )
    assert (
        select_face_development_candidate((_candidate("c"), _candidate("a"))).manifest_sha256
        == "a" * 64
    )


def test_floor_is_checked_before_rounding_and_invalid_annotations_stop_comparison() -> None:
    near_floor = tuple(replace(axis, coverage=0.8999999999) for axis in _axes())
    assert select_face_development_candidate((_candidate("a", axes=near_floor),)).outcome is (
        FaceBakeoffOutcome.NO_DEVELOPMENT_CANDIDATE
    )
    unreliable = tuple(replace(axis, krippendorff_alpha=0.49) for axis in _axes())
    with pytest.raises(FaceBakeoffError, match="annotation reliability"):
        select_face_development_candidate((_candidate("b", axes=unreliable),))


def test_nonfinite_metrics_and_duplicate_candidate_identity_reject() -> None:
    with pytest.raises(FaceBakeoffError):
        replace(_axes()[0], ece=float("nan"))
    with pytest.raises(FaceBakeoffError):
        _candidate("a", throughput=float("inf"))
    with pytest.raises(FaceBakeoffError, match="duplicate"):
        select_face_development_candidate((_candidate("a"), _candidate("a")))
