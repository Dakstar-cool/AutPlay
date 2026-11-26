"""Corroborate public artwork using the actual recording and performer credit."""

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from autplay.domain.featured_artists import featured_names, same_feature_credit
from autplay.domain.title_edits import edit_parts, edit_title, edition_label, joined_title_matches
from autplay.domain.track_metadata import lookup_title, performance_search_title
from autplay.domain.track_names import strip_source_site

_PUNCTUATION = str.maketrans("\u2019\u2018\u2010\u2011\u2013—", "''----")


def label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().translate(_PUNCTUATION)
    return " ".join(value.split())


def album_label(value: str | None) -> str | None:
    name = label(value or "")
    return None if name in {"", "<unknown>", "unknown album", "неизвестный альбом"} else name


def artwork_title(title: str, artist: str) -> str:
    # Strip presentation labels from the search only; live/remix/cover credits stay in the title.
    title = strip_source_site(title)
    if not any(marker in title for marker in ("(", "[", " @", " - ", "|", " \u2013 ", " — ", "*")):
        return edit_title(title)
    cleaned = lookup_title(title, artist)
    for token in (" (music video)", " [music video]", " - music video", " *video in description*"):
        if label(cleaned).endswith(token):
            cleaned = cleaned[: -len(token)].rstrip()
    return cleaned or title


def artwork_search_title(title: str, artist: str) -> str:
    cleaned = performance_search_title(artwork_title(title, artist))
    # A dated language tag can obscure catalogue search. Keep it for the actual match.
    return (
        re.sub(
            r"\s+(?:\((?:19|20)[0-9]{2}\)\s*\[[A-Za-z]{2}\]|\((?:uncensored|censored|explicit|clean)\))$",
            "",
            cleaned,
            flags=re.I,
        )
        or cleaned
    )


def versioned_titles(title: str, version: object) -> tuple[str, ...]:
    if not isinstance(version, str):
        return (title,)
    version = version.strip().strip("()[]").strip()
    if not version:
        return (title,)
    name, suffix = label(title), label(version)
    suffixes = (f" ({suffix})", f" [{suffix}]", f" - {suffix}", f" {suffix}")
    edition = edition_label(version)
    if name.endswith(suffixes) or (edition and edition == edit_parts(title)[1]):
        return (title,)
    return (f"{title} ({version})", f"{title} - {version}", f"{title} {version}")


def recording_labels_match(artist: str, title: str, primary: str, catalog_title: str) -> bool:
    artist = strip_source_site(artist)
    title = strip_source_site(title)
    if not title or not primary or not artist:
        return False
    if (artist, title) == (primary, catalog_title):
        return True
    title, catalog_title = (
        label(artwork_title(title, artist)),
        label(artwork_title(catalog_title, primary)),
    )
    if (artist, title) == (primary, catalog_title):
        return True
    title, artist = featured_names(title, artist)
    catalog_title, primary = featured_names(catalog_title, primary)
    if artist != primary and not same_feature_credit(artist, primary):
        return False
    base, edition = edit_parts(title)
    catalog_base, catalog_edition = edit_parts(catalog_title)
    return edition == catalog_edition and (
        base == catalog_base or joined_title_matches(base, catalog_base)
    )


def image_url(value: object, kind: str) -> str | None:
    if not isinstance(value, str) or len(value) > 400:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"cdn-images.dzcdn.net", "e-cdns-images.dzcdn.net"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = rf"/images/{kind}/(?!0{{32}}/)[0-9a-f]{{32}}/500x500-000000-80-0-0\.jpg"
    return value if re.fullmatch(path, parsed.path) else None


def search_url(artist: str, title: str = "", *, clean_title: bool = True) -> str:
    artist = strip_source_site(artist)
    limit = 25 if title else 100
    if clean_title:
        title = artwork_search_title(title, artist)
    return "https://api.deezer.com/search/track?" + urlencode(
        {"q": (artist + " " + title).strip(), "limit": limit}
    )


@dataclass(frozen=True)
class ArtMatch:
    artist_id: int | None
    artist: str
    track_id: int | str
    album_id: int | str
    cover: str | None
    portrait: str | None
    title: str
    album: str
    credit: str
    provider: str = "DEEZER"
    entity: str = "album"
    basis: str = "recording_duration"
    source_page: str | None = None
    license_url: str | None = None


def timing_limit(duration_ms: int, *, corroborated: bool = False) -> int:
    # A small fixed encoder offset is significant on a very short clip.
    return max(3000, min(10000 if corroborated else 5000, round(duration_ms * 0.08)))


def timing_basis(delta_ms: int) -> str:
    if delta_ms <= 3000:
        return "recording_duration"
    if delta_ms <= 5000:
        return "recording_timing_offset"
    return "edition_timing_offset"


