"""Canonical Face timeline v2 result bytes and domain-separated hash."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, cast

import rfc8785

from autplay.domain.face import FaceAxis, FaceContractError, FaceSemanticState
from autplay.domain.face_presentation_map import parse_source_presentation_map
from autplay.domain.face_v2_identity import parse_face_v2_identity
from autplay.domain.face_v2_timeline import (
    FaceTimelineV2,
    FaceV2Event,
    FaceV2Keyframe,
    FaceV2TimelineError,
)

FACE_V2_CONTENT_TYPE = "application/vnd.autplay.face-timeline.v2+json"
FACE_V2_RESULT_DOMAIN = b"autplay.face.timeline-result.v2\0"
MAX_FACE_V2_TIMELINE_BYTES = 1_048_576
MAX_FACE_V2_JSON_DEPTH = 12


def face_v2_timeline_document(value: FaceTimelineV2) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "identity": value.identity.document(),
        "source_presentation_map": value.presentation_map.document(),
        "track_character": _state_document(value.track_character),
        "keyframes": [
            {"sample_index": frame.sample_index, "state": _state_document(frame.state)}
            for frame in value.keyframes
        ],
        "events": [
            {
                "sample_index": event.sample_index,
                "event_type": event.event_type,
                "strength": event.strength,
                "confidence": event.confidence,
            }
            for event in value.events
        ],
    }


def encode_face_v2_timeline(value: FaceTimelineV2) -> bytes:
    try:
        encoded = rfc8785.dumps(face_v2_timeline_document(value))
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise FaceV2TimelineError("Face v2 timeline cannot be canonicalized") from error
    if len(encoded) > MAX_FACE_V2_TIMELINE_BYTES:
        raise FaceV2TimelineError("Face v2 timeline exceeds byte bound")
    return encoded


def face_v2_result_hash(value: FaceTimelineV2) -> str:
    return sha256(FACE_V2_RESULT_DOMAIN + encode_face_v2_timeline(value)).hexdigest()


def decode_face_v2_timeline(raw: bytes) -> FaceTimelineV2:
    """Accept exact canonical bytes, including the map and decoded-sample identity."""

    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_FACE_V2_TIMELINE_BYTES:
        raise FaceV2TimelineError("Face v2 timeline byte bound invalid")
    _check_nesting(raw)

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FaceV2TimelineError("duplicate Face v2 timeline field")
            result[key] = value
        return result

    def invalid_constant(_token: str) -> None:
        raise FaceV2TimelineError("Face v2 timeline has non-finite number")

    try:
        root = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant
        )
        if (
            not isinstance(root, dict)
            or set(root)
            != {
                "schema_version",
                "identity",
                "source_presentation_map",
                "track_character",
                "keyframes",
                "events",
            }
            or type(root["schema_version"]) is not int
            or root["schema_version"] != 2
        ):
            raise FaceV2TimelineError("invalid Face v2 timeline fields")
        presentation_map = parse_source_presentation_map(
            rfc8785.dumps(root["source_presentation_map"])
        )
        identity = parse_face_v2_identity(rfc8785.dumps(root["identity"]), presentation_map)
        frames = root["keyframes"]
        events = root["events"]
        if (
            not isinstance(frames, list)
            or len(frames) > 4_096
            or not isinstance(events, list)
            or len(events) > 4_096
        ):
            raise FaceV2TimelineError("invalid Face v2 timeline arrays")
        keyframes = tuple(_frame(value) for value in frames)
        parsed_events = tuple(_event(value) for value in events)
        timeline = FaceTimelineV2(
            identity,
            presentation_map,
            _state(root["track_character"]),
            keyframes,
            parsed_events,
        )
        if encode_face_v2_timeline(timeline) != raw:
            raise FaceV2TimelineError("noncanonical Face v2 timeline bytes")
        return timeline
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        OverflowError,
        rfc8785.CanonicalizationError,
        FaceContractError,
        ValueError,
        TypeError,
        KeyError,
    ) as error:
        if isinstance(error, FaceV2TimelineError):
            raise
        raise FaceV2TimelineError("invalid Face v2 timeline document") from error


def _state_document(value: FaceSemanticState) -> dict[str, Any]:
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


def _state(raw: object) -> FaceSemanticState:
    if not isinstance(raw, dict) or set(raw) != {"axes"} or not isinstance(raw["axes"], dict):
        raise FaceV2TimelineError("invalid Face v2 state")
    if len(raw["axes"]) > 64:
        raise FaceV2TimelineError("Face v2 axis count exceeds bound")
    axes: dict[str, FaceAxis] = {}
    for name, value in raw["axes"].items():
        if not isinstance(value, dict) or set(value) != {
            "value",
            "confidence",
            "abstained",
            "reason_code",
        }:
            raise FaceV2TimelineError("invalid Face v2 axis")
        axes[name] = FaceAxis(
            value=value["value"],
            confidence=value["confidence"],
            abstained=value["abstained"],
            reason_code=value["reason_code"],
        )
    return FaceSemanticState(axes)


def _frame(raw: object) -> FaceV2Keyframe:
    if not isinstance(raw, dict) or set(raw) != {"sample_index", "state"}:
        raise FaceV2TimelineError("invalid Face v2 keyframe fields")
    return FaceV2Keyframe(cast(int, raw["sample_index"]), _state(raw["state"]))


def _event(raw: object) -> FaceV2Event:
    if not isinstance(raw, dict) or set(raw) != {
        "sample_index",
        "event_type",
        "strength",
        "confidence",
    }:
        raise FaceV2TimelineError("invalid Face v2 event fields")
    return FaceV2Event(
        cast(int, raw["sample_index"]),
        cast(str, raw["event_type"]),
        cast(float, raw["strength"]),
        cast(float, raw["confidence"]),
    )


def _check_nesting(raw: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
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
            if depth > MAX_FACE_V2_JSON_DEPTH:
                raise FaceV2TimelineError("Face v2 JSON depth exceeds bound")
        elif byte in (93, 125):
            depth -= 1
