"""Public catalogue descriptions never authorize source acquisition or identity merges."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .library import LibraryCommandError
from .music_search import normalize_music_query
from .track_metadata import version_markers

MAX_DISCOVERY_OFFSET = 1000
MAX_RELEASE_TRACKS = 5000


class MusicDiscoveryKind(StrEnum):
    ARTIST = "artist"
    ALBUM = "album"


class MusicDiscoveryEntity(StrEnum):
    ARTIST = "artist"
    RELEASE = "release"
    RECORDING = "recording"
    RELEASE_TRACK = "release_track"


class MusicDiscoveryValidationError(ValueError):
    pass


def discovery_query(query: str) -> str:
    try:
        return normalize_music_query(query)
    except LibraryCommandError:
        raise MusicDiscoveryValidationError("music_discovery_query_invalid") from None


def discovery_page_bounds(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise MusicDiscoveryValidationError("music_discovery_limit_invalid")
    if type(offset) is not int or not 0 <= offset <= MAX_DISCOVERY_OFFSET:
        raise MusicDiscoveryValidationError("music_discovery_offset_invalid")


@dataclass(frozen=True, slots=True)
class MusicDiscoveryCard:
    entity_type: MusicDiscoveryEntity
    entity_id: UUID
    title: str
    artist: str | None = None
    release_id: UUID | None = None
    recording_id: UUID | None = None
    release_date: str | None = None
    country: str | None = None
    disambiguation: str | None = None
    duration_ms: int | None = None
    disc_number: int | None = None
    track_number: int | None = None
    recording_title: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > 500:
            raise MusicDiscoveryValidationError("music_discovery_response_invalid")
        for value in (self.artist, self.country, self.disambiguation, self.recording_title):
            if value is not None and (not value.strip() or len(value) > 500):
                raise MusicDiscoveryValidationError("music_discovery_response_invalid")
        for number, maximum in (
            (self.duration_ms, 86400000),
            (self.disc_number, 1000),
            (self.track_number, 10000),
        ):
            if number is not None and (type(number) is not int or not 1 <= number <= maximum):
                raise MusicDiscoveryValidationError("music_discovery_response_invalid")

    @property
    def namespaced_id(self) -> str:
        return f"musicbrainz:{self.entity_type.value}:{self.entity_id}"

    @property
    def download_search_query(self) -> str | None:
        if (
            self.entity_type
            not in {MusicDiscoveryEntity.RECORDING, MusicDiscoveryEntity.RELEASE_TRACK}
            or not self.artist
        ):
            return None
        # A suggestion starts a separate source lookup; it is never a selected source identity.
        title = self.title
        observed = version_markers(title)
        if self.recording_title:
            recording_versions = version_markers(self.recording_title)
            if observed - recording_versions and recording_versions - observed:
                return None
            if recording_versions - observed:
                title = self.recording_title
                observed = recording_versions
        if self.disambiguation and version_markers(self.disambiguation) - observed:
            title = f"{title} ({self.disambiguation})"
        try:
            return discovery_query(f"{self.artist} {title}")
        except MusicDiscoveryValidationError:
            return None

    def view(self) -> dict[str, object]:
        return {
            "id": self.namespaced_id,
            "entity_type": self.entity_type.value,
            "entity_id": str(self.entity_id),
            "title": self.title,
            "artist": self.artist,
            "release_id": str(self.release_id) if self.release_id else None,
            "recording_id": str(self.recording_id) if self.recording_id else None,
            "release_date": self.release_date,
            "country": self.country,
            "disambiguation": self.disambiguation,
            "duration_ms": self.duration_ms,
            "disc_number": self.disc_number,
            "track_number": self.track_number,
            "recording_title": self.recording_title,
            "source": "MusicBrainz",
            "availability": "METADATA_ONLY",
            "acquisition_allowed": False,
            "can_browse_tracks": self.entity_type
            in {MusicDiscoveryEntity.ARTIST, MusicDiscoveryEntity.RELEASE},
            "download_search_query": self.download_search_query,
        }


@dataclass(frozen=True, slots=True)
class MusicDiscoveryPage:
    items: tuple[MusicDiscoveryCard, ...]
    limit: int
    offset: int
    total_count: int
    source_page_count: int

    def __post_init__(self) -> None:
        discovery_page_bounds(self.limit, self.offset)
        if (
            type(self.total_count) is not int
            or not 0 <= self.total_count <= 1000000
            or type(self.source_page_count) is not int
            or not len(self.items) <= self.source_page_count <= self.limit
            or len({item.namespaced_id for item in self.items}) != len(self.items)
        ):
            raise MusicDiscoveryValidationError("music_discovery_response_invalid")

    def view(self) -> dict[str, object]:
        following = self.offset + self.source_page_count
        more = following < self.total_count
        next_offset = (
            following
            if more and self.source_page_count and following <= MAX_DISCOVERY_OFFSET
            else None
        )
        return {
            "contract_version": "music-discovery-v1",
            "source": "MusicBrainz",
            "source_scope": "INTERNET",
            "availability": "METADATA_ONLY",
            "acquisition_allowed": False,
            "capabilities": {"direct_acquisition": False, "track_source_search": True},
            "items": [item.view() for item in self.items],
            "limit": self.limit,
            "offset": self.offset,
            "total_count": self.total_count,
            "next_offset": next_offset,
            "truncated": more and next_offset is None,
        }
