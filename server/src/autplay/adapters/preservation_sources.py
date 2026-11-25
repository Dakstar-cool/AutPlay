"""Sources provide evidence only. Acquisition grants come from a separate operator-owned file."""

import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

MAX_BYTES = 1_048_576
NEWS = "https://www.theguardian.com/music/rss"
# Dates describe the evidence, not a claim that a historical restriction remains in force.
_GAGA = "https://www.thefader.com/2019/01/11/lady-gaga-r-kelly-do-what-u-want-deleted"
_ARCHIVE = (
    (
        "gaga-dwuw-track-2019",
        "track",
        "Lady Gaga feat. R. Kelly",
        "Do What U Want",
        "2013 ARTPOP recording",
        "Spotify and iTunes",
        "2019-01-11",
        "Artist withdrew the collaboration.",
        _GAGA,
    ),
    (
        "gaga-dwuw-ep-2019",
        "album",
        "Lady Gaga",
        "Do What U Want",
        "remix EP",
        "digital services",
        "2019-01-11",
        "The remix EP was withdrawn alongside the single.",
        _GAGA,
    ),
    (
        "young-spotify-2022",
        "catalog",
        "Neil Young",
        "Artist catalogue",
        "Spotify catalogue",
        "Spotify",
        "2024-03-13",
        "Withdrawn in 2022. Return announced in March 2024.",
        "https://www.theguardian.com/music/2024/mar/13/neil-young-to-return-music-to-spotify-as-he-attacks-disinformation-across-streaming-services",
    ),
    (
        "pogues-radio1-2020",
        "track",
        "The Pogues feat. Kirsty MacColl",
        "Fairytale of New York",
        "1987 original",
        "UK BBC Radio 1",
        "2020-11-19",
        "The station selected an edited version for its 2020 broadcasts.",
        "https://www.theguardian.com/music/2020/nov/19/fairytale-of-new-york-pogues-censored-radio-1-radio-2",
    ),
    (
        "lizzo-grrrls-2022",
        "track",
        "Lizzo",
        "Grrrls",
        "initial June 2022 recording",
        "digital single",
        "2022-08-01",
        "Artist issued a revised vocal after the first release.",
        "https://time.com/6202642/beyonce-lizzo-lyrics-ableist-changed/",
    ),
    (
        "mb-65601948-c476-4c47-9424-fada811ac114",
        "album",
        "Kanye West",
        "Nah Nah Nah",
        "2020 digital single",
        "US iTunes",
        "2021-08-07",
        "Original digital single withdrawn from streaming and download.",
        "https://musicbrainz.org/release/65601948-c476-4c47-9424-fada811ac114",
    ),
    (
        "mb-790bb9c1-dda3-483d-b5bb-06181c77d91f",
        "album",
        "Lady Gaga",
        "ARTPOP",
        "original explicit digital album",
        "digital stores",
        "2019-02-08",
        "The replacement edition omits Do What U Want.",
        "https://musicbrainz.org/release/790bb9c1-dda3-483d-b5bb-06181c77d91f",
    ),
    (
        "prince-sexy-mf-streaming-edit",
        "track",
        "Prince and The New Power Generation",
        "Sexy M.F.",
        "original album intro",
        "reported streaming album edition",
        "",
        "Listeners report that the compilation edit replaced the album version.",
        "https://community.metabrainz.org/t/how-best-to-handle-different-versions-of-albums-on-deezer-spotify-itunes/744894",
    ),
    (
        "kakao-spotify-2021",
        "catalog",
        "Various Artists",
        "Kakao M distributed repertoire",
        "March 2021 catalogue",
        "Spotify global service",
        "2021-03-11",
        "Global licence expired. Distribution agreement subsequently renewed.",
        "https://en.yna.co.kr/view/AEN20210311003200320",
    ),
)


def archive():
    for event, kind, artist, title, version, scope, when, reason, url in _ARCHIVE:
        row = dict(
            event=event,
            kind=kind,
            artist=artist,
            title=title,
            version=version,
            scope=scope,
            date=when,
            reason=reason,
            url=url,
            confidence="review" if event.startswith("prince-") else "confirmed",
            status="historical",
        )
        if event == "mb-65601948-c476-4c47-9424-fada811ac114":
            row["tracks"] = [
                dict(
                    event="nah-nah-nah-2020-original",
                    artist=artist,
                    title=title,
                    version="original 2020 digital single",
                )
            ]
        yield row


