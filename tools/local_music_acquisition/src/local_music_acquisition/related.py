"""Rank discovery evidence without weakening exact acquisition identity checks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .matching import recording_identity
from .models import PlaylistItem
from .normalization import normalize_artist, normalize_query, normalize_text

POLICY_VERSION = "related-recordings-v1"
_FOLD = str.maketrans(
    "\u0430\u0432\u0441\u0435\u043d\u043a\u043c\u043e\u0440\u0442\u0445\u0443\u0456\u0458\u0451",
    "abcehkmoptxyije",
)
_VERSION = re.compile(
    r"\s*[\[(][^\])]*(?:remix|mix|live|cover|slowed|sped|acoustic|version|edit|feat\.?|ft\.?)"
    r"[^\])]*[\])]",
    re.I,
)
_DECORATION = re.compile(
    r"\s*[\[(](?:official\s+(?:audio|music\s+video|video|lyric\s+video)|"
    r"audio|lyrics?|lyric\s+video|visuali[sz]er|hd|4k)[\])]\s*$",
    re.I,
)


def identity_fields(raw: dict[str, Any]) -> tuple[str, str] | None:
    """Use recording tags or explicit display credits, never an arbitrary uploader."""
    artist = raw.get("artist") or raw.get("artists")
    title = raw.get("track")
    if isinstance(artist, list) and all(isinstance(x, str) for x in artist):
        artist = ", ".join(artist)
    if not isinstance(artist, str) or not isinstance(title, str):
        display = raw.get("title")
        if not isinstance(display, str):
            return None
        display = normalize_text(display)
        if re.search(r"\s[-|]\s", display):
            artist, title = re.split(r"\s[-|]\s", display, maxsplit=1)
        elif isinstance(raw.get("uploader"), str) and raw["uploader"].endswith(" - Topic"):
            artist, title = raw["uploader"].removesuffix(" - Topic"), display
        else:
            return None
    artist, title = normalize_artist(artist), normalize_text(title)
    while (cleaned := _DECORATION.sub("", title)) != title:
        title = cleaned
    if any(
        not value.strip() or len(value) > 500 or re.search(r"[\x00-\x1f]", value)
        for value in (artist, title)
    ):
        return None
    return artist, title


@dataclass(frozen=True)
class RelatedCandidate:
    provider: str
    artist: str
    title: str
    duration_seconds: float | None = None

    def item(self, row: int) -> PlaylistItem:
        # An unrelated parent's album and duration must never label this recording.
        return PlaylistItem(row, self.artist, self.title)

    @classmethod
    def parse(cls, raw: dict[str, Any], provider: str) -> RelatedCandidate:
        identity = identity_fields({"artist": raw.get("artist"), "track": raw.get("title")})
        duration = raw.get("duration_seconds")
        if identity is None or (
            duration is not None
            and (
                type(duration) not in (int, float)
                or not math.isfinite(duration)
                or not 0 < duration <= 86400
            )
        ):
            raise ValueError("discovery_candidate_invalid")
        return cls(provider, *identity, duration)


def _fold(value: str) -> str:
    return re.sub(r"[^\w]", "", normalize_query(value).casefold().translate(_FOLD))


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def candidate_score(item: PlaylistItem, candidate: RelatedCandidate) -> float:
    wanted_artist = normalize_artist(item.artist)
    actual_artist = normalize_artist(candidate.artist)
    full = _ratio(_fold(wanted_artist), _fold(actual_artist))
    primary = _ratio(_fold(wanted_artist.split(",")[0]), _fold(actual_artist.split(",")[0]))
    artist = max(full, primary)
    wanted_title = re.sub(r"\s*[\[(][^\])]*$", "", item.title)
    wanted_title = _fold(_VERSION.sub("", wanted_title))
    actual_title = _fold(_VERSION.sub("", candidate.title))
    title = _ratio(wanted_title, actual_title)
    if len(wanted_title) >= 10 and actual_title.startswith(wanted_title):
        title = max(title, 0.94)
    # A long artist name cannot compensate for a different composition title.
    if artist < 0.82 or title < 0.82:
        return 0.0
    if min(len(_fold(wanted_artist)), len(_fold(actual_artist))) < 3 and artist < 1:
        return 0.0
    if min(len(wanted_title), len(actual_title)) < 4 and title < 1:
        return 0.0
    return round((artist + title) / 2, 4)


def select_candidates(
    item: PlaylistItem,
    candidates: list[RelatedCandidate],
    *,
    limit: int = 3,
) -> list[RelatedCandidate]:
    if not 1 <= limit <= 3:
        raise ValueError("candidate_limit_invalid")
    unique: dict[tuple[frozenset[str], str], RelatedCandidate] = {}
    for candidate in candidates:
        identity = recording_identity(candidate.artist, candidate.title)
        previous = unique.get(identity)
        if previous is None or candidate_score(item, candidate) > candidate_score(item, previous):
            unique[identity] = candidate
    original = recording_identity(item.artist, item.title)
    if original in unique:
        return [unique[original]]
    ranked = sorted(unique.values(), key=lambda c: (-candidate_score(item, c), c.artist, c.title))
    return [c for c in ranked if candidate_score(item, c) > 0][:limit]
