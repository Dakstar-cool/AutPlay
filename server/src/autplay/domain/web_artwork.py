"""Identify official release artwork in public catalogue responses and publisher pages."""

import contextlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from autplay.domain.catalog_artwork import (
    ArtMatch,
    album_label,
    artwork_search_title,
    label,
    native_image_host,
    recording_labels_match,
    timing_basis,
    timing_limit,
    versioned_titles,
)
from autplay.domain.track_names import strip_source_site

BANDCAMP_SEARCH = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"


def yandex_search_url(artist: str, title: str = "", *, clean_title: bool = True) -> str:
    artist = strip_source_site(artist)
    if clean_title:
        title = artwork_search_title(title, artist)
    return "https://api.music.yandex.net/search?" + urlencode(
        {"text": (artist + " " + title).strip(), "type": "track", "page": 0}
    )


def discography_url(artist_id: int | str, page: int) -> str:
    return f"https://api.music.yandex.net/artists/{int(artist_id)}/tracks?page={int(page)}&page-size=70"


def publisher_track_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 1024:
        return None
    return (
        value
        if re.fullmatch(
            r"https://[a-z0-9][a-z0-9-]{0,61}\.bandcamp\.com/track/[a-zA-Z0-9%_-]+/?", value
        )
        else None
    )


def release_lookup_url(url: str) -> bool:
    if url == BANDCAMP_SEARCH or publisher_track_url(url):
        return True
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc != "api.music.yandex.net" or parsed.fragment:
        return False
    q = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == "/search":
        return (
            set(q) == {"text", "type", "page"}
            and q["type"] == ["track"]
            and q["page"] == ["0"]
            and len(q["text"]) == 1
            and 0 < len(q["text"][0]) <= 1100
        )
    return bool(
        re.fullmatch(r"/artists/[1-9][0-9]{0,16}/tracks", parsed.path)
        and set(q) == {"page", "page-size"}
        and q["page-size"] == ["70"]
        and len(q["page"]) == 1
        and q["page"][0] in [str(p) for p in range(12)]
    )


def _positive(value: object) -> int | None:
    return (
        int(str(value))
        if type(value) in (str, int) and re.fullmatch(r"[1-9][0-9]{0,16}", str(value))
        else None
    )


def _picture(template: object) -> str | None:
    if not isinstance(template, str) or not template.endswith("/%%") or len(template) > 330:
        return None
    url = "https://" + template[:-2] + "600x600"
    return url if native_image_host(url) == "YANDEX" else None


def _credit_matches(
    artist: str, title: str, performers: Iterable[dict[str, Any]], full_title: str
) -> bool:
    names = [label(p.get("name") or "") for p in performers]
    artist = strip_source_site(artist)
    if not names or not all(names):
        return False
    if len(names) == 1:
        return recording_labels_match(label(artist), label(title), names[0], label(full_title))
    return any(
        recording_labels_match(label(artist), label(title), sep.join(names), label(full_title))
        for sep in (", ", " & ", "/", " / ", " feat. ", " feat ", " ft. ", " x ", " и ")
    )


def yandex_match(
    rows: object, title: str, artist: str, album: str | None, duration_ms: int | None
) -> ArtMatch | None:
    if not isinstance(rows, list) or not title or not artist or not duration_ms:
        return None
    albums, matches = [], []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("durationMs")) is not int:
            continue
        known_album = album_label(album)
        edition_agrees = bool(
            known_album
            and any(
                isinstance(rel, dict) and album_label(rel.get("title")) == known_album
                for rel in row.get("albums") or []
            )
        )
        delta = abs(row["durationMs"] - duration_ms)
        if delta > timing_limit(duration_ms, corroborated=edition_agrees):
            continue
        performers = row.get("artists") or []
        if not performers or not all(
            isinstance(p, dict) and isinstance(p.get("name"), str) and _positive(p.get("id"))
            for p in performers
        ):
            continue
        track_id = _positive(row.get("id"))
        base = row.get("title")
        if not track_id or not isinstance(base, str):
            continue
        versions = versioned_titles(base, row.get("version"))
        if not any(_credit_matches(artist, title, performers, item) for item in versions):
            continue
        primary = performers[0]
        aid = int(primary["id"])
        if primary.get("various") or label(primary["name"]) in {
            "<unknown>",
            "unknown artist",
            "various artists",
            "разные исполнители",
            "неизвестный исполнитель",
        }:
            continue
        photo = primary.get("cover") or {}
        portrait = (
            _picture(photo.get("uri"))
            if isinstance(photo, dict) and photo.get("type") == "from-artist-photos"
            else None
        )
        matches.append((aid, primary["name"], track_id, portrait))
        for release in row.get("albums") or []:
            if (
                not isinstance(release, dict)
                or not (rid := _positive(release.get("id")))
                or not isinstance(release.get("title"), str)
            ):
                continue
            cover = _picture(release.get("coverUri"))
            if not cover or (album_label(album) and label(release["title"]) != album_label(album)):
                continue
            owners = {
                str(p.get("id"))
                for p in release.get("artists") or []
                if isinstance(p, dict) and not p.get("various")
            }
            own = str(aid) in owners
            if not album_label(album) and not own:
                continue
            try:
                released = date.fromisoformat(str(release.get("releaseDate"))[:10])
            except ValueError:
                year = release.get("year")
                released = (
                    date(year, 12, 31) if type(year) is int and 1800 <= year <= 9999 else date.max
                )
            albums.append((released, rid, aid, track_id, release["title"], cover, delta))
    if not matches:
        return None
    artist_ids = {m[0] for m in matches}
    if album_label(album) and albums:
        artist_ids = {a[2] for a in albums}
    if len(artist_ids) != 1:
        return None
    aid = artist_ids.pop()
    matching = [m for m in matches if m[0] == aid]
    _, name, track, _ = matching[0]
    portrait = next((m[3] for m in matching if m[3]), None)
    choices = sorted(a for a in albums if a[2] == aid)
    if choices and not (album_label(album) and len({a[5] for a in choices}) > 1):
        _, rid, _, track, album_name, cover, delta = choices[0]
        basis = timing_basis(delta)
        return ArtMatch(
            aid, name, track, rid, cover, portrait, title, album_name, artist, "YANDEX", basis=basis
        )
    return (
        ArtMatch(aid, name, track, 0, None, portrait, title, "", artist, "YANDEX")
        if portrait
        else None
    )