def https(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("invalid_source_url")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid_source_url")
    return url


def read_document(path: Path):
    with path.open("rb") as stream:
        value = stream.read(MAX_BYTES + 1)
    if len(value) > MAX_BYTES:
        raise ValueError("source_too_large")
    return json.loads(value)


def fetch(url):
    request = Request(https(url), headers={"User-Agent": "AutPlay-MPW/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=8) as response:
                https(response.geturl())
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("source_too_large")
            return data
        except (OSError, URLError) as error:
            if attempt == 2 or (
                isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504)
            ):
                raise
            time.sleep(2**attempt)


def news(url=NEWS):
    data = fetch(url)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("invalid_news_xml")
    for item in ElementTree.fromstring(data).findall("./channel/item")[:100]:
        headline = (item.findtext("title") or "").strip()
        link = item.findtext("link") or ""
        if not re.search(
            r"\b(pull(?:s|ed)?|remov(?:e|es|ed)|ban(?:ned|s)?|censor\w*|withdraw\w*|"
            r"chang\w* lyrics)\b",
            headline,
            re.I,
        ):
            continue
        https(link)
        key = hashlib.sha256(link.encode()).hexdigest()
        yield dict(
            event="news-" + key,
            kind="lead",
            artist="",
            title=headline[:450],
            version="",
            scope="press report",
            date="",
            reason="Report requires corroboration.",
            url=link,
            confidence="review",
            status="reported",
        )


def withdrawn_releases():
    from uuid import UUID

    data = json.loads(
        fetch("https://musicbrainz.org/ws/2/release/?query=status%3Awithdrawn&limit=100&fmt=json")
    )
    for release in data["releases"]:
        if str(release.get("status", "")).casefold() != "withdrawn":
            continue
        mbid = str(UUID(release["id"]))
        artist = "".join(
            c.get("name", "") + c.get("joinphrase", "") for c in release.get("artist-credit", [])
        )
        yield dict(
            event="mb-" + mbid,
            kind="album",
            artist=artist,
            title=release["title"],
            version="release " + mbid,
            scope=release.get("country") or "catalogue",
            date="",
            reason="MusicBrainz marks this edition as withdrawn.",
            url="https://musicbrainz.org/release/" + mbid,
            confidence="review",
            status="reported",
        )


def apple_listings():
    ids = {
        "mb-65601948-c476-4c47-9424-fada811ac114": 1536234310,
        "mb-790bb9c1-dda3-483d-b5bb-06181c77d91f": 687591810,
    }
    url = "https://itunes.apple.com/lookup?country=us&id=" + ",".join(map(str, ids.values()))
    result = json.loads(fetch(url))
    listings = result["results"]
    if not isinstance(listings, list) or len(listings) != result["resultCount"]:
        raise ValueError("invalid_storefront_response")
    for row in archive():
        catalog_id = ids.get(row["event"])
        if catalog_id is None:
            continue
        listed = any(
            item.get("collectionId") == catalog_id and item.get("artistName") == row["artist"]
            for item in listings
        )
        yield dict(row, listing="listed" if listed else "absent", listing_url=url)


def validate(record):
    required = (
        "event",
        "kind",
        "artist",
        "title",
        "version",
        "scope",
        "date",
        "reason",
        "url",
        "confidence",
        "status",
    )
    if not isinstance(record, dict) or not all(
        isinstance(record.get(k), str) and len(record[k]) <= 2048 for k in required
    ):
        raise ValueError("invalid_preservation_record")
    row = {k: record[k].strip() for k in required}
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,95}", row["event"])
        or row["kind"] not in {"track", "album", "catalog", "lead"}
        or row["confidence"] not in {"review", "confirmed"}
        or not row["title"]
        or not row["reason"]
        or row["status"] not in {"reported", "historical", "active", "restored", "retracted"}
        or (row["kind"] == "track" and not (row["artist"] and row["version"]))
    ):
        raise ValueError("invalid_preservation_record")
    if "original_sha256" in record:
        digest = record["original_sha256"]
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("invalid_original_digest")
        row["original_sha256"] = digest
    if record.get("listing") in ("listed", "absent"):
        row["listing"] = record["listing"]
        row["listing_url"] = https(record["listing_url"])
    https(row["url"])
    if row["date"]:
        date.fromisoformat(row["date"])
    return row


def observations(record):
    row = validate(record)
    if "tracks" not in record:
        yield row
        return
    tracks = record["tracks"]
    if (
        row["kind"] not in {"album", "catalog"}
        or not isinstance(tracks, list)
        or len(tracks) > 10000
    ):
        raise ValueError("invalid_collection_tracklist")
    members = {}
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("invalid_collection_track")
        # Each member inherits the evidence and scope of this specific withdrawn edition.
        identity = {key: track[key] for key in ("event", "artist", "title", "version")}
        document = {**row, **identity, "kind": "track"}
        document.pop("original_sha256", None)
        if "original_sha256" in track:
            document["original_sha256"] = track["original_sha256"]
        member = validate(document)
        event = member["event"]
        if event == row["event"] or (event in members and members[event] != member):
            raise ValueError("conflicting_collection_track")
        members[event] = member
    yield {**row, "track_events": list(members)}
    for member in members.values():
        yield {**member, "collection_event": row["event"]}


def adapters(root):
    yield "archive", archive
    yield "guardian", news
    yield "musicbrainz", withdrawn_releases
    yield "itunes-us", apple_listings
    # Each file holds up to 500 observations keyed by stable event ID.
    # Only explicitly configured curated sources can confirm a case or retract an earlier report.
    for path in sorted((root / "feeds").glob("*.json"))[:32]:

        def records(path=path):
            source = read_document(path)
            rows = json.loads(fetch(source["url"])) if isinstance(source, dict) else source
            if not isinstance(rows, list) or len(rows) > 500:
                raise ValueError("invalid_preservation_feed")
            return rows

        yield "feed:" + path.stem[:80], records
