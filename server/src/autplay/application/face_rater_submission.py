"""Pure preflight for isolated human Face qualification submissions.

The caller must get active consent receipts from the independent operator rater
authority and bind an exact sealed fixture manifest before invoking this codec.
No submission is written to product PostgreSQL or participant backup storage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from statistics import median
from typing import Any
from uuid import UUID

import rfc8785

from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES

RUBRIC_VERSION = "FACE_QUALIFICATION_RUBRIC_V1"
MAX_RATER_SUBMISSION_BYTES = 65_536
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset(
    {
        "schema_version",
        "study_id",
        "fixture_manifest_sha256",
        "rubric_version",
        "rater_pseudonym",
        "consent_receipt_sha256",
        "recording_sha256",
        "segments",
        "track_summary",
        "transition_sample_indices",
    }
)


class FaceRaterSubmissionError(ValueError):
    """The raw rating cannot enter an operator qualification report."""


@dataclass(frozen=True, slots=True)
class FaceSegmentRatings:
    segment_id: str
    axes: tuple[int, ...]
    confidence: int


@dataclass(frozen=True, slots=True)
class FaceRaterSubmission:
    study_id: UUID
    fixture_manifest_sha256: str
    rater_pseudonym: UUID
    consent_receipt_sha256: str
    recording_sha256: str
    segments: tuple[FaceSegmentRatings, ...]
    track_summary: tuple[int, ...]
    transition_sample_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FaceReferenceSegment:
    segment_id: str
    axis_medians: tuple[float, ...]


def parse_face_rater_submission(
    raw: bytes,
    *,
    expected_study_id: UUID,
    expected_fixture_manifest_sha256: str,
    expected_recording_sha256: str,
    expected_segment_ids: tuple[str, ...],
    decoded_sample_count: int,
) -> FaceRaterSubmission:
    """Parse exact canonical bytes against the sealed track/segment authority."""

    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAX_RATER_SUBMISSION_BYTES
        or type(expected_segment_ids) is not tuple
        or len(expected_segment_ids) != 12
        or len(set(expected_segment_ids)) != 12
        or any(not _digest(value) for value in expected_segment_ids)
        or type(decoded_sample_count) is not int
        or not 1 <= decoded_sample_count <= 33_177_600_000
        or not _digest(expected_fixture_manifest_sha256)
        or not _digest(expected_recording_sha256)
    ):
        raise FaceRaterSubmissionError("invalid sealed rater authority")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FaceRaterSubmissionError("duplicate rater field")
            result[key] = value
        return result

    def invalid_constant(_token: str) -> None:
        raise FaceRaterSubmissionError("non-finite rater value")

    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant
        )
        if type(document) is not dict or set(document) != _FIELDS:
            raise FaceRaterSubmissionError("invalid rater fields")
        if rfc8785.dumps(document) != raw:
            raise FaceRaterSubmissionError("noncanonical rater bytes")
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["rubric_version"] != RUBRIC_VERSION
            or _uuid(document["study_id"]) != expected_study_id
            or document["fixture_manifest_sha256"] != expected_fixture_manifest_sha256
            or document["recording_sha256"] != expected_recording_sha256
            or not _digest(document["consent_receipt_sha256"])
        ):
            raise FaceRaterSubmissionError("rater ancestry mismatch")
        pseudonym = _uuid(document["rater_pseudonym"])
        rows = document["segments"]
        if type(rows) is not list or len(rows) != 12:
            raise FaceRaterSubmissionError("invalid rater segment count")
        segments = tuple(_segment(row) for row in rows)
        if tuple(row.segment_id for row in segments) != expected_segment_ids:
            raise FaceRaterSubmissionError("rater segments differ from sealed track")
        track_summary = _axes(document["track_summary"])
        raw_transitions = document["transition_sample_indices"]
        if type(raw_transitions) is not list or len(raw_transitions) > 256:
            raise FaceRaterSubmissionError("invalid transition count")
        transitions = tuple(raw_transitions)
        if (
            any(
                type(sample) is not int or not 0 <= sample < decoded_sample_count
                for sample in transitions
            )
            or tuple(sorted(set(transitions))) != transitions
        ):
            raise FaceRaterSubmissionError("invalid transition samples")
        return FaceRaterSubmission(
            study_id=expected_study_id,
            fixture_manifest_sha256=expected_fixture_manifest_sha256,
            rater_pseudonym=pseudonym,
            consent_receipt_sha256=document["consent_receipt_sha256"],
            recording_sha256=expected_recording_sha256,
            segments=segments,
            track_summary=track_summary,
            transition_sample_indices=transitions,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        KeyError,
        TypeError,
        ValueError,
        rfc8785.CanonicalizationError,
    ) as error:
        if isinstance(error, FaceRaterSubmissionError):
            raise
        raise FaceRaterSubmissionError("invalid rater submission") from error


def collate_face_raters(
    submissions: tuple[FaceRaterSubmission, ...],
    *,
    active_consent_receipts: frozenset[tuple[UUID, str]],
) -> tuple[FaceReferenceSegment, ...]:
    """Form segment medians only after at least three independent current grants."""

    if (
        type(submissions) is not tuple
        or not 3 <= len(submissions) <= 20
        or any(not isinstance(value, FaceRaterSubmission) for value in submissions)
        or len({row.rater_pseudonym for row in submissions}) != len(submissions)
        or len({row.consent_receipt_sha256 for row in submissions}) != len(submissions)
        or len({row.study_id for row in submissions}) != 1
        or len({row.fixture_manifest_sha256 for row in submissions}) != 1
        or len({row.recording_sha256 for row in submissions}) != 1
        or len({tuple(segment.segment_id for segment in row.segments) for row in submissions}) != 1
        or any(
            (row.rater_pseudonym, row.consent_receipt_sha256) not in active_consent_receipts
            for row in submissions
        )
    ):
        raise FaceRaterSubmissionError("three independent current rater grants required")
    return tuple(
        FaceReferenceSegment(
            segment_id=submissions[0].segments[index].segment_id,
            axis_medians=tuple(
                float(median(row.segments[index].axes[axis_index] for row in submissions)) / 3.0
                for axis_index in range(len(FACE_BAKEOFF_AXES))
            ),
        )
        for index in range(12)
    )


def collate_face_transition_reference_ms(
    submissions: tuple[FaceRaterSubmission, ...],
    *,
    active_consent_receipts: frozenset[tuple[UUID, str]],
    decoded_sample_rate_hz: int,
) -> tuple[int, ...]:
    """Freeze a strict-majority, one-mark-per-rater transition reference.

    Candidate pairs from different raters are considered by time difference,
    then time and rater index. Additional raters join only if the entire group
    spans at most 3 s. A group needs a strict majority of current raters.
    Each raw mark contributes to at most one reference; the reference is the
    lower median millisecond of that group.
    """

    collate_face_raters(submissions, active_consent_receipts=active_consent_receipts)
    if type(decoded_sample_rate_hz) is not int or not 8_000 <= decoded_sample_rate_hz <= 384_000:
        raise FaceRaterSubmissionError("invalid decoded transition sample rate")
    ordered_submissions = sorted(submissions, key=lambda row: row.rater_pseudonym.bytes)
    marks = sorted(
        (sample * 1_000 // decoded_sample_rate_hz, rater_index, ordinal)
        for rater_index, row in enumerate(ordered_submissions)
        for ordinal, sample in enumerate(row.transition_sample_indices)
    )
    if len(marks) > 256:
        raise FaceRaterSubmissionError("too many transition marks")
    candidates = sorted(
        (
            later[0] - earlier[0],
            earlier[0],
            later[0],
            earlier_index,
            later_index,
        )
        for earlier_index, earlier in enumerate(marks)
        for later_index in range(earlier_index + 1, len(marks))
        if (later := marks[later_index])[0] - earlier[0] <= 3_000 and earlier[1] != later[1]
    )
    consumed: set[int] = set()
    references: list[int] = []
    majority = len(submissions) // 2 + 1
    for _, _, _, first_index, second_index in candidates:
        if first_index in consumed or second_index in consumed:
            continue
        group = [first_index, second_index]
        raters = {marks[first_index][1], marks[second_index][1]}
        minimum, maximum = marks[first_index][0], marks[second_index][0]
        for index in range(len(marks)):
            if index in consumed or index in group or marks[index][1] in raters:
                continue
            time_ms = marks[index][0]
            if max(maximum, time_ms) - min(minimum, time_ms) <= 3_000:
                group.append(index)
                raters.add(marks[index][1])
                minimum = min(minimum, time_ms)
                maximum = max(maximum, time_ms)
        if len(group) < majority:
            continue
        ordered_times = sorted(marks[index][0] for index in group)
        references.append(ordered_times[(len(ordered_times) - 1) // 2])
        consumed.update(group)
    return tuple(sorted(set(references)))


def _digest(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise FaceRaterSubmissionError("invalid rater UUID")
    identifier = UUID(value)
    if str(identifier) != value:
        raise FaceRaterSubmissionError("noncanonical rater UUID")
    return identifier


def _axes(value: object) -> tuple[int, ...]:
    if type(value) is not dict or set(value) != set(FACE_BAKEOFF_AXES):
        raise FaceRaterSubmissionError("invalid rater axis set")
    scores = tuple(value[axis] for axis in FACE_BAKEOFF_AXES)
    if any(type(score) is not int or not -3 <= score <= 3 for score in scores):
        raise FaceRaterSubmissionError("invalid rater axis score")
    return scores


def _segment(value: object) -> FaceSegmentRatings:
    if type(value) is not dict or set(value) != {"segment_id", "axes", "confidence"}:
        raise FaceRaterSubmissionError("invalid rater segment")
    if not _digest(value["segment_id"]):
        raise FaceRaterSubmissionError("invalid rater segment identity")
    confidence = value["confidence"]
    if type(confidence) is not int or not 0 <= confidence <= 3:
        raise FaceRaterSubmissionError("invalid rater confidence")
    return FaceSegmentRatings(value["segment_id"], _axes(value["axes"]), confidence)
