"""Pure complete-corpus collation of independently authorized Face ratings.

The operator boundary must parse every submission against sealed segments and
prove each consent receipt current before passing the values here. This module
keeps raw ordinal units visible to the reliability calculation; it grants no
authority to unseal a final set or approve a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES
from autplay.application.face_ordinal_alpha import (
    FaceAnnotationReliabilityError,
    face_ordinal_krippendorff_alpha,
)
from autplay.application.face_rater_submission import (
    FaceRaterSubmission,
    FaceRaterSubmissionError,
    FaceReferenceSegment,
    FaceSegmentRatings,
    collate_face_raters,
    collate_face_transition_reference_ms,
)


class FaceRaterCorpusError(ValueError):
    """The raw rater matrix cannot establish a complete reference corpus."""


@dataclass(frozen=True, slots=True)
class FaceTrackRaterGroup:
    track_id: str
    decoded_sample_rate_hz: int
    submissions: tuple[FaceRaterSubmission, ...]


@dataclass(frozen=True, slots=True)
class FaceTrackReference:
    track_id: str
    recording_sha256: str
    segments: tuple[FaceReferenceSegment, ...]
    transition_reference_ms: tuple[int, ...]
    rater_pseudonyms: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class FaceRaterCorpusReference:
    study_id: UUID
    fixture_manifest_sha256: str
    tracks: tuple[FaceTrackReference, ...]
    ordinal_alpha_by_axis: tuple[tuple[str, float], ...]


def collate_face_rater_corpus(
    groups: tuple[FaceTrackRaterGroup, ...],
    *,
    expected_track_ids: tuple[str, ...],
    expected_study_id: UUID,
    expected_fixture_manifest_sha256: str,
    active_consent_receipts: frozenset[tuple[UUID, str]],
) -> FaceRaterCorpusReference:
    """Collate exactly the sealed final track order and all six raw-axis matrices."""

    if (
        type(groups) is not tuple
        or type(expected_track_ids) is not tuple
        or not 15 <= len(groups) <= 1_000
        or len(groups) != len(expected_track_ids)
        or any(type(group) is not FaceTrackRaterGroup for group in groups)
        or any(
            type(track_id) is not str or not 1 <= len(track_id) <= 128
            for track_id in expected_track_ids
        )
        or len(set(expected_track_ids)) != len(expected_track_ids)
        or tuple(group.track_id for group in groups) != expected_track_ids
        or type(expected_study_id) is not UUID
        or type(expected_fixture_manifest_sha256) is not str
        or len(expected_fixture_manifest_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_fixture_manifest_sha256)
        or type(active_consent_receipts) is not frozenset
    ):
        raise FaceRaterCorpusError("incomplete sealed Face rater track order")

    pseudonym_receipts: dict[UUID, str] = {}
    receipt_pseudonyms: dict[str, UUID] = {}
    recording_hashes: set[str] = set()
    segment_ids: set[str] = set()
    raw_units: list[list[tuple[int, ...]]] = [[] for _ in FACE_BAKEOFF_AXES]
    references: list[FaceTrackReference] = []
    for group in groups:
        submissions = group.submissions
        if (
            type(submissions) is not tuple
            or type(group.decoded_sample_rate_hz) is not int
            or not 8_000 <= group.decoded_sample_rate_hz <= 384_000
            or not 3 <= len(submissions) <= 20
            or any(type(row) is not FaceRaterSubmission for row in submissions)
            or any(
                row.study_id != expected_study_id
                or row.fixture_manifest_sha256 != expected_fixture_manifest_sha256
                or len(row.segments) != 12
                or len(row.track_summary) != len(FACE_BAKEOFF_AXES)
                or any(
                    type(segment) is not FaceSegmentRatings
                    or len(segment.axes) != len(FACE_BAKEOFF_AXES)
                    or any(type(value) is not int or not -3 <= value <= 3 for value in segment.axes)
                    for segment in row.segments
                )
                for row in submissions
            )
        ):
            raise FaceRaterCorpusError("Face rater ancestry or count mismatch")
        try:
            segments = collate_face_raters(
                submissions, active_consent_receipts=active_consent_receipts
            )
            transitions = collate_face_transition_reference_ms(
                submissions,
                active_consent_receipts=active_consent_receipts,
                decoded_sample_rate_hz=group.decoded_sample_rate_hz,
            )
        except FaceRaterSubmissionError as error:
            raise FaceRaterCorpusError("Face rater grant or segment mismatch") from error
        recording_hash = submissions[0].recording_sha256
        if recording_hash in recording_hashes:
            raise FaceRaterCorpusError("duplicate final Face recording")
        recording_hashes.add(recording_hash)
        for segment in segments:
            if segment.segment_id in segment_ids:
                raise FaceRaterCorpusError("duplicate final Face segment")
            segment_ids.add(segment.segment_id)
        for row in submissions:
            prior_receipt = pseudonym_receipts.setdefault(
                row.rater_pseudonym, row.consent_receipt_sha256
            )
            prior_pseudonym = receipt_pseudonyms.setdefault(
                row.consent_receipt_sha256, row.rater_pseudonym
            )
            if (
                prior_receipt != row.consent_receipt_sha256
                or prior_pseudonym != row.rater_pseudonym
            ):
                raise FaceRaterCorpusError("Face rater pseudonym/receipt changed")
        for segment_index in range(12):
            for axis_index in range(len(FACE_BAKEOFF_AXES)):
                raw_units[axis_index].append(
                    tuple(row.segments[segment_index].axes[axis_index] for row in submissions)
                )
        references.append(
            FaceTrackReference(
                group.track_id,
                recording_hash,
                segments,
                transitions,
                tuple(
                    sorted(
                        (row.rater_pseudonym for row in submissions), key=lambda value: value.bytes
                    )
                ),
            )
        )
    try:
        reliability = tuple(
            (
                axis,
                face_ordinal_krippendorff_alpha(tuple(raw_units[axis_index])),
            )
            for axis_index, axis in enumerate(FACE_BAKEOFF_AXES)
        )
    except FaceAnnotationReliabilityError as error:
        raise FaceRaterCorpusError("undefined Face rater reliability") from error
    return FaceRaterCorpusReference(
        expected_study_id,
        expected_fixture_manifest_sha256,
        tuple(references),
        reliability,
    )
