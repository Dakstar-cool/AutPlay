"""Public catalogue descriptions never authorize source acquisition or identity merges."""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .featured_artists import featured_names
from .library import LibraryCommandError
from .music_search import normalize_music_query
from .music_variations import split_label
from .title_edits import (
    edit_parts,
    edition_label,
    joined_title_matches,
    session_label,
    session_parts,
)
from .track_metadata import normalized, version_markers

MAX_DISCOVERY_OFFSET = 1000
MAX_BROWSE_OFFSET = 1_000_000
MAX_RELEASE_TRACKS = 5000

_BARE_VERSION = re.compile(
    r"\s+(remix|ремикс|acoustic|karaoke|demo|radio edit|remaster(?:ed)?)$", re.I
)
_STANDARD_LABELS = {
    "studio version",
    "studio recording",
    "album version",
    "original version",
    "original mix",
    "official audio",
    "студийная версия",
    "студийная запись",
    "альбомная версия",
}


def song_version(title: str) -> tuple[str, frozenset[str]]:
    base, session = session_parts(title)
    if session:
        return base, frozenset({session.casefold()})
    base, edition = edit_parts(title)
    if not edition and title.casefold().endswith((" speed up", " instrumental")):
        base, edition = edit_parts(title, search=True)
    if edition:
        return base, frozenset({edition.casefold()})
    part = split_label(title)
    if not part and "|" in title:
        before, after = title.rsplit("|", 1)
        part = before.strip(), after.strip()
    if part and part[0] and part[1].casefold() in _STANDARD_LABELS:
        return song_version(part[0])
    if part and (markers := version_markers(part[1])):
        return part[0], markers
    suffix = _BARE_VERSION.search(title)
    if suffix:
        return title[: suffix.start()], version_markers(suffix[1])
    return title, frozenset()


def source_preference(query: str, title: str, artist: str) -> tuple[bool, bool, bool]:
    """Rank the requested song first, and preserve provider relevance within each tier."""
    base, candidate_version = song_version(title)
    wanted, requested_version = song_version(query)
    # An English free-text query often omits the brackets around speed or instrumental versions.
    if not requested_version:
        hinted, edition = edit_parts(query, search=True)
        if edition:
            wanted, requested_version = hinted, frozenset({edition.casefold()})
    exact = normalized(query) in {normalized(title), normalized(f"{artist} {title}")}
    same_song = normalized(wanted) in {normalized(base), normalized(f"{artist} {base}")}
    if not same_song:
        wanted_title = wanted.casefold().removeprefix(artist.casefold() + " ")
        same_song = joined_title_matches(wanted_title, base)
    return not exact, not same_song, requested_version != candidate_version


def source_title(title: str, recording_title: str | None, note: str | None) -> str | None:
    """Preserve catalogue edition evidence when searching and selecting an audio source."""
    track_session = session_parts(title)[1]
    recording_session = session_parts(recording_title or "")[1]
    note_session = session_label(note or "")
    if len({s.casefold() for s in (track_session, recording_session, note_session) if s}) > 1:
        return None
    observed = version_markers(title)
    if recording_title:
        recording_versions = version_markers(recording_title)
        if observed - recording_versions and recording_versions - observed:
            return None
        if recording_versions - observed or (recording_session and not track_session):
            title, observed = recording_title, recording_versions
            track_session = recording_session
    if note and (
        version_markers(edition_label(note) or note) - observed
        or (note_session and not track_session)
    ):
        title = f"{title} ({note_session or note})"
    return title


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


def discovery_page_bounds(limit: int, offset: int, maximum: int = MAX_DISCOVERY_OFFSET) -> None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise MusicDiscoveryValidationError("music_discovery_limit_invalid")
    if type(offset) is not int or not 0 <= offset <= maximum:
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
    release_group_id: UUID | None = None
    release_type: str | None = None
    performance_types: tuple[str, ...] = ()
    guest_artist_ids: tuple[UUID, ...] = ()
    artist_members: tuple[dict[str, str], ...] = ()

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
        title = source_title(self.title, self.recording_title, self.disambiguation)
        if not title:
            return None
        try:
            title, artist = featured_names(title, self.artist)
            return discovery_query(f"{artist} {title}")
        except MusicDiscoveryValidationError:
            return None

    def view(self) -> dict[str, object]:
        title, artist = self.title, self.artist
        if artist and self.entity_type in {
            MusicDiscoveryEntity.RECORDING,
            MusicDiscoveryEntity.RELEASE_TRACK,
        }:
            title, artist = featured_names(title, artist)
        card: dict[str, object] = {
            "id": self.namespaced_id,
            **(
                {"performance_types": list(self.performance_types)}
                if self.performance_types
                else {}
            ),
            **(
                {"guest_artist_ids": [str(item) for item in self.guest_artist_ids]}
                if self.guest_artist_ids
                else {}
            ),
            **({"release_group_id": str(self.release_group_id)} if self.release_group_id else {}),
            **({"release_type": self.release_type} if self.release_type else {}),
            "entity_type": self.entity_type.value,
            "entity_id": str(self.entity_id),
            "title": title,
            "artist": artist,
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
        if self.artist_members:
            card["artist_members"] = [
                {"mbid": member["mbid"], "name": member["name"]} for member in self.artist_members
            ]
            # Clients accept complete cards up to 16 KiB, including the original display credit.
            if len(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode()) > 16_384:
                del card["artist_members"]
        return card


@dataclass(frozen=True, slots=True)
class MusicDiscoveryPage:
    items: tuple[MusicDiscoveryCard, ...]
    limit: int
    offset: int
    total_count: int
    source_page_count: int
    edition_items: tuple[MusicDiscoveryCard, ...] | None = None
    max_offset: int = MAX_DISCOVERY_OFFSET
    edition_title: str | None = None
    edition_artist: str | None = None

    def __post_init__(self) -> None:
        discovery_page_bounds(self.limit, self.offset, self.max_offset)
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
            following if more and self.source_page_count and following <= self.max_offset else None
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
