"""Canonical Face v2 result stays separate from v1 and binds sample identity."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import rfc8785

from autplay.application.face_v2_contract import (
    FACE_V2_CONTENT_TYPE,
    decode_face_v2_timeline,
    encode_face_v2_timeline,
    face_v2_result_hash,
)
from autplay.domain.face_v2_timeline import FaceV2TimelineError

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/face"
GOLDEN_RESULT = "b0f49f7565e575b90c890a06fa2494d7e28a92371d33b725903985497e387b17"


def _bytes() -> bytes:
    return (FIXTURES / "v2-timeline.canonical.json").read_bytes()


def test_cross_language_ready_golden_bytes_and_domain_hash() -> None:
    timeline = decode_face_v2_timeline(_bytes())
    assert len(_bytes()) == 2382
    assert encode_face_v2_timeline(timeline) == _bytes()
    assert face_v2_result_hash(timeline) == GOLDEN_RESULT
    assert FACE_V2_CONTENT_TYPE == "application/vnd.autplay.face-timeline.v2+json"
    assert timeline.keyframes[0].sample_index == 1024
    assert timeline.keyframes[1].sample_index == 49_024


def test_different_result_under_same_semantic_key_is_detectable() -> None:
    timeline = decode_face_v2_timeline(_bytes())
    changed = replace(
        timeline,
        events=(replace(timeline.events[0], strength=0.75),),
    )
    assert changed.identity.semantic_key() == timeline.identity.semantic_key()
    assert face_v2_result_hash(changed) != GOLDEN_RESULT


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"schema_version":2,"schema_version":2}',
        b"[" * 13 + b"null" + b"]" * 13,
        b"\xff",
        b'{"schema_version":NaN}',
    ],
)
def test_invalid_or_deep_bytes_fail_closed(raw: bytes) -> None:
    with pytest.raises(FaceV2TimelineError):
        decode_face_v2_timeline(raw)


def test_noncanonical_unknown_axis_and_sample_overflow_fail_closed() -> None:
    with pytest.raises(FaceV2TimelineError):
        decode_face_v2_timeline((FIXTURES / "v2-timeline.json").read_bytes())
    document = json.loads(_bytes())
    document["keyframes"][0]["sample_index"] = document["identity"]["decoded_sample_count"]
    with pytest.raises(FaceV2TimelineError):
        decode_face_v2_timeline(rfc8785.dumps(document))
    document = json.loads(_bytes())
    document["track_character"]["axes"]["calm_energetic"]["unknown"] = 1
    with pytest.raises(FaceV2TimelineError):
        decode_face_v2_timeline(rfc8785.dumps(document))
    document = json.loads(_bytes())
    document["source_presentation_map"]["decoder_version"] = "other"
    with pytest.raises(FaceV2TimelineError):
        decode_face_v2_timeline(rfc8785.dumps(document))
