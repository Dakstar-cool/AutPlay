"""Raw per-track rater matrices yield one complete six-axis reliability report."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest

from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES
from autplay.application.face_rater_corpus import (
    FaceRaterCorpusError,
    FaceTrackRaterGroup,
    collate_face_rater_corpus,
)
from autplay.application.face_rater_submission import (
    FaceRaterSubmission,
    FaceSegmentRatings,
)

STUDY = UUID("10000000-0000-4000-8000-000000000001")
FIXTURE = "f" * 64
RATERS = tuple(UUID(int=index + 1) for index in range(3))
RECEIPTS = tuple(f"{index + 1:064x}" for index in range(3))
GRANTS = frozenset(zip(RATERS, RECEIPTS, strict=True))


def _groups() -> tuple[FaceTrackRaterGroup, ...]:
    result: list[FaceTrackRaterGroup] = []
    for track_index in range(15):
        segment_ids = tuple(
            sha256(f"{track_index}:{segment_index}".encode()).hexdigest()
            for segment_index in range(12)
        )
        submissions = tuple(
            FaceRaterSubmission(
                study_id=STUDY,
                fixture_manifest_sha256=FIXTURE,
                rater_pseudonym=RATERS[rater_index],
                consent_receipt_sha256=RECEIPTS[rater_index],
                recording_sha256=f"{track_index + 1:064x}",
                segments=tuple(
                    FaceSegmentRatings(
                        segment_id,
                        ((-3 if segment_index % 2 else 3),) * len(FACE_BAKEOFF_AXES),
                        3,
                    )
                    for segment_index, segment_id in enumerate(segment_ids)
                ),
                track_summary=(0,) * len(FACE_BAKEOFF_AXES),
                transition_sample_indices=(),
            )
            for rater_index in range(3)
        )
        result.append(FaceTrackRaterGroup(f"track-{track_index}", 48_000, submissions))
    return tuple(result)


def test_complete_rater_corpus_uses_all_raw_units() -> None:
    groups = _groups()
    result = collate_face_rater_corpus(
        groups,
        expected_track_ids=tuple(group.track_id for group in groups),
        expected_study_id=STUDY,
        expected_fixture_manifest_sha256=FIXTURE,
        active_consent_receipts=GRANTS,
    )
    assert len(result.tracks) == 15
    assert all(len(track.segments) == 12 for track in result.tracks)
    assert result.ordinal_alpha_by_axis == tuple((axis, 1.0) for axis in FACE_BAKEOFF_AXES)
    assert result.tracks[0].segments[0].axis_medians == (1.0,) * len(FACE_BAKEOFF_AXES)
    assert result.tracks[0].segments[1].axis_medians == (-1.0,) * len(FACE_BAKEOFF_AXES)


def test_rater_corpus_refuses_missing_tracks_and_receipt_rotation() -> None:
    groups = _groups()
    track_ids = tuple(group.track_id for group in groups)
    with pytest.raises(FaceRaterCorpusError, match="track order"):
        collate_face_rater_corpus(
            groups[:-1],
            expected_track_ids=track_ids,
            expected_study_id=STUDY,
            expected_fixture_manifest_sha256=FIXTURE,
            active_consent_receipts=GRANTS,
        )
    rotated_receipt = "a" * 64
    changed = replace(
        groups[1],
        submissions=(
            replace(groups[1].submissions[0], consent_receipt_sha256=rotated_receipt),
            *groups[1].submissions[1:],
        ),
    )
    with pytest.raises(FaceRaterCorpusError, match="pseudonym/receipt changed"):
        collate_face_rater_corpus(
            (groups[0], changed, *groups[2:]),
            expected_track_ids=track_ids,
            expected_study_id=STUDY,
            expected_fixture_manifest_sha256=FIXTURE,
            active_consent_receipts=GRANTS | {(RATERS[0], rotated_receipt)},
        )
    with pytest.raises(FaceRaterCorpusError, match="grant"):
        collate_face_rater_corpus(
            groups,
            expected_track_ids=track_ids,
            expected_study_id=STUDY,
            expected_fixture_manifest_sha256=FIXTURE,
            active_consent_receipts=GRANTS - {(RATERS[0], RECEIPTS[0])},
        )
