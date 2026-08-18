"""Pure P07 library and playlist command values and invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from string import ascii_lowercase, digits
from uuid import UUID


class LibraryCommandError(ValueError):
    """A stable, caller-safe command failure."""

    code = "library_command_invalid"


class StaleVersionError(LibraryCommandError):
    code = "row_version_conflict"


class PreferenceValue(StrEnum):
    NEUTRAL = "NEUTRAL"
    LIKED = "LIKED"
    DISLIKED = "DISLIKED"


class AvailabilityStatus(StrEnum):
    LOCAL = "LOCAL"
    VAULT = "VAULT"
    EXTERNAL = "EXTERNAL"
    PENDING = "PENDING"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class RecommendationAttribution:
    """Bounded opaque P04-compatible intent retained by the owning client event.

    P07 validates this input but does not create a server interaction projection;
    P09 owns canonical server materialization.
    """

    recommendation_request_id: UUID
    recording_id: UUID
    source_rank: int
    source: str
    surface: str
    presentation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.source_rank <= 1_000:
            raise LibraryCommandError("recommendation_attribution_invalid")
        allowed = frozenset(ascii_lowercase + digits + "_")
        for value in (self.source, self.surface):
            if (
                not 1 <= len(value) <= 100
                or value[0] not in ascii_lowercase
                or any(character not in allowed for character in value)
            ):
                raise LibraryCommandError("recommendation_attribution_invalid")


@dataclass(frozen=True, slots=True)
class CreateUnresolvedTrack:
    user_track_ref_id: UUID
    title: str | None
    artist: str | None
    album: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if not any(value and value.strip() for value in (self.title, self.artist, self.album)):
            raise LibraryCommandError("unresolved_track_metadata_required")
        if self.duration_ms is not None and self.duration_ms <= 0:
            raise LibraryCommandError("track_duration_invalid")
        for value in (self.title, self.artist, self.album):
            if value is not None and len(value) > 1_000:
                raise LibraryCommandError("track_metadata_too_long")


@dataclass(frozen=True, slots=True)
class AppendListeningEvent:
    listening_event_id: UUID
    user_track_ref_id: UUID
    started_at: datetime
    played_ms: int
    track_duration_ms: int | None = None
    event_origin: str = "ORGANIC"
    context: str = "GENERAL"
    explicit_feedback: str = "NONE"
    excluded_from_taste: bool = False
    attribution: RecommendationAttribution | None = None

    def __post_init__(self) -> None:
        if self.played_ms < 0:
            raise LibraryCommandError("played_duration_invalid")
        if self.track_duration_ms is not None and self.track_duration_ms <= 0:
            raise LibraryCommandError("track_duration_invalid")
        if self.event_origin not in {"ORGANIC", "RECOMMENDED", "PLAYLIST", "SEARCH", "WAVE"}:
            raise LibraryCommandError("listening_origin_invalid")
        if self.event_origin == "RECOMMENDED" and self.attribution is None:
            raise LibraryCommandError("recommendation_attribution_required")
        if self.context not in {"GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"}:
            raise LibraryCommandError("listening_context_invalid")
        if self.explicit_feedback not in {"NONE", "LIKE", "DISLIKE"}:
            raise LibraryCommandError("listening_feedback_invalid")


def validate_playlist_name(name: str) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= 500:
        raise LibraryCommandError("playlist_name_invalid")
    return cleaned


def validate_position_key(position_key: str) -> str:
    if not 1 <= len(position_key) <= 128:
        raise LibraryCommandError("playlist_position_invalid")
    return position_key


__all__ = (
    "AppendListeningEvent",
    "AvailabilityStatus",
    "CreateUnresolvedTrack",
    "LibraryCommandError",
    "PreferenceValue",
    "RecommendationAttribution",
    "StaleVersionError",
    "validate_playlist_name",
    "validate_position_key",
)
