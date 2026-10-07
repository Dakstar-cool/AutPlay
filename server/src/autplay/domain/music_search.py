"""Bounded field selection for existing owner library track projections."""

from enum import StrEnum

from .library import LibraryCommandError


class MusicSearchKind(StrEnum):
    ALL = "all"
    TRACK = "track"
    ARTIST = "artist"
    ALBUM = "album"


def normalize_music_query(query: str) -> str:
    # Preserve punctuation and version words: live/remix are meaningful metadata.
    if not 1 <= len(query) <= 200 or any(
        (ord(character) < 32 or 127 <= ord(character) <= 159) and not character.isspace()
        for character in query
    ):
        raise LibraryCommandError("search_query_invalid")
    normalized = " ".join(query.split())
    if not normalized:
        raise LibraryCommandError("search_query_invalid")
    return normalized


def validate_music_search(
    query: str, limit: int, kind: MusicSearchKind
) -> tuple[str, MusicSearchKind]:
    normalized = normalize_music_query(query)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise LibraryCommandError("search_limit_invalid")
    try:
        selected = MusicSearchKind(kind)
    except ValueError:
        raise LibraryCommandError("search_kind_invalid") from None
    return normalized, selected
