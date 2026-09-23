"""A final candidate cannot replace the sealed rater references or voters."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest

from autplay.application import face_final_report_archive
from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES
from autplay.application.face_bakeoff_metrics import FaceSegmentObservation
from autplay.application.face_final_evaluation import (
    FaceFinalCandidateInput,
    FaceFinalEvaluationError,
    validate_face_final_candidate,
)
from autplay.application.face_final_report_archive import (
    FaceFinalReportArchiveError,
    archive_face_final_candidate,
)
from autplay.application.face_final_statistics import (
    BOOTSTRAP_DRAWS,
    FaceTrackPreferenceVotes,
    FaceTrackTransitions,
)
from autplay.application.face_rater_corpus import (
    FaceRaterCorpusReference,
    FaceTrackReference,
)
from autplay.application.face_rater_submission import FaceReferenceSegment

RATERS = tuple(UUID(int=index + 1) for index in range(3))


def _sealed() -> tuple[FaceRaterCorpusReference, FaceFinalCandidateInput]:
    tracks = tuple(
        FaceTrackReference(
            track_id=f"track-{track_index}",
            recording_sha256=f"{track_index + 1:064x}",
            segments=tuple(
                FaceReferenceSegment(
                    sha256(f"{track_index}:{segment_index}".encode()).hexdigest(),
                    ((-1.0 if segment_index % 2 else 1.0),) * len(FACE_BAKEOFF_AXES),
                )
                for segment_index in range(12)
            ),
            transition_reference_ms=(10_000, 30_000, 50_000, 70_000),
            rater_pseudonyms=RATERS,
        )
        for track_index in range(15)
    )
    corpus = FaceRaterCorpusReference(
        study_id=UUID(int=11),
        fixture_manifest_sha256="f" * 64,
        tracks=tracks,
        ordinal_alpha_by_axis=tuple((axis, 1.0) for axis in FACE_BAKEOFF_AXES),
    )
    candidate = FaceFinalCandidateInput(
        candidate_manifest_sha256="c" * 64,
        observations_by_axis=tuple(
            tuple(
                FaceSegmentObservation(
                    axis,
                    track.track_id,
                    segment.segment_id,
                    segment.axis_medians[axis_index],
                    segment.axis_medians[axis_index],
                    1.0,
                )
                for track in tracks
                for segment in track.segments
            )
            for axis_index, axis in enumerate(FACE_BAKEOFF_AXES)
        ),
        transitions=tuple(
            FaceTrackTransitions(
                track.track_id,
                track.transition_reference_ms,
                track.transition_reference_ms,
            )
            for track in tracks
        ),
        preference_votes=tuple(
            FaceTrackPreferenceVotes(
                track.track_id,
                tuple((str(rater), True) for rater in track.rater_pseudonyms),
            )
            for track in tracks
        ),
    )
    return corpus, candidate


def test_final_report_archives_all_draws_and_inputs_without_granting_approval() -> None:
    corpus, candidate = _sealed()
    archive = archive_face_final_candidate("a" * 64, corpus, candidate)
    document = json.loads(archive.canonical_bytes)
    assert archive.technical_pass
    assert archive.report_sha256 == sha256(archive.canonical_bytes).hexdigest()
    assert document["authority"] == "TECHNICAL_ONLY_NO_FIXTURE_OR_RATER_APPROVAL"
    assert len(document["reference_corpus"]["tracks"]) == 15
    assert len(document["candidate"]["observations_by_axis"]) == 6
    assert len(document["implementation"]["script_sha256"]) == 10
    assert all(seed.isdecimal() for seed in document["protocol"]["seed_by_family_decimal"].values())
    assert all(
        len(axis[family]) == BOOTSTRAP_DRAWS
        for axis in document["metrics_and_all_draws"]["axis_bounds"]
        for family in ("spearman_draws", "balanced_accuracy_draws", "ece_draws")
    )
    assert len(document["metrics_and_all_draws"]["transition_bounds"]["draws"]) == BOOTSTRAP_DRAWS
    assert len(document["metrics_and_all_draws"]["preference_bounds"]["draws"]) == BOOTSTRAP_DRAWS


def test_final_report_rejects_excluding_a_track_in_its_sealed_corpus() -> None:
    corpus, candidate = _sealed()
    with pytest.raises(FaceFinalReportArchiveError, match="invalid sealed Face exclusions"):
        archive_face_final_candidate(
            "a" * 64, corpus, candidate, exclusions=((corpus.tracks[0].track_id, "SKIP"),)
        )


def test_final_report_rejects_evaluator_source_change_during_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus, candidate = _sealed()
    hashes = iter(({"script": "before"}, {"script": "after"}))
    monkeypatch.setattr(face_final_report_archive, "_script_hashes", lambda: next(hashes))
    monkeypatch.setattr(
        face_final_report_archive, "evaluate_face_final_candidate", lambda *_: object()
    )
    with pytest.raises(FaceFinalReportArchiveError, match="changed during qualification"):
        archive_face_final_candidate("a" * 64, corpus, candidate)


def test_final_candidate_matches_every_sealed_segment_and_rater() -> None:
    corpus, candidate = _sealed()
    assert validate_face_final_candidate(corpus, candidate) == tuple(
        track.track_id for track in corpus.tracks
    )
    altered_axis = (
        replace(candidate.observations_by_axis[0][0], reference_median=0.0),
        *candidate.observations_by_axis[0][1:],
    )
    with pytest.raises(FaceFinalEvaluationError, match="axis observation ancestry"):
        validate_face_final_candidate(
            corpus,
            replace(
                candidate,
                observations_by_axis=(altered_axis, *candidate.observations_by_axis[1:]),
            ),
        )


def test_final_candidate_rejects_forged_transition_and_voter() -> None:
    corpus, candidate = _sealed()
    with pytest.raises(FaceFinalEvaluationError, match="transition or voter ancestry"):
        validate_face_final_candidate(
            corpus,
            replace(
                candidate,
                transitions=(
                    replace(
                        candidate.transitions[0], reference_ms=(11_000, 30_000, 50_000, 70_000)
                    ),
                    *candidate.transitions[1:],
                ),
            ),
        )
    with pytest.raises(FaceFinalEvaluationError, match="transition or voter ancestry"):
        validate_face_final_candidate(
            corpus,
            replace(
                candidate,
                preference_votes=(
                    replace(
                        candidate.preference_votes[0],
                        votes=((str(UUID(int=99)), True), *candidate.preference_votes[0].votes[1:]),
                    ),
                    *candidate.preference_votes[1:],
                ),
            ),
        )