def yandex_editions(
    rows: Iterable[dict[str, Any]] | None,
    title: str,
    artist: str,
    album: str | None,
    duration: int | None,
) -> set[str]:
    if not duration:
        return set()
    editions = set()
    for row in rows or []:
        length = row.get("durationMs") if isinstance(row, dict) else None
        if type(length) is int and abs(length - duration) <= timing_limit(
            duration, corroborated=True
        ):
            hit = yandex_match([row], title, artist, album, length)
            if hit and hit.cover and (edition := album_label(hit.album)):
                editions.add(edition)
    return editions


def bandcamp_query(artist: str, title: str) -> bytes:
    artist = strip_source_site(artist)
    return json.dumps(
        {
            "search_text": f"{artist} {artwork_search_title(title, artist)}",
            "search_filter": "t",
            "full_page": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def publisher_candidates(
    page: dict[str, Any], artist: str, title: str, album: str | None
) -> list[tuple[str, dict[str, Any]]]:
    auto = page.get("auto") or {}
    rows = auto.get("results") or [] if isinstance(auto, dict) else []
    result = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("type") != "t"
            or not all(isinstance(row.get(k), str) for k in ("name", "band_name", "item_url_path"))
        ):
            continue
        url = publisher_track_url(row["item_url_path"].split("?", 1)[0])
        if (
            url
            and recording_labels_match(
                label(artist), label(title), label(row["band_name"]), label(row["name"])
            )
            and (not album_label(album) or album_label(row.get("album_name")) == album_label(album))
            and all(_positive(row.get(k)) for k in ("id", "band_id", "art_id"))
        ):
            result.append((url, row))
    return result


class _RecordingPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.recordings: list[dict[str, Any]] = []
        self.native: dict[str, Any] = {}
        self.structured = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            self.structured = (attributes.get("type") or "").lower() == "application/ld+json"
            self.parts = []
        if (native := attributes.get("data-tralbum")) is not None:
            with contextlib.suppress(TypeError, ValueError):
                self.native = json.loads(native)

    def handle_data(self, data: str) -> None:
        if self.structured:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or not self.structured:
            return
        self.structured = False
        try:
            item = json.loads("".join(self.parts))
        except ValueError:
            return
        if isinstance(item, dict) and item.get("@type") == "MusicRecording":
            self.recordings.append(item)


def indexed_publisher_cover(
    candidates: Sequence[tuple[str, dict[str, Any]]], title: str, artist: str, album: str | None
) -> ArtMatch | None:
    """Use artwork from a unique index result when the recording page is absent."""
    if len(candidates) != 1:
        return None
    _, row = candidates[0]
    album_name = row.get("album_name")
    if not isinstance(album_name, str) or not album_name.strip():
        return None
    if album_label(album) and album_label(album) != album_label(album_name):
        return None
    if not album_label(album) and (
        len(label(title)) < 4
        or label(title) in {"intro", "outro", "interlude", "untitled", "скит", "интро"}
    ):
        return None
    supplied = row.get("img")
    art_id = _positive(row.get("art_id"))
    if not isinstance(supplied, str) or not art_id:
        return None
    path = re.fullmatch(
        r"(https://f[0-9]+\.bcbits\.com)/img/a?([0-9]{1,16})_[0-9]{1,2}\.jpg", supplied
    )
    if not path or int(path[2]) != art_id:
        return None
    cover = f"{path[1]}/img/a{path[2]}_10.jpg"
    return ArtMatch(
        int(row["band_id"]),
        row["band_name"],
        int(row["id"]),
        _positive(row.get("album_id")) or 0,
        cover,
        None,
        title,
        album_name,
        artist,
        "BANDCAMP",
        basis="publisher_index",
    )


def publisher_match(
    page: bytes | None,
    row: dict[str, Any],
    url: str,
    title: str,
    artist: str,
    album: str | None,
    duration_ms: int | None,
) -> ArtMatch | None:
    if not page or len(page) > 1048576 or not duration_ms:
        return None
    parsed = _RecordingPage()
    parsed.feed(page.decode("utf-8", errors="replace"))
    native = parsed.native if isinstance(parsed.native, dict) else {}
    current = native.get("current") or {}
    if (
        not isinstance(current, dict)
        or current.get("type") != "track"
        or _positive(current.get("id")) != _positive(row["id"])
    ):
        return None
    art_id = _positive(current.get("art_id") or native.get("art_id"))
    if not art_id or art_id != _positive(row.get("art_id")):
        return None
    tracks = [
        t
        for t in native.get("trackinfo") or []
        if isinstance(t, dict) and _positive(t.get("track_id")) == _positive(row["id"])
    ]
    if (
        len(tracks) != 1
        or type(tracks[0].get("duration")) not in (int, float)
        or abs(duration_ms - 1000 * tracks[0]["duration"])
        > timing_limit(
            duration_ms,
            corroborated=bool(
                album_label(album) and album_label(album) == album_label(row.get("album_name"))
            ),
        )
    ):
        return None
    for music in parsed.recordings:
        credit, release = music.get("byArtist"), music.get("inAlbum") or {}
        if not isinstance(credit, dict) or not isinstance(release, dict) or music.get("@id") != url:
            continue
        name, who, art = music.get("name"), credit.get("name"), music.get("image")
        if not isinstance(name, str) or not isinstance(who, str) or not isinstance(art, str):
            continue
        if not recording_labels_match(label(artist), label(title), label(who), label(name)):
            continue
        album_name = release.get("name") or row.get("album_name") or ""
        if album_label(album) and album_label(album_name) != album_label(album):
            continue
        if native_image_host(art) != "BANDCAMP" or not re.search(
            rf"/a0*{art_id}_[0-9]+\.jpg$", art
        ):
            continue
        return ArtMatch(
            int(row["band_id"]),
            who,
            int(row["id"]),
            _positive(current.get("album_id")) or 0,
            art,
            None,
            title,
            album_name,
            artist,
            "BANDCAMP",
        )
    return None


def archived_editions(
    rows: Iterable[dict[str, Any]],
    title: str,
    artist: str,
    album: str | None,
    duration_ms: int | None,
) -> list[ArtMatch]:
    """Identify artwork-bearing editions without accepting a metadata review candidate."""
    if not title or not artist or not duration_ms:
        return []
    eligible, corroborated = {}, {}
    releases_seen = set()
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
            continue
        fields = row["fields"]
        duration = row.get("duration_ms")
        if not recording_labels_match(
            label(artist),
            label(title),
            label(fields.get("artist") or ""),
            label(fields.get("title") or ""),
        ):
            continue
        name = fields.get("album")
        if not name or (album_label(album) and album_label(album) != album_label(name)):
            continue
        try:
            rec, release = (
                str(UUID(str(fields.get(k)))) for k in ("mb_recording_id", "mb_release_id")
            )
            group = (
                str(UUID(str(fields["mb_release_group_id"])))
                if fields.get("mb_release_group_id")
                else None
            )
        except ValueError, TypeError:
            continue
        if row.get("source_id") != f"musicbrainz:release:{release}":
            continue
        releases_seen.add((rec, group))
        if type(duration) is not int:
            continue
        observed = fields.get("release_date") or fields.get("original_release_date") or "9999"
        key, value = (str(observed), release, rec), (rec, release, group, name)
        delta = abs(duration - duration_ms)
        if delta <= 3000:
            eligible[key] = value
        elif group and delta <= min(8000, duration_ms * 0.03):
            corroborated[key] = value
    basis = "recording_duration"
    if not eligible and len(releases_seen) == 1:
        eligible, basis = corroborated, "recording_and_release_group"
    if len({entry[0] for entry in eligible.values()}) != 1:
        return []
    ordered: list[ArtMatch] = []
    for _, (rec, release, group, name) in sorted(eligible.items()):
        for kind, identifier in (("release", release), ("release-group", group)):
            if identifier and not any(
                item.album_id == identifier and item.entity == kind for item in ordered
            ):
                url = f"https://coverartarchive.org/{kind}/{identifier}/front-500"
                ordered.append(
                    ArtMatch(
                        None,
                        artist,
                        rec,
                        identifier,
                        url,
                        None,
                        title,
                        name,
                        artist,
                        "COVER_ART_ARCHIVE",
                        kind,
                        basis,
                    )
                )
    return ordered[:6]
