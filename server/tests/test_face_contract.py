"""Untrusted wire boundaries and identity invariants for the shared Face contract."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from autplay.application.face_contract import (
    decode_projection,
    decode_timeline,
    encode_timeline,
    result_hash,
    semantic_key,
    verify_projection,
)
from autplay.domain.face import FaceContractError, require_same_result

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/face/v1"


def test_shared_canonical_hashes_and_unknown_values_round_trip() -> None:
    timeline = decode_timeline((FIXTURES / "timeline.json").read_bytes())
    expected = json.loads((FIXTURES / "hashes.json").read_text())
    assert encode_timeline(timeline) == (FIXTURES / "timeline.canonical.json").read_bytes()
    assert semantic_key(timeline.identity).hex() == expected["semantic_key"]
    assert result_hash(timeline).hex() == expected["result_hash"]
    assert decode_timeline(encode_timeline(timeline)) == timeline
    assert timeline.track_character.axes["future_axis"].value == -0.1
    assert timeline.track_character.axes["valence"].abstained
    assert "missing" not in timeline.track_character.axes
    assert timeline.events[0].event_type == "future_event"
    with pytest.raises(TypeError):
        timeline.track_character.axes["injected"] = timeline.track_character.axes["energy"]  # type: ignore[index]


@pytest.mark.parametrize(
    "field",
    [
        "recording_id",
        "audio_variant_id",
        "source_sha256",
        "source_duration_ms",
        "embedding_model_id",
        "embedding_manifest_sha256",
        "semantic_interpreter_id",
        "interpreter_manifest_sha256",
        "preprocessing_sha256",
    ],
)
def test_every_source_model_and_interpreter_field_changes_semantic_identity(field: str) -> None:
    identity = decode_timeline((FIXTURES / "timeline.json").read_bytes()).identity
    original = getattr(identity, field)
    changed = (
        UUID(int=55)
        if isinstance(original, UUID)
        else (b"z" * 32 if isinstance(original, bytes) else original + 1)
    )
    candidate = replace(identity, **{field: changed})  # type: ignore[arg-type]
    assert semantic_key(candidate) != semantic_key(identity)


def test_projection_is_owner_scoped_but_epoch_cannot_duplicate_semantics() -> None:
    timeline = decode_timeline((FIXTURES / "timeline.json").read_bytes())
    binding = decode_projection((FIXTURES / "projection.json").read_bytes())
    verify_projection(timeline, binding)
    verify_projection(timeline, replace(binding, activation_epoch=2, user_id=UUID(int=9)))
    with pytest.raises(FaceContractError, match=r"ml\.face\.integrity_mismatch"):
        verify_projection(timeline, replace(binding, result_hash=b"x" * 32))
    with pytest.raises(FaceContractError, match=r"ml\.face\.identity_mismatch"):
        verify_projection(
            timeline,
            replace(binding, identity=replace(timeline.identity, recording_id=UUID(int=6))),
        )
    require_same_result(binding.result_hash, binding.result_hash)
    with pytest.raises(FaceContractError, match=r"ml\.face\.result_conflict"):
        require_same_result(binding.result_hash, b"x" * 32)


@pytest.mark.parametrize("case", json.loads((FIXTURES / "invalid-cases.json").read_text()))
def test_shared_invalid_documents(case: dict[str, object]) -> None:
    raw = (FIXTURES / "timeline.json").read_text()
    document = json.loads(raw)
    path = str(case["path"]).split(".")
    parent = document
    for key in path[:-1]:
        parent = parent[int(key)] if isinstance(parent, list) else parent[key]
    final_key = int(path[-1]) if isinstance(parent, list) else path[-1]
    parent[final_key] = case["value"]
    with pytest.raises(FaceContractError):
        decode_timeline(json.dumps(document).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"axes": {"energy":1,"en\\u0065rgy":2}}',
        b"[" * 13 + b"]" * 13,
        b" " * 1_048_577,
        b"\xff",
    ],
    ids=["duplicate", "nan", "infinity", "escaped-duplicate", "depth", "size", "utf8"],
)
def test_malformed_input_fails_bounded_with_stable_errors(raw: bytes) -> None:
    with pytest.raises(FaceContractError):
        decode_timeline(raw)


def test_huge_numeric_tokens_do_not_escape_as_overflow_errors() -> None:
    raw = (FIXTURES / "timeline.json").read_text().replace('"value": 0.2', '"value": ' + "9" * 400)
    with pytest.raises(FaceContractError):
        decode_timeline(raw.encode())
