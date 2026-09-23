"""Face v2 decoded-sample timeline values; Face v1 remains immutable."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from autplay.domain.face import NAME, FaceSemanticState
from autplay.domain.face_presentation_map import SourcePresentationMapV1
from autplay.domain.face_v2_identity import FaceTimelineIdentityV2, FaceV2IdentityError

MAX_FACE_V2_KEYFRAMES = 4_096
MAX_FACE_V2_EVENTS = 4_096


class FaceV2TimelineError(ValueError):
    """Invalid decoded-sample timeline; selection must remain neutral."""


@dataclass(frozen=True, slots=True)
class FaceV2Keyframe:
    sample_index: int
    state: FaceSemanticState


@dataclass(frozen=True, slots=True)
class FaceV2Event:
    sample_index: int
    event_type: str
    strength: float
    confidence: float


@dataclass(frozen=True, slots=True)
class FaceTimelineV2:
    identity: FaceTimelineIdentityV2
    presentation_map: SourcePresentationMapV1
    track_character: FaceSemanticState
    keyframes: tuple[FaceV2Keyframe, ...]
    events: tuple[FaceV2Event, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "keyframes", tuple(self.keyframes))
        object.__setattr__(self, "events", tuple(self.events))
        if (
            not isinstance(self.identity, FaceTimelineIdentityV2)
            or not isinstance(self.presentation_map, SourcePresentationMapV1)
            or not isinstance(self.track_character, FaceSemanticState)
            or len(self.keyframes) > MAX_FACE_V2_KEYFRAMES
            or len(self.events) > MAX_FACE_V2_EVENTS
        ):
            raise FaceV2TimelineError("invalid Face v2 timeline structure")
        try:
            self.identity.bind_map(self.presentation_map)
        except FaceV2IdentityError as error:
            raise FaceV2TimelineError("Face v2 timeline map mismatch") from error
        limit = self.identity.decoded_sample_count
        previous_frame = -1
        axes = set(self.track_character.axes)
        for frame in self.keyframes:
            if (
                not isinstance(frame, FaceV2Keyframe)
                or type(frame.sample_index) is not int
                or not 0 <= frame.sample_index < limit
                or frame.sample_index <= previous_frame
                or not isinstance(frame.state, FaceSemanticState)
            ):
                raise FaceV2TimelineError("invalid Face v2 keyframe")
            axes.update(frame.state.axes)
            previous_frame = frame.sample_index
        if len(axes) > 64:
            raise FaceV2TimelineError("Face v2 axis count exceeds bound")
        previous_event: tuple[int, str] | None = None
        for event in self.events:
            if (
                not isinstance(event, FaceV2Event)
                or type(event.sample_index) is not int
                or not 0 <= event.sample_index < limit
                or not isinstance(event.event_type, str)
                or not NAME.fullmatch(event.event_type)
                or type(event.strength) not in (float, int)
                or not isfinite(event.strength)
                or not 0 <= event.strength <= 1
                or type(event.confidence) not in (float, int)
                or not isfinite(event.confidence)
                or not 0 <= event.confidence <= 1
            ):
                raise FaceV2TimelineError("invalid Face v2 event")
            key = (event.sample_index, event.event_type)
            if previous_event is not None and key <= previous_event:
                raise FaceV2TimelineError("Face v2 events are not ordered")
            previous_event = key
