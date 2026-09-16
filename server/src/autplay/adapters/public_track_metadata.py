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

from autplay.domain.track_metadata import (
    MetadataCandidate,
    MetadataFields,
    MetadataQuery,
    validate_fields,
)
from autplay.ports.track_metadata import MetadataProviderError

USER_AGENT = "AutPlay/0.3.7 (personal music library; https://github.com/Dakstar-cool/AutPlay)"


def validate_public_url(url: str, *, artwork: bool = False) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    allowed = host in {"musicbrainz.org", "coverartarchive.org", "api.acoustid.org"}
    if artwork:
        allowed = allowed or host == "archive.org" or host.endswith(".archive.org")
    if (
        not allowed
        or parsed.scheme != "https"
        or parsed.port not in (None, 443)
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

    def get(self, url: str, *, artwork: bool = False, post: bytes | None = None) -> bytes | None:
        limit = 4194304 if artwork else 1048576
        for _ in range(5):
            validate_public_url(url, artwork=artwork)
            headers = {"Accept": "image/*" if artwork else "application/json"}
            if post is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
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


class MusicBrainzMetadataProvider:
    def __init__(self, http: PublicMetadataHttp | None = None) -> None:
        self.http = http or PublicMetadataHttp()

    def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        try:
            return self._search(query)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def _search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        incomplete = False
        if query.recording_mbid:
            identity = str(UUID(query.recording_mbid))
            recording = self.http.json(
                f"https://musicbrainz.org/ws/2/recording/{identity}?inc=artist-credits+releases&fmt=json"
            )
            recordings = [dict(recording, score=100)] if recording.get("id") else []
        elif query.title.strip() and query.artist.strip():
            expression = f'recording:"{_lucene(query.title)}" AND artist:"{_lucene(query.artist)}"'
            params = urlencode({"query": expression, "limit": 5, "fmt": "json"})
            data = self.http.json(f"https://musicbrainz.org/ws/2/recording?{params}")
            recordings = data.get("recordings", [])
            incomplete = int(data.get("count", len(recordings))) > len(recordings)
        else:
            return ()
        results: list[MetadataCandidate] = []
        seen: set[str] = set()
        incomplete = incomplete or len(recordings) > 5
        for recording in recordings[:5]:
            if not isinstance(recording, dict):
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
                    continue
                key = f"{fields['mb_recording_id']}:{fields['mb_release_id']}"
                if key in seen:
                    continue
                seen.add(key)
                length = recording.get("length")
                duration = length if isinstance(length, int) and length > 0 else None
                results.append(
                    MetadataCandidate(
                        key,
                        fields,
                        duration,
                        int(recording.get("score", 0)),
                        f"musicbrainz:release:{fields['mb_release_id']}",
                    )
                )
        # Prefer an observed edition title, but preserve multiple matching editions for review.
        if query.album:
            from autplay.domain.track_metadata import normalized

            results.sort(
                key=lambda item: (
                    normalized(str(item.fields.get("album", ""))) != normalized(query.album or "")
                )
            )
        incomplete = incomplete or len(results) > 5
        return tuple(replace(item, auto_eligible=not incomplete) for item in results[:5])

    def release(self, candidate: MetadataCandidate) -> MetadataCandidate:
        try:
            return self._release(candidate)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

    def _release(self, candidate: MetadataCandidate) -> MetadataCandidate:
        identity = str(UUID(str(candidate.fields["mb_release_id"])))
        data = self.http.json(
            f"https://musicbrainz.org/ws/2/release/{identity}?inc=artist-credits+labels+release-groups+recordings&fmt=json"
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
        for medium in data.get("media", []):
            for track in medium.get("tracks", []):
                if track.get("recording", {}).get("id") == fields.get("mb_recording_id"):
                    fields.update(
                        _fields(
                            {
                                "track_number": track.get("position"),
                                "disc_number": medium.get("position"),
                            }
                        )
                    )
                    return MetadataCandidate(
                        candidate.candidate_id,
                        fields,
                        candidate.duration_ms,
                        candidate.score,
                        candidate.source_id,
                    )
        raise MetadataProviderError("metadata_release_recording_missing", retryable=False)

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
