"""Content-bound final Face observation windows fixed before rater access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

import rfc8785

FINAL_WINDOWS_PER_RECORDING = 12
FINAL_WINDOW_SECONDS = 10
SEGMENT_ID_DOMAIN = b"autplay.face.qualification-segment.v1\0"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class FaceQualificationSegmentError(ValueError):
    """A source cannot enter the sealed final observation schedule."""


@dataclass(frozen=True, slots=True)
class FaceQualificationSegment:
    segment_id: str
    index: int
    start_sample: int
    end_sample: int


def preregister_face_final_segments(
    *, source_sha256: str, decoded_sample_rate: int, decoded_sample_count: int
) -> tuple[FaceQualificationSegment, ...]:
    """Place twelve exact ten-second windows from start to end of one source.

    Extra samples are distributed across the eleven gaps using integer floor;
    the final window ends exactly at the decoded-source end. This schedule is
    content addressed and independent of model outputs or human ratings.
    """

    if (
        type(source_sha256) is not str
        or _HEX.fullmatch(source_sha256) is None
        or type(decoded_sample_rate) is not int
        or not 8_000 <= decoded_sample_rate <= 384_000
        or type(decoded_sample_count) is not int
        or decoded_sample_count > 33_177_600_000
    ):
        raise FaceQualificationSegmentError("invalid final fixture source")
    window = FINAL_WINDOW_SECONDS * decoded_sample_rate
    minimum = FINAL_WINDOWS_PER_RECORDING * window
    if decoded_sample_count < minimum:
        raise FaceQualificationSegmentError("final fixture is shorter than twelve windows")
    spare = decoded_sample_count - minimum
    result: list[FaceQualificationSegment] = []
    for index in range(FINAL_WINDOWS_PER_RECORDING):
        start = index * window + (index * spare) // (FINAL_WINDOWS_PER_RECORDING - 1)
        end = start + window
        document = {
            "schema_version": 1,
            "source_sha256": source_sha256,
            "decoded_sample_rate": decoded_sample_rate,
            "decoded_sample_count": decoded_sample_count,
            "index": index,
            "start_sample": start,
            "end_sample": end,
        }
        identifier = sha256(SEGMENT_ID_DOMAIN + rfc8785.dumps(cast(Any, document))).hexdigest()
        result.append(FaceQualificationSegment(identifier, index, start, end))
    if result[0].start_sample != 0 or result[-1].end_sample != decoded_sample_count:
        raise FaceQualificationSegmentError("final window schedule failed endpoints")
    return tuple(result)
