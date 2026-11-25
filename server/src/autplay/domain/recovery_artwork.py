"""Public archive metadata used to corroborate the recording and the illustrated release."""

import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlencode, urlsplit
from uuid import UUID

from autplay.domain.catalog_artwork import (
    ArtMatch,
    album_label,
    label,
    recording_labels_match,
    timing_limit,
)

ARCHIVE_COLLECTIONS = {"freemusicarchive", "jamendoalbums", "netlabels"}
ITEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
FILENAME = re.compile(r"[\w .()&,'+-]{1,190}\.(?:jpe?g|png)", re.I)


def mbid(value):
    try:
        return str(UUID(str(value)))
    except ValueError, TypeError:
        return None


def selected_group_art(fields, title, artist):
    recording = mbid(fields.get("mb_recording_id"))
    group = mbid(fields.get("mb_release_group_id"))
    if not (recording and group and mbid(fields.get("mb_release_id")) and title and artist):
        return None
    return ArtMatch(
        None,
        artist,
        recording,
        group,
        f"https://coverartarchive.org/release-group/{group}/front-500",
        None,
        title,
        fields.get("album") or "",
        artist,
        "COVER_ART_ARCHIVE",
        "release-group",
        "selected_release_group",
    )


def listenbrainz_url(ids):
    return "https://api.listenbrainz.org/1/metadata/recording/?" + urlencode(
        {"recording_mbids": ",".join(ids), "inc": "artist release"}
    )


def listenbrainz_cover(records, title, artist, album, duration, *, selected_release=None, isrcs=()):
    if not title or not artist or not duration:
        return None
    options = []
    for rec_id, item in records.items():
        if not mbid(rec_id) or not isinstance(item, dict):
            continue
        who, song, release = (item.get(k) or {} for k in ("artist", "recording", "release"))
        if not all(isinstance(x, dict) for x in (who, song, release)):
            continue
        if not recording_labels_match(
            label(artist), label(title), label(who.get("name") or ""), label(song.get("name") or "")
        ):
            continue
        length = song.get("length")
        if type(length) is not int:
            continue
        shared_isrc = bool(set(song.get("isrcs") or []) & set(isrcs))
        if abs(length - duration) > timing_limit(
            duration,
            corroborated=shared_isrc
            or bool(album_label(album) and album_label(release.get("name")) == album_label(album))
            or bool(selected_release and selected_release == release.get("mbid")),
        ):
            continue
        caa = mbid(release.get("caa_release_mbid"))
        if not caa or not release.get("caa_id") or (selected_release and caa != selected_release):
            continue
        if album_label(album) and (
            caa != mbid(release.get("mbid"))
            or album_label(release.get("name")) != album_label(album)
        ):
            continue
        credit = tuple(
            p.get("artist_mbid") for p in who.get("artists") or [] if isinstance(p, dict)
        )
        if not credit or not all(mbid(value) for value in credit):
            continue
        options.append((credit, caa, rec_id, release.get("name") or "", shared_isrc))
    if not options or len({(credit, caa) for credit, caa, _, _, _ in options}) != 1:
        return None
    _, caa, recording, name, shared_isrc = options[0]
    return ArtMatch(
        None,
        artist,
        recording,
        caa,
        f"https://coverartarchive.org/release/{caa}/front-500",
        None,
        title,
        name,
        artist,
        "COVER_ART_ARCHIVE",
        "release",
        "listenbrainz_isrc" if shared_isrc else "listenbrainz_recording",
    )


def archive_search(artist):
    # Solr quoted phrase; the collection constraint is controlled by the application.
    literal = artist.replace("\\", "\\\\").replace('"', '\\"')
    q = f'mediatype:audio AND collection:(freemusicarchive OR jamendoalbums OR netlabels) AND creator:"{literal}" AND licenseurl:[* TO *]'
    return "https://archive.org/advancedsearch.php?" + urlencode(
        {"q": q, "output": "json", "fl[]": "identifier", "rows": 24, "sort[]": "downloads desc"}
    )


def archive_batch_search(artists):
    terms = ['"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"' for name in artists]
    q = (
        "mediatype:audio AND collection:(freemusicarchive OR jamendoalbums OR netlabels) AND creator:("
        + " OR ".join(terms)
        + ") AND licenseurl:[* TO *]"
    )
    return "https://archive.org/advancedsearch.php?" + urlencode(
        [
            ("q", q),
            ("output", "json"),
            ("fl[]", "identifier"),
            ("fl[]", "creator"),
            ("rows", "96"),
            ("sort[]", "downloads desc"),
        ]
    )


def archive_image_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.query or p.fragment or p.netloc != "archive.org":
        return False
    parts = unquote(p.path).split("/")
    return (
        len(parts) == 4
        and parts[1] == "download"
        and bool(ITEM.fullmatch(parts[2]) and FILENAME.fullmatch(parts[3]))
    )


