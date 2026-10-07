"""Bounded MusicBrainz entity parsing over a mandatory trusted HTTP dependency."""

import re
from datetime import date
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from autplay.domain.music_discovery import (
    MAX_RELEASE_TRACKS,
    MusicDiscoveryCard,
    MusicDiscoveryEntity,
    MusicDiscoveryKind,
    MusicDiscoveryPage,
    discovery_page_bounds,
    discovery_query,
)
from autplay.ports.music_discovery import MusicDiscoveryHttp
from autplay.ports.track_metadata import MetadataProviderError

_BASE = "https://musicbrainz.org/ws/2/"


def _text(value: object, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError("missing title")
        return None
    if not isinstance(value, str) or len(value) > 500 or not value.strip():
        raise ValueError("invalid text")
    return value.strip()


def _title(value: object) -> str:
    result = _text(value, required=True)
    assert result is not None
    return result


def _positive(value: object, maximum: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid number")
    return value


def _duration(value: object) -> int | None:
    if value == 0 and type(value) is int:
        return None
    return _positive(value, 86400000)


def _partial_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("invalid date")
    parts = tuple(map(int, value.split("-")))
    date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    return value


def _credit(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("invalid credit")
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid credit")
        artist = item.get("artist", {})
        if not isinstance(artist, dict):
            raise ValueError("invalid credit")
        name = _text(item.get("name") or artist.get("name"), required=True)
        join = item.get("joinphrase", "")
        if not isinstance(join, str) or len(join) > 100:
            raise ValueError("invalid credit join")
        parts.append(str(name) + join)
    return _text("".join(parts))


def _rows(value: object, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("invalid provider page")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError("invalid provider item")
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1000000:
        raise ValueError("invalid provider count")
    return value


def _source_progress(
    document: dict[str, Any], key: str, offset: int, count: int, size: int
) -> None:
    if key in document and _count(document[key]) != offset:
        raise ValueError("provider offset mismatch")
    if size and offset + size > count:
        raise ValueError("provider count mismatch")


def _deduplicate(items: list[MusicDiscoveryCard]) -> tuple[MusicDiscoveryCard, ...]:
    seen: set[str] = set()
    result: list[MusicDiscoveryCard] = []
    for item in items:
        if item.namespaced_id not in seen:
            seen.add(item.namespaced_id)
            result.append(item)
    return tuple(result)


class MusicBrainzDiscoveryProvider:
    def __init__(self, http: MusicDiscoveryHttp) -> None:
        # No default client: production must inject the shared cluster-gated transport.
        self.http = http

    def search(
        self, query: str, kind: MusicDiscoveryKind, *, limit: int, offset: int
    ) -> MusicDiscoveryPage:
        query = discovery_query(query)
        discovery_page_bounds(limit, offset)
        try:
            kind = MusicDiscoveryKind(kind)
            resource = "artist" if kind == MusicDiscoveryKind.ARTIST else "release"
            expression = re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r"\\\1", query)
            params = urlencode(
                {"query": expression, "limit": limit, "offset": offset, "fmt": "json"}
            )
            document = self.http.json(_BASE + resource + "?" + params)
            rows = _rows(document.get("artists" if resource == "artist" else "releases"), limit)
            count = _count(document.get("count"))
            _source_progress(document, "offset", offset, count, len(rows))
            items: list[MusicDiscoveryCard] = []
            for row in rows:
                identity = UUID(row["id"])
                if resource == "artist":
                    card = MusicDiscoveryCard(
                        MusicDiscoveryEntity.ARTIST,
                        identity,
                        _title(row.get("name")),
                        country=_text(row.get("country")),
                        disambiguation=_text(row.get("disambiguation")),
                    )
                else:
                    card = MusicDiscoveryCard(
                        MusicDiscoveryEntity.RELEASE,
                        identity,
                        _title(row.get("title")),
                        artist=_credit(row.get("artist-credit")),
                        release_id=identity,
                        release_date=_partial_date(row.get("date")),
                        country=_text(row.get("country")),
                        disambiguation=_text(row.get("disambiguation")),
                    )
                items.append(card)
            return MusicDiscoveryPage(_deduplicate(items), limit, offset, count, len(rows))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def artist_tracks(self, artist_id: UUID, *, limit: int, offset: int) -> MusicDiscoveryPage:
        discovery_page_bounds(limit, offset)
        try:
            artist_id = UUID(str(artist_id))
            params = urlencode(
                {
                    "artist": str(artist_id),
                    "limit": limit,
                    "offset": offset,
                    "inc": "artist-credits",
                    "fmt": "json",
                }
            )
            document = self.http.json(_BASE + "recording?" + params)
            if not document:
                raise MetadataProviderError("metadata_artist_missing", retryable=False)
            rows = _rows(document.get("recordings"), limit)
            count = _count(document.get("recording-count"))
            _source_progress(document, "recording-offset", offset, count, len(rows))
            items: list[MusicDiscoveryCard] = []
            for row in rows:
                identity = UUID(row["id"])
                items.append(
                    MusicDiscoveryCard(
                        MusicDiscoveryEntity.RECORDING,
                        identity,
                        _title(row.get("title")),
                        artist=_credit(row.get("artist-credit")),
                        recording_id=identity,
                        duration_ms=_duration(row.get("length")),
                        disambiguation=_text(row.get("disambiguation")),
                        release_date=_partial_date(row.get("first-release-date")),
                    )
                )
            return MusicDiscoveryPage(
                _deduplicate(items),
                limit,
                offset,
                count,
                len(rows),
            )
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def release_tracks(self, release_id: UUID, *, limit: int, offset: int) -> MusicDiscoveryPage:
        discovery_page_bounds(limit, offset)
        try:
            release_id = UUID(str(release_id))
            document = self.http.json(
                _BASE + f"release/{release_id}?inc=recordings+artist-credits&fmt=json"
            )
            if document.get("id") != str(release_id):
                raise MetadataProviderError("metadata_release_missing", retryable=False)
            media = _rows(document.get("media"), 1000)
            if "medium-count" in document and _count(document["medium-count"]) != len(media):
                raise ValueError("partial release media")
            ordered: list[tuple[int, int, MusicDiscoveryCard]] = []
            for medium in media:
                disc = _positive(medium.get("position"), 1000)
                rows = _rows(medium.get("tracks"), MAX_RELEASE_TRACKS)
                if "track-count" in medium and _count(medium["track-count"]) != len(rows):
                    raise ValueError("partial release medium")
                # Missing positions stay unknown; the stable provider order is the fallback.
                for row in rows:
                    if len(ordered) >= MAX_RELEASE_TRACKS:
                        raise ValueError("release too large")
                    recording = row.get("recording")
                    if not isinstance(recording, dict):
                        raise ValueError("invalid recording")
                    number = _positive(row.get("position"), 10000)
                    identity = UUID(row["id"])
                    ordered.append(
                        (
                            disc or 1001,
                            number or 10001,
                            MusicDiscoveryCard(
                                MusicDiscoveryEntity.RELEASE_TRACK,
                                identity,
                                _title(row.get("title") or recording.get("title")),
                                artist=_credit(row.get("artist-credit"))
                                or _credit(recording.get("artist-credit")),
                                release_id=release_id,
                                recording_id=UUID(recording["id"]),
                                release_date=_partial_date(document.get("date")),
                                country=_text(document.get("country")),
                                duration_ms=_duration(
                                    row.get("length")
                                    if row.get("length") is not None
                                    else recording.get("length")
                                ),
                                disc_number=disc,
                                track_number=number,
                                recording_title=_text(recording.get("title")),
                                disambiguation=_text(recording.get("disambiguation")),
                            ),
                        )
                    )
            ordered.sort(key=lambda item: (item[0], item[1]))
            page = [item[2] for item in ordered[offset : offset + limit]]
            return MusicDiscoveryPage(_deduplicate(page), limit, offset, len(ordered), len(page))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error
