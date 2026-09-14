"""Immutable, framework-independent musical-character contract values."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

MAX_AXES = 64
MAX_KEYFRAMES = 4096
MAX_EVENTS = 4096
MAX_DURATION_MS = 86_400_000
MAX_TIMELINE_BYTES = 1_048_576
SOURCE_TIMEBASE = "SOURCE_MILLISECONDS_V1"
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
REASON = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class FaceContractError(ValueError):
    """Stable, payload-free contract failure suitable for fallback decisions."""

    def __init__(self, code: str = "ml.face.invalid_timeline") -> None:
        super().__init__(code)
        self.code = code


def bounded_integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FaceContractError()


def finite_number(value: object, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not isinstance(value, (int, float)):
        raise FaceContractError()
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise FaceContractError()


def digest(value: object) -> None:
    if type(value) is not bytes or len(value) != 32:
        raise FaceContractError()


@dataclass(frozen=True, slots=True)
class FaceAxis:
    value: float | None
    confidence: float | None
    abstained: bool
    reason_code: str | None

    def __post_init__(self) -> None:
        if type(self.abstained) is not bool:
            raise FaceContractError()
        if self.abstained:
            if self.value is not None or self.confidence is not None:
                raise FaceContractError()
            if not isinstance(self.reason_code, str) or not REASON.fullmatch(self.reason_code):
                raise FaceContractError()
        else:
            finite_number(self.value, -1, 1)
            finite_number(self.confidence, 0, 1)
            if self.reason_code is not None:
                raise FaceContractError()


@dataclass(frozen=True, slots=True)
class FaceSemanticState:
    axes: Mapping[str, FaceAxis]

    def __post_init__(self) -> None:
        if len(self.axes) > MAX_AXES:
            raise FaceContractError()
        for name, axis in self.axes.items():
            if (
                not isinstance(name, str)
                or not NAME.fullmatch(name)
                or not isinstance(axis, FaceAxis)
            ):
                raise FaceContractError()
        object.__setattr__(self, "axes", MappingProxyType(dict(self.axes)))


@dataclass(frozen=True, slots=True)
class FaceTimelineIdentity:
    recording_id: UUID
    audio_variant_id: UUID
    source_sha256: bytes
    source_duration_ms: int
    embedding_model_id: UUID
    embedding_manifest_sha256: bytes
    semantic_interpreter_id: UUID
    interpreter_manifest_sha256: bytes
    preprocessing_sha256: bytes
    schema_version: int = 1
    source_timebase: str = SOURCE_TIMEBASE

    def __post_init__(self) -> None:
        bounded_integer(self.schema_version, 1, 1)
        bounded_integer(self.source_duration_ms, 1, MAX_DURATION_MS)
        if self.source_timebase != SOURCE_TIMEBASE:
            raise FaceContractError()
        for identifier in (
            self.recording_id,
            self.audio_variant_id,
            self.embedding_model_id,
            self.semantic_interpreter_id,
        ):
            if type(identifier) is not UUID:
                raise FaceContractError()
        for value in (
            self.source_sha256,
            self.embedding_manifest_sha256,
            self.interpreter_manifest_sha256,
            self.preprocessing_sha256,
        ):
            digest(value)


@dataclass(frozen=True, slots=True)
class FaceKeyframe:
    time_ms: int
    state: FaceSemanticState

    def __post_init__(self) -> None:
        bounded_integer(self.time_ms, 0, MAX_DURATION_MS)
        if not isinstance(self.state, FaceSemanticState):
            raise FaceContractError()


@dataclass(frozen=True, slots=True)
class FaceEvent:
    time_ms: int
    event_type: str
    strength: float
    confidence: float

    def __post_init__(self) -> None:
        bounded_integer(self.time_ms, 0, MAX_DURATION_MS)
        if not isinstance(self.event_type, str) or not NAME.fullmatch(self.event_type):
            raise FaceContractError()
        finite_number(self.strength, 0, 1)
        finite_number(self.confidence, 0, 1)


@dataclass(frozen=True, slots=True)
class TemporalFaceTimeline:
    identity: FaceTimelineIdentity
    track_character: FaceSemanticState
    keyframes: tuple[FaceKeyframe, ...]
    events: tuple[FaceEvent, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        bounded_integer(self.schema_version, 1, 1)
        if not isinstance(self.identity, FaceTimelineIdentity) or not isinstance(
            self.track_character, FaceSemanticState
        ):
            raise FaceContractError()
        if len(self.keyframes) > MAX_KEYFRAMES or len(self.events) > MAX_EVENTS:
            raise FaceContractError()
        object.__setattr__(self, "keyframes", tuple(self.keyframes))
        object.__setattr__(self, "events", tuple(self.events))
        axes = set(self.track_character.axes)
        previous = -1
        for frame in self.keyframes:
            if not isinstance(frame, FaceKeyframe):
                raise FaceContractError()
            if frame.time_ms <= previous or frame.time_ms > self.identity.source_duration_ms:
                raise FaceContractError()
            axes.update(frame.state.axes)
            previous = frame.time_ms
        if self.keyframes and self.keyframes[0].time_ms != 0:
            raise FaceContractError()
        if len(axes) > MAX_AXES:
            raise FaceContractError()
        previous_event: tuple[int, str] | None = None
        for event in self.events:
            if not isinstance(event, FaceEvent) or event.time_ms > self.identity.source_duration_ms:
                raise FaceContractError()
            key = event.time_ms, event.event_type
            if previous_event is not None and key <= previous_event:
                raise FaceContractError()
            previous_event = key


@dataclass(frozen=True, slots=True)
class FaceProjectionBinding:
    """Authorization provenance, not proof of present server access or a public capability."""

    server_profile_id: UUID
    user_id: UUID
    identity: FaceTimelineIdentity
    semantic_key: bytes
    result_hash: bytes
    activation_epoch: int

    def __post_init__(self) -> None:
        if type(self.server_profile_id) is not UUID or type(self.user_id) is not UUID:
            raise FaceContractError()
        if not isinstance(self.identity, FaceTimelineIdentity):
            raise FaceContractError()
        digest(self.semantic_key)
        digest(self.result_hash)
        bounded_integer(self.activation_epoch, 1, 9_007_199_254_740_991)


def require_same_result(existing_hash: bytes, candidate_hash: bytes) -> None:
    """Only call after matching semantic identity; a digest never authorizes object access."""
    digest(existing_hash)
    digest(candidate_hash)
    if existing_hash != candidate_hash:
        raise FaceContractError("ml.face.result_conflict")
