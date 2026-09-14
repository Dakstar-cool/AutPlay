"""Bounded Face v1 wire codec and domain-separated RFC 8785 integrity identities."""

from __future__ import annotations

import hashlib
import json
import re
from typing import cast
from uuid import UUID

import rfc8785

from autplay.domain.face import (
    MAX_TIMELINE_BYTES,
    FaceAxis,
    FaceContractError,
    FaceEvent,
    FaceKeyframe,
    FaceProjectionBinding,
    FaceSemanticState,
    FaceTimelineIdentity,
    TemporalFaceTimeline,
)

IDENTITY_FIELDS = {
    "schema_version",
    "recording_id",
    "audio_variant_id",
    "source_sha256",
    "source_duration_ms",
    "source_timebase",
    "embedding_model_id",
    "embedding_manifest_sha256",
    "semantic_interpreter_id",
    "interpreter_manifest_sha256",
    "preprocessing_sha256",
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_DEPTH = 12
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or value.keys() != fields:
        raise FaceContractError()
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if type(value) is not list:
        raise FaceContractError()
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise FaceContractError()
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise FaceContractError()
    return value


def _number(value: object) -> float:
    if type(value) not in (float, int) or not isinstance(value, (float, int)):
        raise FaceContractError()
    try:
        return float(value)
    except OverflowError as error:
        raise FaceContractError() from error


def _uuid(value: object) -> UUID:
    text = _string(value)
    try:
        result = UUID(text)
    except ValueError as error:
        raise FaceContractError() from error
    if str(result) != text:
        raise FaceContractError()
    return result


def _hash(value: object) -> bytes:
    text = _string(value)
    if not HEX.fullmatch(text):
        raise FaceContractError()
    return bytes.fromhex(text)


def _identity(value: object) -> FaceTimelineIdentity:
    row = _object(value, IDENTITY_FIELDS)
    return FaceTimelineIdentity(
        recording_id=_uuid(row["recording_id"]),
        audio_variant_id=_uuid(row["audio_variant_id"]),
        source_sha256=_hash(row["source_sha256"]),
        source_duration_ms=_integer(row["source_duration_ms"]),
        embedding_model_id=_uuid(row["embedding_model_id"]),
        embedding_manifest_sha256=_hash(row["embedding_manifest_sha256"]),
        semantic_interpreter_id=_uuid(row["semantic_interpreter_id"]),
        interpreter_manifest_sha256=_hash(row["interpreter_manifest_sha256"]),
        preprocessing_sha256=_hash(row["preprocessing_sha256"]),
        schema_version=_integer(row["schema_version"]),
        source_timebase=_string(row["source_timebase"]),
    )


def _state(value: object) -> FaceSemanticState:
    raw_axes = _object(value, {"axes"})["axes"]
    if type(raw_axes) is not dict:
        raise FaceContractError()
    axes = {}
    for name, value in raw_axes.items():
        row = _object(value, {"value", "confidence", "abstained", "reason_code"})
        if type(row["abstained"]) is not bool:
            raise FaceContractError()
        axes[name] = FaceAxis(
            value=None if row["value"] is None else _number(row["value"]),
            confidence=None if row["confidence"] is None else _number(row["confidence"]),
            abstained=row["abstained"],
            reason_code=None if row["reason_code"] is None else _string(row["reason_code"]),
        )
    return FaceSemanticState(axes)


def identity_document(value: FaceTimelineIdentity) -> dict[str, JsonValue]:
    return {
        "schema_version": value.schema_version,
        "recording_id": str(value.recording_id),
        "audio_variant_id": str(value.audio_variant_id),
        "source_sha256": value.source_sha256.hex(),
        "source_duration_ms": value.source_duration_ms,
        "source_timebase": value.source_timebase,
        "embedding_model_id": str(value.embedding_model_id),
        "embedding_manifest_sha256": value.embedding_manifest_sha256.hex(),
        "semantic_interpreter_id": str(value.semantic_interpreter_id),
        "interpreter_manifest_sha256": value.interpreter_manifest_sha256.hex(),
        "preprocessing_sha256": value.preprocessing_sha256.hex(),
    }


def state_document(value: FaceSemanticState) -> dict[str, JsonValue]:
    return {
        "axes": {
            name: {
                "value": axis.value,
                "confidence": axis.confidence,
                "abstained": axis.abstained,
                "reason_code": axis.reason_code,
            }
            for name, axis in value.axes.items()
        }
    }


def timeline_document(value: TemporalFaceTimeline) -> dict[str, JsonValue]:
    return {
        "schema_version": value.schema_version,
        "identity": identity_document(value.identity),
        "track_character": state_document(value.track_character),
        "keyframes": [
            {"time_ms": frame.time_ms, "state": state_document(frame.state)}
            for frame in value.keyframes
        ],
        "events": [
            {
                "time_ms": event.time_ms,
                "event_type": event.event_type,
                "strength": event.strength,
                "confidence": event.confidence,
            }
            for event in value.events
        ],
    }


def encode_timeline(value: TemporalFaceTimeline) -> bytes:
    encoded = rfc8785.dumps(timeline_document(value))
    if len(encoded) > MAX_TIMELINE_BYTES:
        raise FaceContractError("ml.face.timeline_too_large")
    return encoded


def semantic_key(identity: FaceTimelineIdentity) -> bytes:
    return hashlib.sha256(
        b"autplay.face.semantic-key.v1\0" + rfc8785.dumps(identity_document(identity))
    ).digest()


def result_hash(timeline: TemporalFaceTimeline) -> bytes:
    return hashlib.sha256(b"autplay.face.timeline-result.v1\0" + encode_timeline(timeline)).digest()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FaceContractError()
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise FaceContractError()


def _bounded_json(data: bytes) -> object:
    if not data or len(data) > MAX_TIMELINE_BYTES:
        raise FaceContractError("ml.face.timeline_too_large")
    # Check nesting before allocating objects or recursing in the JSON decoder.
    depth = 0
    in_string = False
    escaped = False
    for byte in data:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise FaceContractError()
        elif byte in (93, 125):
            depth -= 1
    try:
        return cast(
            object,
            json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_unique_pairs,
                parse_constant=_reject_constant,
            ),
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FaceContractError() from error


def decode_timeline(data: bytes) -> TemporalFaceTimeline:
    root = _object(
        _bounded_json(data),
        {"schema_version", "identity", "track_character", "keyframes", "events"},
    )
    frames = []
    for value in _list(root["keyframes"]):
        row = _object(value, {"time_ms", "state"})
        frames.append(FaceKeyframe(_integer(row["time_ms"]), _state(row["state"])))
    events = []
    for value in _list(root["events"]):
        row = _object(value, {"time_ms", "event_type", "strength", "confidence"})
        events.append(
            FaceEvent(
                _integer(row["time_ms"]),
                _string(row["event_type"]),
                _number(row["strength"]),
                _number(row["confidence"]),
            )
        )
    timeline = TemporalFaceTimeline(
        _identity(root["identity"]),
        _state(root["track_character"]),
        tuple(frames),
        tuple(events),
        _integer(root["schema_version"]),
    )
    encode_timeline(timeline)
    return timeline


def projection_document(value: FaceProjectionBinding) -> dict[str, JsonValue]:
    return {
        "server_profile_id": str(value.server_profile_id),
        "user_id": str(value.user_id),
        "identity": identity_document(value.identity),
        "semantic_key": value.semantic_key.hex(),
        "result_hash": value.result_hash.hex(),
        "activation_epoch": value.activation_epoch,
    }


def decode_projection(data: bytes) -> FaceProjectionBinding:
    row = _object(
        _bounded_json(data),
        {
            "server_profile_id",
            "user_id",
            "identity",
            "semantic_key",
            "result_hash",
            "activation_epoch",
        },
    )
    binding = FaceProjectionBinding(
        _uuid(row["server_profile_id"]),
        _uuid(row["user_id"]),
        _identity(row["identity"]),
        _hash(row["semantic_key"]),
        _hash(row["result_hash"]),
        _integer(row["activation_epoch"]),
    )
    if binding.semantic_key != semantic_key(binding.identity):
        raise FaceContractError("ml.face.identity_mismatch")
    return binding


def verify_projection(timeline: TemporalFaceTimeline, binding: FaceProjectionBinding) -> None:
    if (
        timeline.identity != binding.identity
        or semantic_key(timeline.identity) != binding.semantic_key
    ):
        raise FaceContractError("ml.face.identity_mismatch")
    if result_hash(timeline) != binding.result_hash:
        raise FaceContractError("ml.face.integrity_mismatch")
