"""Conservative recording identity matching; OCR corrections belong in reviewed input."""

from __future__ import annotations

import re
import unicodedata

from .normalization import normalize_artist, normalize_text

SEARCH_LIMIT = 20
_DECORATION = re.compile(
    r"\s*[\[(](?:official\s+(?:audio|music\s+video|video|lyric\s+video)|"
    r"audio|lyrics?|lyric\s+video|visuali[sz]er|hd|4k)[\])]\s*$",
    re.IGNORECASE,
)
_FEATURE = re.compile(r"\s*[\[(]?\b(?:feat\.?|ft\.?|featuring|w/)\s+(.+?)[\])]?\s*$", re.I)
_ARTIST_SEPARATOR = re.compile(r"\s*(?:,|\s+&\s+|\s+x\s+|\s+feat\.?\s+|\s+ft\.?\s+)\s*", re.I)
_REMIX_CREDIT = re.compile(r"\s*[\[(]([^\])]+?)\s+remix[\])]\s*$", re.I)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("\u0451", "\u0435")
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())


def recording_identity(artist: str, title: str) -> tuple[frozenset[str], str]:
    artist, title = normalize_artist(artist), normalize_text(title)
    while (cleaned := _DECORATION.sub("", title)) != title:
        title = cleaned
    feature = _FEATURE.search(title)
    if feature:
        artist += ", " + feature.group(1)
        title = title[: feature.start()]
    remix = _REMIX_CREDIT.search(title)
    if remix:
        artist += ", " + remix.group(1)
        title = title[: remix.start()] + " (Remix)"
    artists = frozenset(normalize(part) for part in _ARTIST_SEPARATOR.split(artist) if part.strip())
    return artists, normalize(title)


def candidate_matches(candidate: dict[str, object], *, artist: str, title: str) -> bool:
    """Preserve version labels and all credited artists; remove only presentation suffixes."""
    actual_artist, actual_title = candidate.get("artist"), candidate.get("track")
    if isinstance(actual_artist, list) and all(isinstance(part, str) for part in actual_artist):
        actual_artist = ", ".join(actual_artist)
    if not (isinstance(actual_artist, str) and isinstance(actual_title, str)):
        display = candidate.get("title")
        if not isinstance(display, str):
            return False
        display = normalize_text(display)
        if re.search(r"\s[-|]\s", display):
            actual_artist, actual_title = (
                part.strip() for part in re.split(r"\s[-|]\s", display, maxsplit=1)
            )
        else:
            uploader = candidate.get("uploader")
            if not isinstance(uploader, str) or not uploader.endswith(" - Topic"):
                return False
            actual_artist, actual_title = uploader.removesuffix(" - Topic"), display
    return recording_identity(actual_artist, actual_title) == recording_identity(artist, title)
