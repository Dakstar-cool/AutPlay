"""Face v2 semantic-key vectors bind the decoded-sample presentation map."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from autplay.domain.face_presentation_map import (
    SourcePresentationMapV1,
    parse_source_presentation_map,
)
from autplay.domain.face_v2_identity import (
    FaceTimelineIdentityV2,
    FaceV2IdentityError,
    parse_face_v2_identity,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/face"
GOLDEN_KEY = "12b13bf6cc0d2d4f71aa014d7a4b89e27986e489dfa22135d55ca0d58be4cce4"


def _map() -> SourcePresentationMapV1:
    return parse_source_presentation_map(
        (FIXTURES / "source-presentation-map-v1.canonical.json").read_bytes()
    )


def _identity() -> FaceTimelineIdentityV2:
    document = json.loads((FIXTURES / "v2-identity.json").read_text(encoding="utf-8"))
    for field in ("schema_version", "timeline_codec_version", "source_timebase"):
        document.pop(field)
    for field in (
        "recording_id",
        "audio_variant_id",
        "embedding_model_id",
        "semantic_interpreter_id",
    ):
        document[field] = UUID(document[field])
    return FaceTimelineIdentityV2(**document)


def test_identity_golden_and_exact_presentation_map_binding() -> None:
    identity = _identity()
    canonical = (FIXTURES / "v2-identity.canonical.json").read_bytes()
    assert identity.canonical_bytes() == canonical
    assert len(canonical) == 1032
    assert identity.semantic_key() == GOLDEN_KEY
    assert parse_face_v2_identity(canonical, _map()) == identity


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("recording_id", UUID("10000000-0000-4000-8000-000000000009")),
        ("audio_variant_id", UUID("20000000-0000-4000-8000-000000000009")),
        ("source_sha256", "0" * 64),
        ("source_presentation_map_sha256", "0" * 64),
        ("embedding_manifest_sha256", "0" * 64),
        ("interpreter_manifest_sha256", "0" * 64),
        ("preprocessing_sha256", "0" * 64),
        ("calibration_sha256", "0" * 64),
        ("execution_profile_sha256", "0" * 64),
        ("decoded_sample_rate", 44_100),
    ],
)
def test_every_source_or_execution_change_creates_new_semantic_key(
    field: str, replacement: object
) -> None:
    successor = replace(_identity(), **cast(dict[str, Any], {field: replacement}))
    assert successor.semantic_key() != GOLDEN_KEY


def test_map_or_canonical_identity_mismatch_fails_closed() -> None:
    identity = _identity()
    with pytest.raises(FaceV2IdentityError):
        identity.bind_map(replace(_map(), decoder_version="different"))
    raw = identity.canonical_bytes()
    with pytest.raises(FaceV2IdentityError):
        parse_face_v2_identity(raw + b" ", _map())
    with pytest.raises(FaceV2IdentityError):
        parse_face_v2_identity(
            raw.replace(b'"schema_version":2', b'"schema_version":2,"schema_version":2'), _map()
        )
    with pytest.raises(FaceV2IdentityError):
        parse_face_v2_identity(
            raw.replace(b'"timeline_codec_version":2', b'"timeline_codec_version":1'), _map()
        )
