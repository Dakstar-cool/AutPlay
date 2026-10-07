"""Bounded native source evidence, independent of audio identity and authorization."""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import suppress
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

MAX_EVIDENCE_BYTES = 16 * 1024
SOURCE_PROVIDERS = {
    "jamendo": "JAMENDO",
    "yandex": "YANDEX",
    "bandcamp": "BANDCAMP",
    "soundcloud": "SOUNDCLOUD",
    "yt_dlp": "YOUTUBE",
    "hitmo": "HITMO",
}
_TEXT = frozenset({"title", "artist", "album", "album_artist"})
_DATES = frozenset({"release_date", "original_release_date"})
_IDS = frozenset({"mb_recording_id", "mb_release_id", "mb_release_group_id"})
_NUMBERS = frozenset({"track_number", "disc_number"})
_KINDS = frozenset({"album", "track", "thumbnail", "avatar", "waveform"})
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")


def _text(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        return None
    return unicodedata.normalize("NFC", value).strip() or None


def _partial_date(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}){0,2}", value):
        return None
    parts = [int(part) for part in value.split("-")]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return None
    return value


def _art_url(value: object, provider: str) -> str | None:
    """Retain known public image origins as hints; never fetch images here."""
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) <= 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
        ):
            return None
        hosts = {
            "jamendo": host == "usercontent.jamendo.com",
            "bandcamp": host == "bcbits.com" or host.endswith(".bcbits.com"),
            "soundcloud": host == "sndcdn.com" or host.endswith(".sndcdn.com"),
            "yt_dlp": host in {"i.ytimg.com", "img.youtube.com"},
            "yandex": host in {"avatars.yandex.net", "avatars.mds.yandex.net"},
        }
        if not hosts.get(provider, False):
            return None
        if parsed.query:
            parameters = parse_qsl(parsed.query, keep_blank_values=True)
            if (
                provider != "jamendo"
                or len(parameters) > 4
                or len({key for key, _ in parameters}) != len(parameters)
                or any(
                    key not in {"type", "id", "width", "trackid"}
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", query_value) is None
                    for key, query_value in parameters
                )
            ):
                return None
    except ValueError:
        return None
    return value


def bounded_source_metadata(raw: object, *, provider: str) -> dict[str, object] | None:
    """Discard malformed optional evidence without turning verified audio into failure."""
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or raw.get("provider") != SOURCE_PROVIDERS.get(provider, provider.upper())
    ):
        return None
    source_id = raw.get("source_id")
    if (
        not isinstance(source_id, str)
        or _SOURCE_ID.fullmatch(source_id) is None
        or "://" in source_id
    ):
        return None
    result: dict[str, object] = {
        "schema_version": 1,
        "provider": SOURCE_PROVIDERS.get(provider, provider.upper()),
        "source_id": source_id,
    }
    fields: dict[str, object] = {}
    source = raw.get("fields")
    if isinstance(source, dict):
        for key in _TEXT:
            if cleaned := _text(source.get(key)):
                fields[key] = cleaned
        for key in _DATES:
            if cleaned := _partial_date(source.get(key)):
                fields[key] = cleaned
        for key in _IDS:
            value = source.get(key)
            if isinstance(value, str) and len(value) <= 36:
                with suppress(ValueError):
                    fields[key] = str(UUID(value))
        for key in _NUMBERS:
            value = source.get(key)
            if type(value) is int and 1 <= value <= 9999:
                fields[key] = value
        genres = source.get("genres")
        if isinstance(genres, list) and len(genres) <= 12:
            cleaned_genres = list(
                dict.fromkeys(text for item in genres if (text := _text(item, 100)))
            )
            if cleaned_genres:
                fields["genres"] = cleaned_genres
    result["fields"] = fields
    external: dict[str, str] = {}
    identifiers = raw.get("external_ids")
    if isinstance(identifiers, dict):
        isrc = identifiers.get("isrc")
        if isinstance(isrc, str) and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", isrc):
            external["isrc"] = isrc
        for key in ("native_album_id", "native_artist_id"):
            value = identifiers.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
                external[key] = value
    result["external_ids"] = external
    artwork = raw.get("artwork")
    images: list[dict[str, str]] = []
    if isinstance(artwork, list):
        for item in artwork[:4]:
            if not isinstance(item, dict):
                continue
            kind, image_id = item.get("kind"), item.get("source_id")
            url = _art_url(item.get("url"), provider)
            if (
                isinstance(kind, str)
                and kind in _KINDS
                and isinstance(image_id, str)
                and _SOURCE_ID.fullmatch(image_id)
                and "://" not in image_id
                and url is not None
            ):
                images.append({"kind": kind, "source_id": image_id, "url": url})
    result["artwork"] = images
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return result if len(encoded.encode("utf-8")) <= MAX_EVIDENCE_BYTES else None


def native_metadata(
    provider: str,
    source_id: object,
    fields: dict[str, object],
    *,
    isrc: object = None,
    native_album_id: object = None,
    native_artist_id: object = None,
    artwork: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    if not isinstance(source_id, (str, int)) or isinstance(source_id, bool):
        return None
    if isinstance(native_album_id, bool):
        native_album_id = None
    if isinstance(native_artist_id, bool):
        native_artist_id = None
    return bounded_source_metadata(
        {
            "schema_version": 1,
            "provider": SOURCE_PROVIDERS.get(provider, provider.upper()),
            "source_id": str(source_id),
            "fields": fields,
            "external_ids": {
                "isrc": isrc,
                "native_album_id": str(native_album_id) if native_album_id is not None else None,
                "native_artist_id": str(native_artist_id) if native_artist_id is not None else None,
            },
            "artwork": artwork or [],
        },
        provider=provider,
    )


def yt_dlp_metadata(info: dict[str, object], *, provider: str) -> dict[str, object] | None:
    """Use explicit music tags only: no uploader, series or upload-date fallbacks."""
    fields = {key: info.get(key) for key in _TEXT | _IDS | _NUMBERS | {"genres"}}
    fields["title"] = info.get("track")
    for name, plural in (("artist", "artists"), ("album_artist", "album_artists")):
        if not fields.get(name):
            artists = info.get(plural)
            if (
                isinstance(artists, list)
                and 1 <= len(artists) <= 12
                and all(_text(a) for a in artists)
            ):
                fields[name] = ", ".join(str(a) for a in artists)
    for key in _DATES:
        value = info.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value):
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        fields[key] = value
    if not fields.get("release_date"):
        year = info.get("release_year")
        if type(year) is int and 1 <= year <= 9999:
            fields["release_date"] = f"{year:04d}"
        else:
            timestamp = info.get("release_timestamp")
            with suppress(ValueError, OverflowError, OSError):
                if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                    fields["release_date"] = (
                        datetime.fromtimestamp(timestamp, UTC).date().isoformat()
                    )
    images: list[dict[str, object]] = []
    thumbnail = info.get("thumbnail")
    if thumbnail:
        # Generic extractor thumbnails can be artist avatars, even with album tags.
        images.append({"kind": "thumbnail", "url": thumbnail, "source_id": str(info.get("id"))})
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails[-4:]):
            if isinstance(item, dict):
                images.append(
                    {"kind": "thumbnail", "url": item.get("url"), "source_id": str(info.get("id"))}
                )
    return native_metadata(
        provider,
        info.get("id"),
        fields,
        isrc=info.get("isrc"),
        native_album_id=info.get("native_album_id"),
        native_artist_id=info.get("native_artist_id"),
        artwork=images,
    )
