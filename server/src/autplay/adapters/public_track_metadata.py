"""MusicBrainz / Cover Art Archive with strict hosts, bounds and no credentials."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import UUID

import httpx2

from autplay.domain.catalog_artwork import (
    ArtMatch,
    catalogue_api_url,
    image_url,
    label,
    match_artwork,
    native_image_host,
    search_url,
)
from autplay.domain.music_variations import variation_kind
from autplay.domain.musicbrainz_performances import cover_original, recording_work, remix_sources
from autplay.domain.original_artwork import (
    catalog_original,
    credited_original,
    linked_original,
    variation_title,
)
from autplay.domain.recovery_artwork import (
    archive_cover,
    archive_image_url,
    archive_items,
    archive_search,
    listenbrainz_cover,
    listenbrainz_url,
    mbid,
    metadata_url_allowed,
)
from autplay.domain.track_metadata import (
    MetadataCandidate,
    MetadataFields,
    MetadataQuery,
    catalogue_title_matches,
    lookup_title,
    metadata_search_terms,
    normalized,
    validate_fields,
)
from autplay.domain.web_artwork import (
    BANDCAMP_SEARCH,
    bandcamp_query,
    indexed_publisher_cover,
    publisher_candidates,
    publisher_match,
    publisher_track_url,
    release_lookup_url,
    yandex_editions,
    yandex_match,
    yandex_search_url,
)
from autplay.ports.track_metadata import MetadataProviderError

USER_AGENT = "AutPlay/0.3.7 (personal music library; https://github.com/Dakstar-cool/AutPlay)"


def is_cover_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname or "", parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in {None, 443}
    ):
        return False
    if image_url(url, "artist"):
        return True
    if host == "coverartarchive.org":
        return bool(
            re.fullmatch(r"/(?:release|release-group)/[0-9a-f-]{36}/front-(?:250|500)", parsed.path)
        )
    return (host == "archive.org" or host.endswith(".archive.org")) and bool(
        re.fullmatch(r"/(?:download|[0-9]+/items)/mbid-[0-9a-f-]{36}/[a-zA-Z0-9._%-]+", parsed.path)
    )


def validate_public_url(url: str, *, artwork: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname or "", parsed.port
    except ValueError as error:
        raise MetadataProviderError("metadata_url_rejected", retryable=False) from error
    allowed = host in {"musicbrainz.org", "coverartarchive.org", "api.acoustid.org"}
    allowed = (
        allowed or catalogue_api_url(url) or release_lookup_url(url) or metadata_url_allowed(url)
    )
    if host == "api.listenbrainz.org" and not metadata_url_allowed(url):
        allowed = (
            bool(
                re.fullmatch(r"/1/popularity/top-recordings-for-artist/[0-9a-f-]{36}", parsed.path)
            )
            and not parsed.query
        )
    if artwork:
        allowed = (
            allowed
            or host == "archive.org"
            or host.endswith(".archive.org")
            or bool(image_url(url, "cover") or image_url(url, "artist") or native_image_host(url))
        )
    if (
        not allowed
        or parsed.scheme != "https"
        or port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise MetadataProviderError("metadata_url_rejected", retryable=False)
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise MetadataProviderError("metadata_network_unavailable") from error
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise MetadataProviderError("metadata_url_rejected", retryable=False)


class PublicMetadataHttp:
    def __init__(
        self,
        gate: Callable[[], AbstractContextManager[Any]] = nullcontext,
        *,
        proxy: str | None = None,
    ) -> None:
        self.gate = gate
        # Request URLs contain private library searches; never log them at INFO.
        logging.getLogger("httpx2").setLevel(logging.WARNING)
        logging.getLogger("httpcore2").setLevel(logging.WARNING)
        self.client = httpx2.Client(
            proxy=proxy,
            trust_env=False,
            follow_redirects=False,
            timeout=15,
            headers={"User-Agent": USER_AGENT},
        )

    def cover_hop(self, url: str) -> bytes | None:
        """Fetch exactly one thumbnail hop under its own retained catalogue grant."""
        validate_public_url(url, artwork=True)
        try:
            with (
                self.gate(),
                self.client.stream(
                    "GET", url, headers={"Accept": "image/jpeg,image/png"}
                ) as response,
            ):
                if response.status_code == 404:
                    return None
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location or len(location) > 2048:
                        raise MetadataProviderError("metadata_response_invalid", retryable=False)
                    return json.dumps({"redirect": urljoin(url, location)}).encode()
                if response.status_code != 200:
                    raise MetadataProviderError(
                        "metadata_provider_unavailable",
                        retryable=response.status_code in {429, 503} or response.status_code >= 500,
                    )
                data = bytearray()
                for chunk in response.iter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise MetadataProviderError("metadata_response_too_large", retryable=False)
                if not (
                    data.startswith(bytes.fromhex("ffd8ff"))
                    or data.startswith(bytes.fromhex("89504e470d0a1a0a"))
                ):
                    raise MetadataProviderError("metadata_artwork_invalid", retryable=False)
                return bytes(data)
        except httpx2.HTTPError as exc:
            raise MetadataProviderError("metadata_network_unavailable") from exc

    def get(self, url: str, *, artwork: bool = False, post: bytes | None = None) -> bytes | None:
        if post is not None and (
            artwork or url not in {BANDCAMP_SEARCH, "https://api.acoustid.org/v2/lookup"}
        ):
            raise MetadataProviderError("metadata_url_rejected", retryable=False)
        ranking = url.startswith("https://api.listenbrainz.org/")
        native_host = native_image_host(url) if artwork else None
        archive_file = urlsplit(url).path if artwork and archive_image_url(url) else None
        catalog_kind = next(
            (kind for kind in ("cover", "artist") if artwork and image_url(url, kind)), None
        )
        limit = 4_194_304 if artwork or ranking else 1_048_576
        for _ in range(5):
            validate_public_url(url, artwork=artwork)
            headers = {"Accept": "image/*" if artwork else "application/json"}
            if publisher_track_url(url):
                headers["Accept"] = "text/html"
            if post is not None:
                headers["Content-Type"] = (
                    "application/json"
                    if url == BANDCAMP_SEARCH
                    else "application/x-www-form-urlencoded"
                )
            try:
                with (
                    self.gate(),
                    self.client.stream(
                        "POST" if post is not None else "GET", url, content=post, headers=headers
                    ) as response,
                ):
                    if response.status_code == 404:
                        return None
                    if (
                        response.status_code in {301, 302, 303, 307, 308}
                        and artwork
                        and post is None
                    ):
                        location = response.headers.get("Location")
                        if location:
                            url = urljoin(url, location)
                            if archive_file:
                                redirected = urlsplit(url)
                                suffix = archive_file.removeprefix("/download")
                                from urllib.parse import unquote

                                if (
                                    redirected.scheme != "https"
                                    or not (
                                        redirected.netloc == "archive.org"
                                        or redirected.netloc.endswith(".archive.org")
                                    )
                                    or not unquote(redirected.path).endswith(unquote(suffix))
                                ):
                                    raise MetadataProviderError(
                                        "metadata_url_rejected", retryable=False
                                    )
                            if (native_host and native_image_host(url) != native_host) or (
                                catalog_kind and not image_url(url, catalog_kind)
                            ):
                                raise MetadataProviderError(
                                    "metadata_url_rejected", retryable=False
                                )
                            continue
                    if response.status_code != 200:
                        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                        raise MetadataProviderError(
                            "metadata_provider_busy"
                            if response.status_code in {429, 503}
                            else "metadata_provider_unavailable",
                            retryable=response.status_code >= 500 or response.status_code == 429,
                            retry_after_seconds=retry_after,
                        )
                    payload = bytearray()
                    deadline = time.monotonic() + 30
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if time.monotonic() > deadline:
                            raise MetadataProviderError("metadata_network_unavailable")
                        payload.extend(chunk)
                        if len(payload) > limit:
                            raise MetadataProviderError(
                                "metadata_response_too_large", retryable=False
                            )
                    return bytes(payload)
            except httpx2.HTTPError as error:
                raise MetadataProviderError("metadata_network_unavailable") from error
        raise MetadataProviderError("metadata_redirect_limit", retryable=False)

    def json(self, url: str, *, post: bytes | None = None) -> dict[str, Any]:
        payload = self.get(url, post=post)
        if payload is None:
            return {}
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("object")
            return value
        except (ValueError, UnicodeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error


def _credit(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        str(item.get("name") or item.get("artist", {}).get("name") or "")
        + str(item.get("joinphrase") or "")
        for item in value
        if isinstance(item, dict)
    ).strip()[:500]


def _credit_members(value: Any) -> tuple[dict[str, str], ...]:
    """A flat label or a partial provider credit cannot establish any artist identity."""
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        return ()
    members = []
    prior_join = ""
    for item in value:
        if not isinstance(item, dict) or not isinstance(artist := item.get("artist"), dict):
            return ()
        try:
            identity = str(UUID(str(artist.get("id"))))
        except ValueError:
            return ()
        name = artist.get("name")
        credited = item.get("name") or name
        join = item.get("joinphrase", "")
        if not all(
            isinstance(field, str) and len(field) <= 1000 for field in (name, credited, join)
        ):
            return ()
        if (
            not isinstance(name, str)
            or not isinstance(credited, str)
            or not name.strip()
            or not credited.strip()
        ):
            return ()
        members.append(
            {
                "mbid": identity,
                "name": name,
                "credited_name": credited,
                "join_phrase": join,
                "role": "FEATURED"
                if prior_join.strip().casefold() in {"feat.", "feat", "ft.", "ft", "featuring"}
                else "PRIMARY",
            }
        )
        aliases = artist.get("aliases")
        if isinstance(aliases, list) and any(
            isinstance(alias, dict)
            and alias.get("type") == "Legal name"
            and alias.get("name") == credited
            for alias in aliases[:100]
        ):
            members[-1]["legal_name"] = credited
        prior_join = join
    if len("".join(m["credited_name"] + m["join_phrase"] for m in members)) > 2000:
        return ()
    return tuple(members)


def _fields(raw: dict[str, Any]) -> MetadataFields:
    result: MetadataFields = {}
    for key, value in raw.items():
        if value is not None and value != "":
            try:
                result.update(validate_fields({key: value}))
            except ValueError:
                continue
    return result


def _lucene(value: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r"\\\1", value[:500])


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and 1 <= len(value.strip()) <= 500 else None


def _types(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("types")
    return tuple(dict.fromkeys(text for item in value if (text := _optional_text(item))))


def _duration(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 3600000 else None


def _expressions(query: MetadataQuery) -> tuple[str, ...]:
    """Keep observed edition and duration constraints at every search width."""
    constraints: list[str] = []
    if query.album:
        constraints.append(f'release:"{_lucene(query.album)}"')
    if query.release_mbid:
        constraints.append(f"reid:{UUID(query.release_mbid)}")
    if query.release_date:
        # Known date precision stays a prefix, never a fabricated January date.
        constraints.append(f"date:{query.release_date}*")
    if query.duration_ms is not None:
        duration = query.duration_ms
        if type(duration) is not int or not 0 < duration <= 3600000:
            raise ValueError("duration")
        constraints.append(f"dur:[{max(1, duration - 2000)} TO {duration + 2000}]")
    expressions = [
        " AND ".join([f'recording:"{_lucene(title)}"', f'artist:"{_lucene(artist)}"', *constraints])
        for title, artist in metadata_search_terms(query.title, query.artist)
    ]
    if query.source_metadata and (isrc := query.source_metadata.external_ids.get("isrc")):
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", isrc) is None:
            raise ValueError("isrc")
        credit = [f'artist:"{_lucene(query.artist)}"'] if query.artist.strip() else []
        expressions.insert(0, " AND ".join([f"isrc:{isrc}", *credit, *constraints]))
    return tuple(expressions)


class MusicBrainzMetadataProvider:
    def __init__(self, http: PublicMetadataHttp | None = None) -> None:
        self.http = http or PublicMetadataHttp()
        self._last_deezer: tuple[MetadataQuery, list[dict[str, Any]]] | None = None

    def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        try:
            return self._search(query)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def _search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        incomplete = False
        if query.release_track_mbid and not (query.recording_mbid and query.release_mbid):
            raise MetadataProviderError("metadata_release_track_context_invalid", retryable=False)
        if query.recording_mbid and query.release_mbid:
            # Exact native/embedded IDs permit one direct edition lookup. This
            # complete membership proof is separate from truncated text search.
            recording_id, release_id = (
                str(UUID(query.recording_mbid)),
                str(UUID(query.release_mbid)),
            )
            candidate = MetadataCandidate(
                f"{recording_id}:{release_id}",
                {"mb_recording_id": recording_id, "mb_release_id": release_id},
                None,
                100,
                f"musicbrainz:release:{release_id}",
            )
            return (
                self.release(candidate, release_track_mbid=query.release_track_mbid)
                if query.release_track_mbid
                else self.release(candidate),
            )
        if query.recording_mbid:
            identity = str(UUID(query.recording_mbid))
            recording = self.http.json(
                f"https://musicbrainz.org/ws/2/recording/{identity}?inc=artist-credits+releases&fmt=json"
            )
            recordings = [dict(recording, score=100)] if recording.get("id") else []
        else:
            recordings = []
            expected_title = lookup_title(query.title, query.artist)
            for expression in _expressions(query):
                params = urlencode({"query": expression, "limit": 5, "fmt": "json"})
                data = self.http.json(f"https://musicbrainz.org/ws/2/recording?{params}")
                found = data.get("recordings", [])
                incomplete = incomplete or int(data.get("count", len(found))) > len(found)
                if not found:
                    continue
                if not recordings:
                    recordings = found
                if expression.startswith("isrc:") or any(
                    isinstance(row, dict)
                    and normalized(_credit(row.get("artist-credit")) or "")
                    == normalized(query.artist)
                    and catalogue_title_matches(
                        expected_title,
                        [lookup_title(row.get("title") or "", query.artist)],
                        _optional_text(row.get("disambiguation")),
                    )
                    for row in found
                ):
                    recordings = found
                    break
        results: list[MetadataCandidate] = []
        seen: set[str] = set()
        incomplete = incomplete or len(recordings) > 5
        for recording in recordings[:5]:
            if not isinstance(recording, dict):
                incomplete = True
                continue
            releases = recording.get("releases", [])
            incomplete = (
                incomplete
                or len(releases) > 20
                or int(recording.get("release-count", len(releases))) > len(releases)
            )
            for release in releases[:20]:
                fields = _fields(
                    {
                        "title": recording.get("title"),
                        "artist": _credit(recording.get("artist-credit")),
                        "album": release.get("title"),
                        "release_date": release.get("date"),
                        "original_release_date": recording.get("first-release-date"),
                        "mb_recording_id": recording.get("id"),
                        "mb_release_id": release.get("id"),
                        "mb_release_group_id": release.get("release-group", {}).get("id"),
                        "country": release.get("country"),
                    }
                )
                if not fields.get("mb_recording_id") or not fields.get("mb_release_id"):
                    incomplete = True
                    continue
                key = f"{fields['mb_recording_id']}:{fields['mb_release_id']}"
                if key in seen:
                    incomplete = True
                    continue
                seen.add(key)
                length = recording.get("length")
                duration = _duration(length)
                group = release.get("release-group", {})
                results.append(
                    MetadataCandidate(
                        key,
                        fields,
                        duration,
                        int(recording.get("score", 0)),
                        f"musicbrainz:release:{fields['mb_release_id']}",
                        release_status=_optional_text(release.get("status")),
                        release_primary_type=_optional_text(group.get("primary-type")),
                        release_secondary_types=_types(group.get("secondary-types", [])),
                        recording_disambiguation=_optional_text(recording.get("disambiguation")),
                        recording_title=_optional_text(recording.get("title")),
                    )
                )
        # Prefer an observed edition title, but preserve multiple matching editions for review.
        if query.album:
            results.sort(
                key=lambda item: (
                    normalized(str(item.fields.get("album", ""))) != normalized(query.album or "")
                )
            )
        incomplete = incomplete or len(results) > 5
        # An exact release ID can filter complete evidence, but cannot make an
        # incomplete response safe by hiding the unreturned editions.
        if query.release_mbid:
            results = [
                item for item in results if item.fields.get("mb_release_id") == query.release_mbid
            ]
        return tuple(replace(item, auto_eligible=not incomplete) for item in results[:5])

    def release(
        self, candidate: MetadataCandidate, *, release_track_mbid: str | None = None
    ) -> MetadataCandidate:
        try:
            return self._release(candidate, release_track_mbid=release_track_mbid)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def _release(
        self, candidate: MetadataCandidate, *, release_track_mbid: str | None = None
    ) -> MetadataCandidate:
        identity = str(UUID(str(candidate.fields["mb_release_id"])))
        requested_track = release_track_mbid or candidate.mb_release_track_id
        if requested_track is not None:
            requested_track = str(UUID(requested_track))
        data = self.http.json(
            f"https://musicbrainz.org/ws/2/release/{identity}?inc=artist-credits+labels+release-groups+recordings+genres&fmt=json"
        )
        if data.get("id") != identity:
            raise MetadataProviderError("metadata_release_missing", retryable=False)
        fields = dict(candidate.fields)
        fields.update(
            _fields(
                {
                    "album": data.get("title"),
                    "album_artist": _credit(data.get("artist-credit")),
                    "release_date": data.get("date"),
                    "country": data.get("country"),
                    "original_release_date": data.get("release-group", {}).get(
                        "first-release-date"
                    ),
                    "mb_release_group_id": data.get("release-group", {}).get("id"),
                }
            )
        )
        labels = [
            item.get("label", {}).get("name")
            for item in data.get("label-info", [])
            if item.get("label")
        ]
        if labels:
            fields.update(_fields({"label": labels[0]}))
        genres = [item.get("name") for item in data.get("genres", []) if isinstance(item, dict)]
        if genres:
            fields.update(_fields({"genres": genres[:12]}))
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        incomplete = False
        media = data.get("media", [])
        incomplete = int(data.get("medium-count", len(media))) > len(media)
        requested_occurrences = 0
        for medium in media:
            tracks = medium.get("tracks", [])
            incomplete = incomplete or int(medium.get("track-count", len(tracks))) > len(tracks)
            for track in tracks:
                if requested_track is not None and track.get("id") == requested_track:
                    requested_occurrences += 1
                if track.get("recording", {}).get("id") == fields.get("mb_recording_id") and (
                    requested_track is None or track.get("id") == requested_track
                ):
                    matches.append((medium, track))
        if not matches:
            raise MetadataProviderError(
                "metadata_release_track_missing"
                if requested_track
                else "metadata_release_recording_missing",
                retryable=False,
            )
        # The same recording may occur twice on a deluxe release. Never claim
        # the first position as exact, even for a manually selected edition.
        ambiguous = incomplete or len(matches) != 1 or requested_occurrences > 1
        duration = candidate.duration_ms
        disambiguation = candidate.recording_disambiguation
        recording_title = candidate.recording_title
        release_track_id = None
        fields.pop("track_number", None)
        fields.pop("disc_number", None)
        if not ambiguous:
            medium, track = matches[0]
            recording = track.get("recording", {})
            if track.get("id") is not None:
                release_track_id = str(UUID(track["id"]))
            fields.update(
                _fields(
                    {
                        "track_number": track.get("position"),
                        "disc_number": medium.get("position"),
                        "title": track.get("title") or recording.get("title"),
                        "artist": _credit(track.get("artist-credit"))
                        or _credit(recording.get("artist-credit")),
                    }
                )
            )
            duration = (
                _duration(track.get("length")) or _duration(recording.get("length")) or duration
            )
            disambiguation = _optional_text(recording.get("disambiguation")) or disambiguation
            recording_title = _optional_text(recording.get("title")) or recording_title
        group = data.get("release-group", {})
        status = _optional_text(data.get("status"))
        return replace(
            candidate,
            fields=fields,
            duration_ms=duration,
            auto_eligible=candidate.auto_eligible
            and not ambiguous
            and normalized(status or "") == "official",
            release_status=status,
            release_primary_type=_optional_text(group.get("primary-type")),
            release_secondary_types=_types(group.get("secondary-types", [])),
            release_hydrated=True,
            release_position_ambiguous=ambiguous,
            recording_disambiguation=disambiguation,
            recording_title=recording_title,
            mb_release_track_id=release_track_id,
            recording_members=_credit_members(
                matches[0][1].get("recording", {}).get("artist-credit")
            )
            if not ambiguous
            else (),
            release_members=_credit_members(data.get("artist-credit")),
            track_members=_credit_members(matches[0][1].get("artist-credit"))
            if not ambiguous
            else (),
        )

    def _deezer_json(self, url: str) -> dict[str, Any]:
        page = self.http.json(url)
        error = page.get("error")
        if isinstance(error, dict) and error.get("code") in (4, 700):
            raise MetadataProviderError("metadata_provider_busy", retryable=True)
        return page

    def find_deezer_artwork(self, query: MetadataQuery) -> ArtMatch | None:
        if not query.artist or not query.title or not query.duration_ms:
            return None
        rows: list[dict[str, Any]] = []
        for title, artist in metadata_search_terms(query.title, query.artist):
            page = self._deezer_json(search_url(artist, title, clean_title=False))
            rows.extend(page.get("data") or [])
            match = match_artwork(rows, query.title, query.artist, query.album, query.duration_ms)
            if match and match.cover:
                break
        self._last_deezer = (query, rows)
        return match_artwork(rows, query.title, query.artist, query.album, query.duration_ms)

    def find_yandex_artwork(self, query: MetadataQuery) -> ArtMatch | None:
        rows: list[dict[str, Any]] = []
        match = None
        for title, artist in metadata_search_terms(query.title, query.artist):
            result = (
                self.http.json(yandex_search_url(artist, title, clean_title=False)).get("result")
                or {}
            )
            tracks = result.get("tracks") if isinstance(result, dict) else None
            if isinstance(tracks, dict):
                rows.extend(tracks.get("results") or [])
            match = yandex_match(rows, query.title, query.artist, query.album, query.duration_ms)
            if match and match.cover:
                break
        if (not match or not match.cover) and self._last_deezer and self._last_deezer[0] == query:
            editions = yandex_editions(
                rows, query.title, query.artist, query.album, query.duration_ms
            )
            if editions:
                corroborated = match_artwork(
                    self._last_deezer[1],
                    query.title,
                    query.artist,
                    query.album,
                    query.duration_ms,
                    confirmed_albums=editions,
                )
                if corroborated and corroborated.cover:
                    same_edition = yandex_match(
                        rows, query.title, query.artist, corroborated.album, query.duration_ms
                    )
                    return same_edition if same_edition and same_edition.cover else corroborated
        return match

    def find_publisher_cover(self, query: MetadataQuery) -> ArtMatch | None:
        page = self.http.json(BANDCAMP_SEARCH, post=bandcamp_query(query.artist, query.title))
        candidates = publisher_candidates(page, query.artist, query.title, query.album)
        matches = []
        for url, row in candidates[:3]:
            try:
                payload = self.http.get(url)
            except MetadataProviderError:
                if result := indexed_publisher_cover(
                    candidates, query.title, query.artist, query.album
                ):
                    return result
                raise
            match = publisher_match(
                payload, row, url, query.title, query.artist, query.album, query.duration_ms
            )
            if match:
                matches.append(match)
            elif (not payload or b"data-tralbum" not in payload) and (
                result := indexed_publisher_cover(
                    candidates, query.title, query.artist, query.album
                )
            ):
                return result
        return matches[0] if len({(m.artist_id, m.cover) for m in matches}) == 1 else None

    def find_listenbrainz_cover(
        self, query: MetadataQuery, review: tuple[MetadataCandidate, ...] = ()
    ) -> ArtMatch | None:
        recording = mbid(query.recording_mbid)
        ids = (
            [recording]
            if recording
            else sorted(
                {
                    value
                    for candidate in review
                    if (value := mbid(candidate.fields.get("mb_recording_id")))
                }
            )
        )
        if not ids:
            return None
        return listenbrainz_cover(
            self.http.json(listenbrainz_url(ids[:5])),
            query.title,
            query.artist,
            query.album,
            query.duration_ms,
            selected_release=query.release_mbid,
        )

    def _original_by_credit(
        self, query: MetadataQuery, kind: str, credit: tuple[str, str]
    ) -> ArtMatch | None:
        rows = self._deezer_json(search_url(*credit, clean_title=False)).get("data")
        match = catalog_original(rows, query, kind, credit)
        if match:
            album = self._deezer_json(f"https://api.deezer.com/album/{match.album_id}")
            owner = album.get("artist")
            if (
                album.get("id") == match.album_id
                and album.get("record_type") in {"album", "single", "ep"}
                and isinstance(owner, dict)
                and owner.get("id") == match.artist_id
                and isinstance(album.get("title"), str)
                and label(album["title"]) == label(match.album)
                and album.get("cover_big") == match.cover
            ):
                return match
        return None

    def _cover_recording_id(self, query: MetadataQuery, version: str | None) -> str | None:
        # Identify the performed cover first. Its work relation supplies the original.
        if not query.duration_ms:
            return None
        base, _ = variation_title(query, "COVER", version)
        expression = f'artist:"{_lucene(query.artist)}" AND recording:"{_lucene(base)}"'
        url = "https://musicbrainz.org/ws/2/recording/?" + urlencode(
            {"query": expression, "limit": 25, "fmt": "json"}
        )
        page = self.http.json(url)
        rows = page.get("recordings")
        if not isinstance(rows, list) or page.get("count") != len(rows):
            return None
        ids = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                continue
            length, recording = _duration(row.get("length")), mbid(row.get("id"))
            if (
                length
                and recording
                and abs(length - query.duration_ms) <= 2000
                and normalized(row["title"]) == normalized(base)
                and normalized(_credit(row.get("artist-credit"))) == normalized(query.artist)
            ):
                ids.add(recording)
        return ids.pop() if len(ids) == 1 else None

    def find_original_artwork(
        self,
        query: MetadataQuery,
        performance: object = None,
        recording_kind: str | None = None,
        version: str | None = None,
    ) -> ArtMatch | None:
        recording = mbid(query.recording_mbid)
        link = (
            performance
            if recording
            and isinstance(performance, dict)
            and performance.get("recording_id") == recording
            else {}
        )
        kind = variation_kind(
            link.get("role"),
            bool(link.get("live")),
            recording_kind,
            version,
            lookup_title(query.title, query.artist),
        )
        if kind not in {"COVER", "REMIX"}:
            return None
        if not recording:
            credit = credited_original(query, kind, version)
            if credit and (match := self._original_by_credit(query, kind, credit)):
                return match
            recording = self._cover_recording_id(query, version) if kind == "COVER" else None
            if not recording:
                return None

        original = mbid(link.get("original_recording_id")) if link.get("role") == kind else None
        if not original:
            detail = self.http.json(
                f"https://musicbrainz.org/ws/2/recording/{recording}?inc=work-rels+recording-rels&fmt=json"
            )
            if detail.get("id") != recording:
                return None
            role, work_id = recording_work(detail)
            sources = remix_sources(detail)
            if kind == "REMIX" and role != "COVER" and len(sources) == 1:
                original = sources.pop()
            elif kind == "COVER" and role == "COVER" and work_id and not sources:
                work = self.http.json(
                    f"https://musicbrainz.org/ws/2/work/{work_id}?inc=recording-rels&fmt=json"
                )
                original = cover_original(work, work_id, recording)
        if original and original != recording:
            match = linked_original(
                self.http.json(listenbrainz_url([original])), query, kind, original
            )
            if match:
                # A recording can also appear on unrelated various-artists compilations.
                release = self.http.json(
                    f"https://musicbrainz.org/ws/2/release/{match.album_id}?inc=artist-credits+release-groups&fmt=json"
                )
                group = release.get("release-group")
                if (
                    release.get("id") == match.album_id
                    and release.get("status") == "Official"
                    and isinstance(group, dict)
                    and group.get("primary-type") in {"Album", "EP", "Single"}
                    and group.get("secondary-types") == []
                    and label(_credit(release.get("artist-credit"))) == label(match.artist)
                ):
                    return match
        return None

    def find_archive_cover(self, query: MetadataQuery) -> ArtMatch | None:
        if not query.artist or not query.title or not query.duration_ms or query.release_mbid:
            return None
        choices = []
        ids = archive_items(self.http.json(archive_search(query.artist)))
        for identifier in ids[:6]:
            item = self.http.json("https://archive.org/metadata/" + identifier)
            if match := archive_cover(
                item, identifier, query.title, query.artist, query.album, query.duration_ms
            ):
                choices.append(match)
        return choices[0] if len({m.cover for m in choices}) == 1 else None

    def public_image(self, url: str) -> bytes | None:
        archive = url.startswith("https://coverartarchive.org/") and is_cover_url(url)
        if not (
            archive
            or archive_image_url(url)
            or image_url(url, "cover")
            or image_url(url, "artist")
            or native_image_host(url)
        ):
            return None
        return self.http.get(url, artwork=True)

    def group_cover(self, group_id: str) -> bytes | None:
        return self.http.get(
            f"https://coverartarchive.org/release-group/{UUID(group_id)}/front-500", artwork=True
        )

    def cover(self, release_id: str) -> bytes | None:
        identity = str(UUID(release_id))
        return self.http.get(
            f"https://coverartarchive.org/release/{identity}/front-500", artwork=True
        )

    def acoustid(self, fingerprint: str, duration_seconds: int, key: str) -> tuple[str, ...]:
        try:
            return self._acoustid(fingerprint, duration_seconds, key)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def _acoustid(self, fingerprint: str, duration_seconds: int, key: str) -> tuple[str, ...]:
        if not key:
            return ()
        body = urlencode(
            {
                "client": key,
                "duration": duration_seconds,
                "fingerprint": fingerprint,
                "meta": "recordings",
                "format": "json",
            }
        ).encode()
        data = self.http.json("https://api.acoustid.org/v2/lookup", post=body)
        if data.get("status") != "ok":
            raise MetadataProviderError("metadata_acoustid_unavailable", retryable=False)
        identities = {
            str(UUID(recording["id"]))
            for match in data.get("results", [])
            if float(match.get("score", 0)) >= 0.98
            for recording in match.get("recordings", [])
        }
        return tuple(sorted(identities))[:5]


def retry_after_seconds(value: str | None) -> int:
    if value is None:
        return 60
    try:
        seconds = (
            int(value)
            if value.isdecimal()
            else int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        )
        return max(1, min(seconds, 86400))
    except ValueError, TypeError, OverflowError:
        return 60
