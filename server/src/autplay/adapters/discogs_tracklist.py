"""Read exact editions through the catalogue's single-grant HTTP transport."""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from autplay.domain.music_discovery import MAX_RELEASE_TRACKS
from autplay.ports.track_metadata import MetadataProviderError

type ReadDocument = Callable[[str], dict[str, Any]]


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _artist_key(value: str) -> tuple[str, tuple[str, ...]]:
    parts = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", _key(value), maxsplit=1)
    guests = re.split(r"\s+(?:&|and)\s+|,\s*", parts[1]) if len(parts) == 2 else []
    return parts[0], tuple(sorted(guests))


def annotate_discogs_positions(
    tracks: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> None:
    """A Discogs position is not a recording identifier. Keep ambiguous matches unknown."""
    by_name: dict[tuple[str, tuple[str, tuple[str, ...]]], list[dict[str, Any]]] = {}
    for row in evidence:
        by_name.setdefault((_key(row["title"]), _artist_key(row["artist"])), []).append(row)
    for track in tracks:
        position = track["position"].strip().casefold()
        matches = set()
        for row in by_name.get((_key(track["title"]), _artist_key(track["artist"])), []):
            if (
                track.get("mbid")
                and row.get("mb_recording")
                and track["mbid"] != row["mb_recording"]
            ):
                continue
            disc, number = row["disc"], row["sequence"]
            labels = {f"{disc}-{number}", f"{disc}.{number}"} if disc and number else set()
            printed = str(row["number"] or "").strip().casefold()
            if printed and not printed.isdigit():
                labels.add(printed)
            if disc in (None, 1) and number:
                labels.add(str(number))
            difference = (
                abs(track["duration_ms"] - row["duration_ms"])
                if track["duration_ms"] and row["duration_ms"]
                else None
            )
            if difference is not None and difference > 3000:
                continue
            printed_position = (
                f"{track['disc']}-{position}" if (track.get("disc") or 1) > 1 else position
            )
            if printed_position not in labels and (
                number is not None or difference is None or difference > 1500
            ):
                continue
            matches.add(row["recording"])
        track["recording"] = matches.pop() if len(matches) == 1 else None
        track["in_vault"] = True if track["recording"] else None


def _credit(artists: list[dict[str, Any]]) -> str:
    return " ".join(
        part
        for artist in artists
        for part in (
            re.sub(r" \(\d+\)$", "", artist.get("anv") or artist["name"]),
            artist.get("join", ""),
        )
        if part
    )


def edition_choices(
    read: ReadDocument, source: str, title: str, artist: str, year: int | None
) -> tuple[list[dict[str, str]], bool]:
    """Only automatically select a unique exact artist/title in a complete result."""
    if source == "discogs":
        query = urlencode(
            {"type": "release", "artist": artist, "release_title": title, "per_page": 25}
        )
        data = read("https://api.discogs.com/database/search?" + query)
        rows = data["results"]
        complete = data["pagination"]["pages"] <= 1
    else:

        def quote(value: str) -> str:
            return '"' + re.sub(r'([\\"])', r"\\\1", value) + '"'

        query = urlencode(
            {
                "query": f"release:{quote(title)} AND artist:{quote(artist)}",
                "limit": 25,
                "fmt": "json",
            }
        )
        data = read("https://musicbrainz.org/ws/2/release?" + query)
        if not data:
            return [], True
        rows = data["releases"]
        complete = data["count"] <= len(rows)
    choices = {}
    for row in rows:
        if source == "discogs":
            if row.get("type") != "release":
                continue
            label = row["title"]
            identity = str(row["id"])
            if not re.fullmatch(r"[1-9][0-9]{0,15}", identity):
                continue
            # Discogs appends a numeric disambiguator to artist names.
            exact = _key(re.sub(r" \(\d+\)(?= - )", "", label)) == _key(f"{artist} - {title}")
            date = str(row.get("year") or "")
            formats = row.get("format") or []
        else:
            identity = str(UUID(row["id"]))
            name = "".join(
                (a.get("name") or a["artist"]["name"]) + a.get("joinphrase", "")
                for a in row.get("artist-credit", [])
            )
            exact = _key(row["title"]) == _key(title) and _key(name) == _key(artist)
            date = row.get("date") or ""
            formats = [m.get("format") for m in row.get("media", [])]
        if exact and (not year or not date or str(year) == date[:4]):
            description = " · ".join(
                dict.fromkeys(
                    v for v in [date, row.get("country"), *formats, row.get("disambiguation")] if v
                )
            )
            choices[identity] = {"source": source, "id": identity, "label": description or title}
    return list(choices.values()), complete


def linked_discogs(read: ReadDocument, mbid: UUID) -> tuple[int | None, str, str]:
    doc = read(f"https://musicbrainz.org/ws/2/release/{mbid}?inc=url-rels+artist-credits&fmt=json")
    if doc.get("id") != str(mbid):
        return None, "", ""
    ids = set()
    for rel in doc.get("relations", []):
        if rel.get("type") == "discogs" and not rel.get("ended"):
            match = re.fullmatch(
                r"https?://(?:www\.)?discogs\.com/release/([1-9][0-9]{0,15})(?:-[^/?#]*)?/?",
                rel.get("url", {}).get("resource", ""),
            )
            if match:
                ids.add(int(match[1]))
    artist = "".join(
        a["artist"]["name"] + a.get("joinphrase", "") for a in doc.get("artist-credit", [])
    )
    return next(iter(ids)) if len(ids) == 1 else None, doc.get("title", ""), artist


def discogs_edition(read: ReadDocument, release_id: int) -> dict[str, Any]:
    doc = read(f"https://api.discogs.com/releases/{release_id}")
    if doc.get("id") != release_id:
        raise MetadataProviderError("metadata_release_missing", retryable=False)
    artist = _credit(doc["artists"])
    tracks: list[dict[str, Any]] = []

    def append(rows: list[dict[str, Any]], credit: str, depth: int = 0) -> None:
        if depth > 8 or len(rows) > MAX_RELEASE_TRACKS:
            raise ValueError("invalid subtracks")
        for row in rows:
            kind = row.get("type_", "track")
            names = _credit(row.get("artists") or []) or credit
            if kind == "index":
                append(row["sub_tracks"], names, depth + 1)
            elif kind == "track":
                guests = [
                    _credit([guest])
                    for guest in row.get("extraartists", [])
                    if "featuring"
                    in [role.strip().lower() for role in guest.get("role", "").split(",")]
                ]
                if guests and not _artist_key(names)[1]:
                    names += " feat. " + " & ".join(dict.fromkeys(guests))
                title = row["title"]
                if (
                    not isinstance(title, str)
                    or not title.strip()
                    or len(tracks) >= MAX_RELEASE_TRACKS
                ):
                    raise ValueError("invalid track")
                duration = row.get("duration") or ""
                seconds = None
                if re.fullmatch(r"(?:\d{1,3}:)?\d{1,3}:[0-5]\d", duration):
                    seconds = 0
                    for part in duration.split(":"):
                        seconds = seconds * 60 + int(part)
                tracks.append(
                    {
                        "position": row.get("position") or "",
                        "title": title,
                        "artist": names,
                        "duration_ms": seconds * 1000 if seconds else None,
                    }
                )

    append(doc["tracklist"], artist)
    if not tracks:
        raise MetadataProviderError("metadata_release_missing", retryable=False)
    return {
        "source": "discogs",
        "id": str(release_id),
        "title": doc["title"],
        "artist": artist,
        "year": doc.get("year") or None,
        "tracks": tracks,
    }
