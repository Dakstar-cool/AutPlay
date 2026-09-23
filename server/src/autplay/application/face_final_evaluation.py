"""Pure final Face technical evaluation over one sealed candidate and rater corpus.

This module cannot establish fixture/rater legal authority, approve artifacts,
or unseal a qualification set. The operator report must retain all bootstrap
draws and implementation hashes before any signed approval.
"""

from __future__ import annotations

from dataclasses import dataclass

from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES
from autplay.application.face_bakeoff_metrics import FaceSegmentObservation
from autplay.application.face_final_statistics import (
    FaceAxisBootstrapBounds,
    FaceFinalStatisticsError,
    FacePreferenceBootstrapBounds,
    FaceTrackPreferenceVotes,
    FaceTrackTransitions,
    FaceTransitionBootstrapBounds,
    face_axis_bootstrap_bounds,
    face_preference_bootstrap_bounds,
    face_transition_bootstrap_bounds,
)
from autplay.application.face_rater_corpus import FaceRaterCorpusReference


class FaceFinalEvaluationError(ValueError):
    """A purported final observation differs from the sealed rater reference."""


@dataclass(frozen=True, slots=True)
class FaceFinalCandidateInput:
    candidate_manifest_sha256: str
    observations_by_axis: tuple[tuple[FaceSegmentObservation, ...], ...]
    transitions: tuple[FaceTrackTransitions, ...]
    preference_votes: tuple[FaceTrackPreferenceVotes, ...]


@dataclass(frozen=True, slots=True)
class FaceFinalTechnicalReport:
    qualification_manifest_sha256: str
    fixture_manifest_sha256: str
    candidate_manifest_sha256: str
    axis_bounds: tuple[FaceAxisBootstrapBounds, ...]
    transition_bounds: FaceTransitionBootstrapBounds
    preference_bounds: FacePreferenceBootstrapBounds

    @property
    def technical_pass(self) -> bool:
        return (
            len(self.axis_bounds) == len(FACE_BAKEOFF_AXES)
            and all(bound.passed for bound in self.axis_bounds)
            and self.transition_bounds.passed
            and self.preference_bounds.passed
        )


def validate_face_final_candidate(
    corpus: FaceRaterCorpusReference, candidate: FaceFinalCandidateInput
) -> tuple[str, ...]:
    """Require every axis/segment/transition/voter to match sealed raw-rater ancestry."""

    if (
        type(corpus) is not FaceRaterCorpusReference
        or type(candidate) is not FaceFinalCandidateInput
        or type(candidate.candidate_manifest_sha256) is not str
        or len(candidate.candidate_manifest_sha256) != 64
        or any(char not in "0123456789abcdef" for char in candidate.candidate_manifest_sha256)
        or type(candidate.observations_by_axis) is not tuple
        or len(candidate.observations_by_axis) != len(FACE_BAKEOFF_AXES)
        or type(candidate.transitions) is not tuple
        or type(candidate.preference_votes) is not tuple
        or len(candidate.transitions) != len(corpus.tracks)
        or len(candidate.preference_votes) != len(corpus.tracks)
        or tuple(axis for axis, _ in corpus.ordinal_alpha_by_axis) != FACE_BAKEOFF_AXES
    ):
        raise FaceFinalEvaluationError("incomplete final Face candidate")
    track_ids = tuple(track.track_id for track in corpus.tracks)
    if len(track_ids) < 15 or len(set(track_ids)) != len(track_ids):
        raise FaceFinalEvaluationError("invalid final Face track ancestry")
    for axis_index, axis in enumerate(FACE_BAKEOFF_AXES):
        observations = candidate.observations_by_axis[axis_index]
        expected = tuple(
            (track.track_id, segment.segment_id, segment.axis_medians[axis_index])
            for track in corpus.tracks
            for segment in track.segments
        )
        if (
            type(observations) is not tuple
            or len(observations) != len(track_ids) * 12
            or any(type(row) is not FaceSegmentObservation for row in observations)
            or tuple((row.track_id, row.segment_id, row.reference_median) for row in observations)
            != expected
            or any(row.axis != axis for row in observations)
        ):
            raise FaceFinalEvaluationError("final Face axis observation ancestry mismatch")
    for index, track in enumerate(corpus.tracks):
        transition = candidate.transitions[index]
        preference = candidate.preference_votes[index]
        if (
            type(transition) is not FaceTrackTransitions
            or transition.track_id != track.track_id
            or transition.reference_ms != track.transition_reference_ms
            or type(preference) is not FaceTrackPreferenceVotes
            or preference.track_id != track.track_id
            or type(preference.votes) is not tuple
            or len(preference.votes) != len(track.rater_pseudonyms)
            or any(
                type(vote) is not tuple
                or len(vote) != 2
                or type(vote[0]) is not str
                or type(vote[1]) is not bool
                for vote in preference.votes
            )
            or {vote[0] for vote in preference.votes}
            != {str(pseudonym) for pseudonym in track.rater_pseudonyms}
            or len({vote[0] for vote in preference.votes}) != len(preference.votes)
        ):
            raise FaceFinalEvaluationError("final Face transition or voter ancestry mismatch")
    return track_ids


def evaluate_face_final_candidate(
    qualification_manifest_sha256: str,
    corpus: FaceRaterCorpusReference,
    candidate: FaceFinalCandidateInput,
) -> FaceFinalTechnicalReport:
    """Run every frozen technical gate once after the caller proves legal authority."""

    track_ids = validate_face_final_candidate(corpus, candidate)
    axis_bounds: list[FaceAxisBootstrapBounds] = []
    try:
        for index, (_, alpha) in enumerate(corpus.ordinal_alpha_by_axis):
            axis_bounds.append(
                face_axis_bootstrap_bounds(
                    qualification_manifest_sha256,
                    track_ids,
                    candidate.observations_by_axis[index],
                    krippendorff_alpha=alpha,
                )
            )
        transition_bounds = face_transition_bootstrap_bounds(
            qualification_manifest_sha256, track_ids, candidate.transitions
        )
        preference_bounds = face_preference_bootstrap_bounds(
            qualification_manifest_sha256, track_ids, candidate.preference_votes
        )
    except FaceFinalStatisticsError as error:
        raise FaceFinalEvaluationError("final Face statistic undefined") from error
    return FaceFinalTechnicalReport(
        qualification_manifest_sha256,
        corpus.fixture_manifest_sha256,
        candidate.candidate_manifest_sha256,
        tuple(axis_bounds),
        transition_bounds,
        preference_bounds,
    )