def archive_cover(item, identifier, title, artist, album, duration):
    if not album_label(album) and label(title) in {
        "intro",
        "outro",
        "untitled",
        "interlude",
        "интро",
        "скит",
    }:
        return None
    data = item.get("metadata") or {}
    if (
        not ITEM.fullmatch(identifier)
        or not isinstance(data, dict)
        or data.get("identifier") != identifier
        or not duration
    ):
        return None
    collections = data.get("collection")
    collections = [collections] if isinstance(collections, str) else collections
    if not isinstance(collections, list) or not ARCHIVE_COLLECTIONS.intersection(collections):
        return None
    license_url = data.get("licenseurl") or ""
    if not isinstance(license_url, str) or not re.fullmatch(
        r"https?://creativecommons\.org/(?:licenses/[a-z-]+|publicdomain/(?:zero|mark))/[0-9.]+/?",
        license_url,
    ):
        return None
    creator, release = data.get("creator"), data.get("title")
    if (
        not isinstance(creator, str)
        or label(creator) != label(artist)
        or not isinstance(release, str)
    ):
        return None
    if album_label(album) and album_label(release) != album_label(album):
        return None
    originals = [
        f for f in item.get("files") or [] if isinstance(f, dict) and f.get("source") == "original"
    ]
    front = []
    fits = False
    for f in originals:
        path = f.get("name") or ""
        if not isinstance(path, str):
            continue
        if FILENAME.fullmatch(path):
            if label(PurePosixPath(path).stem) in {
                label(identifier),
                label(release),
                "cover",
                "front",
                "folder",
            }:
                front.append(path)
            continue
        if not re.search(r"\.(flac|mp3|ogg|opus|wav|m4a|aac)$", path, re.I):
            continue
        song, who = f.get("title"), f.get("artist") or creator
        if (
            not isinstance(song, str)
            or not isinstance(who, str)
            or not recording_labels_match(label(artist), label(title), label(who), label(song))
        ):
            continue
        if f.get("album") and album_label(f["album"]) != album_label(release):
            continue
        try:
            parts = str(f.get("length", "")).split(":")
            if len(parts) > 3 or not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
                continue
            seconds = sum(float(p) * 60**i for i, p in enumerate(reversed(parts)))
        except ValueError, OverflowError:
            continue
        if abs(1000 * seconds - duration) <= timing_limit(
            duration, corroborated=bool(album_label(album))
        ):
            fits = True
    if not fits or len(front) != 1:
        return None
    url = f"https://archive.org/download/{identifier}/{quote(front[0], safe='')}"
    return ArtMatch(
        None,
        artist,
        identifier,
        identifier,
        url,
        None,
        title,
        release,
        artist,
        "INTERNET_ARCHIVE",
        "item",
        "archive_original_file",
        f"https://archive.org/details/{identifier}",
        license_url,
    )


def archive_items(response):
    hits = (response.get("response") or {}).get("docs") or []
    return list(
        dict.fromkeys(
            row["identifier"]
            for row in hits
            if isinstance(row, dict)
            and isinstance(row.get("identifier"), str)
            and ITEM.fullmatch(row["identifier"])
        )
    )[:24]


def metadata_url_allowed(url):
    from urllib.parse import parse_qs

    p = urlsplit(url)
    if p.scheme != "https" or p.fragment:
        return False
    q = parse_qs(p.query)
    if p.netloc == "api.listenbrainz.org" and p.path == "/1/metadata/recording/":
        ids = q.get("recording_mbids", [""])[0].split(",")
        return (
            1 <= len(ids) <= 20 and all(mbid(i) == i for i in ids) and url == listenbrainz_url(ids)
        )
    if p.netloc != "archive.org":
        return False
    if p.path.startswith("/metadata/"):
        return not p.query and bool(ITEM.fullmatch(p.path[len("/metadata/") :]))
    if p.path == "/advancedsearch.php":
        expr = q.get("q", [""])[0]
        if " AND creator:(" in expr:
            literal = expr.partition(" AND creator:(")[2].rpartition(") AND licenseurl:")[0]
            names = [
                re.sub(r"\\(.)", r"\1", v) for v in re.findall(r'"((?:\\.|[^"\\])*)"', literal)
            ]
            return (
                1 <= len(names) <= 12
                and all(0 < len(name) <= 500 for name in names)
                and url == archive_batch_search(names)
            )
        match = re.search(r' AND creator:"((?:\\.|[^"\\])*)" AND licenseurl:', expr)
        if match and 0 < len(match[1]) <= 1024:
            raw = re.sub(r"\\(.)", r"\1", match[1])
            return url == archive_search(raw)
    return False
