"""Three independent raw ratings are required before a Face reference median."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from uuid import UUID

import pytest
import rfc8785

from autplay.application.face_bakeoff import FACE_BAKEOFF_AXES
from autplay.application.face_rater_submission import (
    FaceRaterSubmission,
    FaceRaterSubmissionError,
    collate_face_raters,
    collate_face_transition_reference_ms,
    parse_face_rater_submission,
)

STUDY = UUID("00000000-0000-4000-8000-000000000101")
RATERS = tuple(UUID(f"00000000-0000-4000-8000-{index:012d}") for index in (1, 2, 3))
SEGMENTS = tuple(f"{index:064x}" for index in range(1, 13))
FIXTURE = "a" * 64
RECORDING = "b" * 64


def _document(rater: UUID, score: int = 1) -> dict[str, object]:
    axes = {axis: score for axis in FACE_BAKEOFF_AXES}
    return {
        "schema_version": 1,
        "study_id": str(STUDY),
        "fixture_manifest_sha256": FIXTURE,
        "rubric_version": "FACE_QUALIFICATION_RUBRIC_V1",
        "rater_pseudonym": str(rater),
        "consent_receipt_sha256": f"{RATERS.index(rater) + 1:064x}",
        "recording_sha256": RECORDING,
        "segments": [
            {"segment_id": segment, "axes": axes, "confidence": 2} for segment in SEGMENTS
        ],
        "track_summary": axes,
        "transition_sample_indices": [100, 200],
    }


def _parse(document: dict[str, object]) -> FaceRaterSubmission:
    return parse_face_rater_submission(
        rfc8785.dumps(cast(Any, document)),
        expected_study_id=STUDY,
        expected_fixture_manifest_sha256=FIXTURE,
        expected_recording_sha256=RECORDING,
        expected_segment_ids=SEGMENTS,
        decoded_sample_count=1_000_000,
    )


def test_three_current_independent_raters_produce_reference_median() -> None:
    submissions = tuple(
        _parse(_document(rater, score)) for rater, score in zip(RATERS, (-2, 1, 3), strict=True)
    )
    current = frozenset((row.rater_pseudonym, row.consent_receipt_sha256) for row in submissions)
    reference = collate_face_raters(submissions, active_consent_receipts=current)
    assert len(reference) == 12
    assert reference[0].segment_id == SEGMENTS[0]
    assert reference[0].axis_medians == (1 / 3,) * 6


def test_rater_tampering_and_missing_consent_fail_closed() -> None:
    original = _document(RATERS[0])
    assert len(_parse(original).segments) == 12
    altered = deepcopy(original)
    altered["segments"][0]["segment_id"] = "c" * 64  # type: ignore[index]
    with pytest.raises(FaceRaterSubmissionError, match="sealed track"):
        _parse(altered)
    altered = deepcopy(original)
    altered["segments"][0]["axes"][FACE_BAKEOFF_AXES[0]] = True  # type: ignore[index]
    with pytest.raises(FaceRaterSubmissionError, match="axis score"):
        _parse(altered)
    altered = deepcopy(original)
    altered["transition_sample_indices"] = [200, 100]
    with pytest.raises(FaceRaterSubmissionError, match="transition"):
        _parse(altered)
    with pytest.raises(FaceRaterSubmissionError, match="noncanonical"):
        parse_face_rater_submission(
            b" " + rfc8785.dumps(cast(Any, original)),
            expected_study_id=STUDY,
            expected_fixture_manifest_sha256=FIXTURE,
            expected_recording_sha256=RECORDING,
            expected_segment_ids=SEGMENTS,
            decoded_sample_count=1_000_000,
        )
    submissions = tuple(_parse(_document(rater)) for rater in RATERS)
    with pytest.raises(FaceRaterSubmissionError, match="current rater grants"):
        collate_face_raters(submissions, active_consent_receipts=frozenset())
    with pytest.raises(FaceRaterSubmissionError, match="independent"):
        collate_face_raters(
            (submissions[0], submissions[0], submissions[2]),
            active_consent_receipts=frozenset(
                (row.rater_pseudonym, row.consent_receipt_sha256) for row in submissions
            ),
        )


def test_independent_transition_marks_form_majority_references_without_lone_marks() -> None:
    documents = [_document(rater) for rater in RATERS]
    documents[0]["transition_sample_indices"] = [10_000, 100_000, 200_000]
    documents[1]["transition_sample_indices"] = [11_000, 101_000]
    documents[2]["transition_sample_indices"] = [9_000, 99_000]
    submissions = tuple(_parse(row) for row in documents)
    current = frozenset((row.rater_pseudonym, row.consent_receipt_sha256) for row in submissions)
    assert collate_face_transition_reference_ms(
        submissions, active_consent_receipts=current, decoded_sample_rate_hz=10_000
    ) == (1_000, 10_000)
    assert collate_face_transition_reference_ms(
        tuple(reversed(submissions)),
        active_consent_receipts=current,
        decoded_sample_rate_hz=10_000,
    ) == (1_000, 10_000)
    assert isinstance(
        collate_face_transition_reference_ms(
            submissions,
            active_consent_receipts=current,
            decoded_sample_rate_hz=384_000,
        ),
        tuple,
    )
    with pytest.raises(FaceRaterSubmissionError, match="current rater grants"):
        collate_face_transition_reference_ms(
            submissions, active_consent_receipts=frozenset(), decoded_sample_rate_hz=10_000
        )
    with pytest.raises(FaceRaterSubmissionError, match="sample rate"):
        collate_face_transition_reference_ms(
            submissions, active_consent_receipts=current, decoded_sample_rate_hz=384_001
        )