def match_artwork(
    rows: object,
    title: str,
    artist: str,
    album: str | None,
    duration_ms: int | None,
    *,
    confirmed_albums: Collection[str] = (),
) -> ArtMatch | None:
    if (
        not isinstance(rows, list)
        or not title
        or not artist
        or not duration_ms
        or duration_ms < 1000
    ):
        return None
    expected = label(strip_source_site(artist)), label(strip_source_site(title))
    if expected[0] in {
        "",
        "<unknown>",
        "unknown artist",
        "various artists",
        "неизвестный исполнитель",
        "разные исполнители",
    }:
        return None
    expected_album = album_label(album)
    matches = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("artist"), dict)
            or not isinstance(row.get("album"), dict)
        ):
            continue
        performer, release = row["artist"], row["album"]
        if not all(
            isinstance(s, str)
            for s in (performer.get("name"), row.get("title"), release.get("title"))
        ):
            continue
        titles = versioned_titles(row["title"], row.get("title_version"))
        if not any(
            recording_labels_match(*expected, label(performer["name"]), label(name))
            for name in titles
        ):
            continue
        release_name = album_label(release.get("title"))
        corroborated = bool(
            release_name and (expected_album == release_name or release_name in confirmed_albums)
        )
        if type(row.get("duration")) is not int:
            continue
        delta = abs(1000 * row["duration"] - duration_ms)
        if delta > timing_limit(duration_ms, corroborated=corroborated):
            continue
        basis = timing_basis(delta)
        if not all(
            type(v) is int and v > 0
            for v in (performer.get("id"), row.get("id"), release.get("id"))
        ):
            continue
        matches.append(
            ArtMatch(
                performer["id"],
                performer["name"],
                row["id"],
                release["id"],
                image_url(release.get("cover_big"), "cover"),
                image_url(performer.get("picture_big"), "artist"),
                title,
                release["title"],
                artist,
                basis=basis,
            )
        )
    editions = [m for m in matches if not expected_album or label(m.album) == expected_album]
    matches = editions or matches
    if not matches or len({m.artist_id for m in matches}) != 1:
        return None
    covers = {m.cover for m in editions if m.cover}
    unknown_edition = (
        not expected_album
        and len({m.album_id for m in editions}) > 1
        and any(m.cover is None for m in editions)
    )
    cover = next(iter(covers)) if len(covers) == 1 and not unknown_edition else None
    representative = next((m for m in matches if cover and m.cover == cover), matches[0])
    return ArtMatch(
        representative.artist_id,
        representative.artist,
        representative.track_id,
        representative.album_id,
        cover,
        next((m.portrait for m in matches if m.portrait), None),
        representative.title,
        representative.album,
        representative.credit,
        basis=representative.basis,
    )


def catalogue_api_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.fragment:
        return False
    if parsed.netloc == "api.discogs.com":
        if re.fullmatch(r"/releases/[1-9][0-9]{0,15}", parsed.path):
            return not parsed.query
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (
            parsed.path == "/database/search"
            and set(query) == {"type", "artist", "release_title", "per_page"}
            and query["type"] == ["release"]
            and query["per_page"] == ["25"]
            and all(
                len(query[k]) == 1 and 0 < len(query[k][0]) <= 500
                for k in ("artist", "release_title")
            )
        )
    if parsed.netloc != "api.deezer.com":
        return False
    if re.fullmatch(r"/(?:artist|album)/[1-9][0-9]{0,16}", parsed.path):
        return not parsed.query
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.path == "/search/track"
        and set(query) == {"q", "limit"}
        and query["limit"] in (["25"], ["100"])
        and len(query["q"]) == 1
        and 0 < len(query["q"][0]) <= 1100
    )


def native_image_host(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.fragment:
        return None
    if (
        re.fullmatch(r"f[0-9]+\.bcbits\.com", parsed.netloc)
        and not parsed.query
        and re.fullmatch(r"/img/a[0-9]{1,16}_[0-9]{1,2}\.jpg", parsed.path)
    ):
        return "BANDCAMP"
    if (
        parsed.netloc in {"avatars.yandex.net", "avatars.mds.yandex.net"}
        and not parsed.query
        and re.fullmatch(r"/get-music-content/[0-9]+/[\w.%-]+/600x600", parsed.path)
    ):
        return "YANDEX"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.netloc == "usercontent.jamendo.com"
        and parsed.path in {"", "/"}
        and set(query) <= {"type", "id", "width", "trackid"}
        and query.get("type") in (["album"], ["track"])
        and query.get("id", query.get("trackid"))
        and all(
            len(values) == 1 and values[0].isdigit() for k, values in query.items() if k != "type"
        )
    ):
        return "JAMENDO"
    return None
