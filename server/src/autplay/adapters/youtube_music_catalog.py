"""Use typed public YouTube Music catalogue records to admit studio audio."""

import json
import os
import re
import sys
from typing import Any

from ytmusicapi import YTMusic

from autplay.domain.acquisition_policy import track_review_reasons

MAX_INTERNET_TRACKS = 25
_PROVIDER_SCAN_LIMIT = 2 * MAX_INTERNET_TRACKS
_AUDIO_TRACK = "MUSIC_VIDEO_TYPE_ATV"


def _catalog() -> YTMusic:
    proxy = os.environ.get("AUTPLAY_MUSIC_PROXY")
    return YTMusic(proxies={"https": proxy, "http": proxy} if proxy else None)


def song_candidates(query: str) -> list[dict[str, Any]]:
    if track_review_reasons(1, query):
        return []
    rows = _catalog().search(query, filter="songs", limit=_PROVIDER_SCAN_LIMIT)
    candidates: list[dict[str, Any]] = []
    seen = set()
    for row in rows[:_PROVIDER_SCAN_LIMIT]:
        identity, title = row.get("videoId"), row.get("title")
        seconds, artists, album = row.get("duration_seconds"), row.get("artists"), row.get("album")
        if (
            row.get("resultType") != "song"
            or row.get("videoType") != _AUDIO_TRACK
            or row.get("isAvailable") is False
            or not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity)
            or identity in seen
            or not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 500
            or not isinstance(artists, list)
            or not isinstance(album, dict)
            or not isinstance(album.get("name"), str)
            or not album["name"].strip()
            or type(seconds) is not int
            or track_review_reasons(seconds, title, album["name"])
        ):
            continue
        artist = ", ".join(
            a["name"].strip()
            for a in artists
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip()
        )
        if not 1 <= len(artist) <= 500:
            continue
        seen.add(identity)
        candidates.append(
            {
                "candidate_id": identity,
                "provider": "YouTube",
                "title": title.strip(),
                "artist": artist,
                "duration_ms": seconds * 1000,
                "rank": len(candidates) + 1,
            }
        )
        if len(candidates) == MAX_INTERNET_TRACKS:
            break
    return candidates


def current_song_duration(candidate_id: str) -> int | None:
    """A player response for this exact ID must still classify it as an audio track."""
    song = _catalog().get_song(candidate_id)
    status = song.get("playabilityStatus", {}).get("status")
    if status in {"LOGIN_REQUIRED", "ERROR"}:
        raise RuntimeError("music_catalog_unavailable")
    details = song.get("videoDetails") or {}
    raw = details.get("lengthSeconds")
    if (
        details.get("videoId") != candidate_id
        or details.get("musicVideoType") != _AUDIO_TRACK
        or details.get("isLiveContent") is not False
        or not isinstance(raw, str)
        or not re.fullmatch(r"[0-9]{1,6}", raw)
        or not isinstance(details.get("title"), str)
        or not details["title"].strip()
        or track_review_reasons(int(raw), details["title"])
    ):
        return None
    return int(raw)


if __name__ == "__main__":
    print(json.dumps(song_candidates(sys.argv[1]), ensure_ascii=True, allow_nan=False))
