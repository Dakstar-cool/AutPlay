"""Evidence for borrowing an original recording's cover for a cover version or remix."""

import re

from autplay.domain.catalog_artwork import ArtMatch, artwork_title, image_url, label
from autplay.domain.music_variations import split_label, variation_kind
from autplay.domain.recovery_artwork import mbid
from autplay.domain.track_metadata import MetadataQuery

ORIGINAL_BASES = {"original_cover", "original_remix"}


def original_artwork_source(source) -> bool:
    return bool(
        source
        and source.get("source") == "PUBLIC_CATALOG"
        and source.get("match_basis") in ORIGINAL_BASES
    )


def variation_title(query: MetadataQuery, kind: str, version: str | None = None) -> tuple[str, str]:
    title = artwork_title(query.title, query.artist)
    part = split_label(title)
    if part and variation_kind(None, False, None, part[1], None) == kind:
        return part
    return title, (version or "").strip()


def credited_original(
    query: MetadataQuery, kind: str, version: str | None = None
) -> tuple[str, str] | None:
    """Only use a named source artist or the credited artist of a labeled remix."""
    if re.search(r"\b(?:mashup|medley|мэшап|попурри|no remix)\b", query.title, re.I):
        return None
    base, suffix = variation_title(query, kind, version)
    if kind == "COVER":
        credit = re.fullmatch(r"(?:originally by|кавер на)\s+(.+)", suffix, re.I)
        if not credit:
            return None
        artist = credit[1].strip()
    elif kind == "REMIX":
        artist = query.artist
        remixer = re.fullmatch(r"(.+?)\s+(?:remix|ремикс)", suffix, re.I)
        if remixer and label(remixer[1]) == label(artist):
            # Uploads credited to the remixer sometimes name the original in the title.
            pair = re.fullmatch(r"(.+?)\s+[-–—]\s+(.+)", base)
            if not pair:
                return None
            artist, base = pair.groups()
    else:
        return None
    unknown = {
        "",
        "<unknown>",
        "unknown artist",
        "various artists",
        "неизвестный исполнитель",
        "разные исполнители",
    }
    if label(artist) in unknown or not base or variation_kind(None, False, None, None, base):
        return None
    return artist, base


def catalog_original(
    rows, query: MetadataQuery, kind: str, credit: tuple[str, str]
) -> ArtMatch | None:
    artist, title = map(label, credit)
    matches = {}
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        performer, album = row.get("artist"), row.get("album")
        if not isinstance(performer, dict) or not isinstance(album, dict):
            continue
        if (
            not isinstance(performer.get("name"), str)
            or not isinstance(row.get("title"), str)
            or label(performer["name"]) != artist
            or label(row["title"]) != title
            or row.get("title_version")
        ):
            continue
        ids = performer.get("id"), row.get("id"), album.get("id")
        if not all(type(v) is int and v > 0 for v in ids) or not isinstance(
            album.get("title"), str
        ):
            return None
        cover = image_url(album.get("cover_big"), "cover")
        if not cover:
            return None
        matches[(*ids, cover)] = ArtMatch(
            ids[0],
            performer["name"],
            ids[1],
            ids[2],
            cover,
            None,
            query.title,
            album["title"],
            query.artist,
            basis=f"original_{kind.lower()}",
        )
    return next(iter(matches.values())) if len(matches) == 1 else None


def linked_original(records, query: MetadataQuery, kind: str, original: str) -> ArtMatch | None:
    item = records.get(original)
    if not isinstance(item, dict):
        return None
    artist, recording, release = (item.get(k) for k in ("artist", "recording", "release"))
    if not all(isinstance(value, dict) for value in (artist, recording, release)):
        return None
    caa = mbid(release.get("caa_release_mbid"))
    if (
        not caa
        or caa != mbid(release.get("mbid"))
        or not release.get("caa_id")
        or not all(
            isinstance(s, str) and s.strip()
            for s in (artist.get("name"), recording.get("name"), release.get("name"))
        )
    ):
        return None
    return ArtMatch(
        None,
        artist["name"],
        original,
        caa,
        f"https://coverartarchive.org/release/{caa}/front-500",
        None,
        query.title,
        release["name"],
        query.artist,
        "COVER_ART_ARCHIVE",
        "release",
        f"original_{kind.lower()}",
    )
