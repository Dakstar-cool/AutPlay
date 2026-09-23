"""Final observation IDs and windows are frozen before model or rater access."""

from __future__ import annotations

from itertools import pairwise

import pytest

from autplay.application.face_qualification_segments import (
    FaceQualificationSegmentError,
    preregister_face_final_segments,
)


def test_twelve_ten_second_windows_span_exact_source_with_golden_ids() -> None:
    windows = preregister_face_final_segments(
        source_sha256="a" * 64, decoded_sample_rate=16_000, decoded_sample_count=2_880_000
    )
    assert len(windows) == 12
    assert windows[0].start_sample == 0
    assert windows[-1].end_sample == 2_880_000
    assert all(row.end_sample - row.start_sample == 160_000 for row in windows)
    assert all(left.end_sample <= right.start_sample for left, right in pairwise(windows))
    assert len({row.segment_id for row in windows}) == 12
    assert (
        windows[0].segment_id == "93ad45b5e35c27b1332dd3d17b95bc72014eda726526162fc33fd8df7f407e2c"
    )
    assert (
        windows[-1].segment_id == "202fec649be340d03b3b2c31a1095961f1a688a3e0cf9ab7c8a0cefe46924ce7"
    )


def test_short_or_changed_source_cannot_reuse_pre_registered_ids() -> None:
    with pytest.raises(FaceQualificationSegmentError, match="shorter"):
        preregister_face_final_segments(
            source_sha256="a" * 64, decoded_sample_rate=16_000, decoded_sample_count=1_919_999
        )
    exact = preregister_face_final_segments(
        source_sha256="a" * 64, decoded_sample_rate=16_000, decoded_sample_count=1_920_000
    )
    assert all(row.start_sample == index * 160_000 for index, row in enumerate(exact))
    successor = preregister_face_final_segments(
        source_sha256="b" * 64, decoded_sample_rate=16_000, decoded_sample_count=1_920_000
    )
    assert exact[0].segment_id != successor[0].segment_id
    with pytest.raises(FaceQualificationSegmentError):
        preregister_face_final_segments(
            source_sha256="A" * 64, decoded_sample_rate=16_000, decoded_sample_count=1_920_000
        )
