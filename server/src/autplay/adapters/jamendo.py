"""Official Jamendo API v3 adapter for manual, artist-authorized acquisition."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from autplay.domain.discovery import (
    DiscoveryCandidate,
    DiscoveryError,
    ProviderArtist,
    ProviderArtistTracks,
)
from autplay.ports.source_adapters import (
    AdapterLimits,
    CredentialRequirement,
    NetworkPolicy,
    SourceAdapterManifest,
    SourceCapability,
)

JAMENDO_ADAPTER_VERSION = "1.0.0"
JAMENDO_API_VERSION = "3.0"
JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"
JAMENDO_ARTISTS_URL = "https://api.jamendo.com/v3.0/artists/"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 25
_AUDIO_TYPES = frozenset(
    {"application/octet-stream", "audio/mp3", "audio/mpeg", "binary/octet-stream"}
)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


class JamendoProvider:
    """Bounded HTTPS client with an exact provider/storage egress allowlist."""

    manifest = SourceAdapterManifest(
        adapter_id="autplay.jamendo.manual",
        adapter_version=JAMENDO_ADAPTER_VERSION,
        capabilities=(
            SourceCapability.RELEASE_DISCOVERY,
            SourceCapability.PLAYABLE_ACQUISITION,
        ),
        credential_requirement=CredentialRequirement.REQUIRED_OPERATOR_SECRET,
        network_policy=NetworkPolicy.PUBLIC_ALLOWLIST_ONLY,
        limits=AdapterLimits(0, 1, MAX_RESULTS, 15.0),
    )

    def __init__(self, client_id: str, *, timeout_seconds: float = 15.0) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{4,100}", client_id) is None:
            raise ValueError("Jamendo client ID is invalid")
        if not 1.0 <= timeout_seconds <= self.manifest.limits.timeout_seconds:
            raise ValueError("Jamendo timeout is invalid")
        self._client_id = client_id
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirects())

    def search(self, query: str, *, limit: int) -> tuple[DiscoveryCandidate, ...]:
        normalized = " ".join(query.split())
        if not normalized or len(normalized) > 200:
            raise DiscoveryError("discovery_query_invalid")
        if not 1 <= limit <= self.manifest.limits.max_results_per_query:
            raise DiscoveryError("discovery_limit_invalid")
        payload = self._request_json(
            JAMENDO_TRACKS_URL,
            {
                "format": "json",
                "limit": str(limit),
                "order": "relevance",
                "search": normalized,
                "include": "licenses",
                "audiodlformat": "mp32",
                "type": "single albumtrack",
            },
        )
        return _parse_candidates(payload, limit=limit)

    def lookup(self, provider_track_id: str) -> DiscoveryCandidate:
        if re.fullmatch(r"\d{1,20}", provider_track_id) is None:
            raise DiscoveryError("discovery_target_not_found")
        payload = self._request_json(
            JAMENDO_TRACKS_URL,
            {
                "format": "json",
                "limit": "1",
                "id": provider_track_id,
                "include": "licenses",
                "audiodlformat": "mp32",
            },
        )
        candidates = _parse_candidates(payload, limit=1)
        if len(candidates) != 1 or candidates[0].provider_track_id != provider_track_id:
            raise DiscoveryError("discovery_target_not_found")
        return candidates[0]

    def search_artists(self, name: str, *, limit: int = 3) -> tuple[ProviderArtist, ...]:
        normalized = " ".join(name.split())
        if not normalized or len(normalized) > 500 or not 1 <= limit <= 10:
            raise DiscoveryError("discovery_query_invalid")
        payload = self._request_json(
            JAMENDO_ARTISTS_URL,
            {
                "format": "json",
                "limit": str(limit),
                "name": normalized,
                "order": "popularity_total",
            },
        )
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DiscoveryError("discovery_provider_response_invalid") from error
        if not isinstance(document, dict):
            raise DiscoveryError("discovery_provider_response_invalid")
        headers = document.get("headers")
        results = document.get("results")
        if (
            not isinstance(headers, dict)
            or headers.get("status") != "success"
            or headers.get("code") != 0
            or not isinstance(results, list)
            or len(results) > limit
        ):
            raise DiscoveryError("discovery_provider_response_invalid")
        artists: list[ProviderArtist] = []
        for raw in results:
            if not isinstance(raw, dict):
                raise DiscoveryError("discovery_provider_response_invalid")
            try:
                artists.append(
                    ProviderArtist(
                        provider_artist_id=_digits(raw, "id"),
                        name=_text(raw, "name", 500),
                        share_url=_text(raw, "shareurl", 1_000),
                    )
                )
            except ValueError as error:
                raise DiscoveryError("discovery_provider_response_invalid") from error
        return tuple(artists)

    def top_tracks(self, provider_artist_id: str, *, limit: int = 25) -> ProviderArtistTracks:
        """Return the most popular bounded prefix and the provider's full count."""

        if re.fullmatch(r"\d{1,20}", provider_artist_id) is None:
            raise DiscoveryError("discovery_target_not_found")
        if not 1 <= limit <= self.manifest.limits.max_results_per_query:
            raise DiscoveryError("discovery_limit_invalid")
        payload = self._request_json(
            JAMENDO_TRACKS_URL,
            {
                "format": "json",
                "limit": str(limit),
                "order": "popularity_total",
                "artist_id": provider_artist_id,
                "fullcount": "true",
                "include": "licenses",
                "audiodlformat": "mp32",
                "type": "single albumtrack",
            },
        )
        candidates = _parse_candidates(payload, limit=limit)
        total_count = _parse_full_count(payload)
        try:
            return ProviderArtistTracks(provider_artist_id, total_count, candidates)
        except ValueError as error:
            raise DiscoveryError("discovery_provider_response_invalid") from error

    def acquire(
        self,
        candidate: DiscoveryCandidate,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        if not candidate.acquisition_allowed or candidate.download_url is None:
            raise DiscoveryError("discovery_not_eligible")
        if not 1_024 <= max_bytes <= 1024 * 1024 * 1024:
            raise ValueError("Jamendo acquisition limit is invalid")
        request = Request(
            candidate.download_url,
            headers={
                "Accept": "audio/mpeg,application/octet-stream;q=0.9",
                "User-Agent": "AutPlay-Jamendo-Manual/1.0",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                content_type = response.headers.get_content_type().casefold()
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise DiscoveryError("discovery_response_too_large")
                byte_count = 0
                with destination.open("xb") as output:
                    while chunk := response.read(min(1024 * 1024, max_bytes + 1 - byte_count)):
                        byte_count += len(chunk)
                        if byte_count > max_bytes:
                            raise DiscoveryError("discovery_response_too_large")
                        output.write(chunk)
        except DiscoveryError:
            destination.unlink(missing_ok=True)
            raise
        except HTTPError, URLError, OSError, ValueError:
            destination.unlink(missing_ok=True)
            raise DiscoveryError("discovery_acquisition_failed") from None
        if (
            byte_count < 1_024
            or content_type not in _AUDIO_TYPES
            or not _looks_like_mp3(destination)
        ):
            destination.unlink(missing_ok=True)
            raise DiscoveryError("discovery_content_invalid")
        return byte_count

    def _request_json(self, url: str, parameters: Mapping[str, str]) -> bytes:
        query = urlencode({"client_id": self._client_id, **parameters})
        request = Request(
            f"{url}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "AutPlay-Jamendo-Manual/1.0",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                    raise DiscoveryError("discovery_response_too_large")
                payload = bytes(response.read(MAX_RESPONSE_BYTES + 1))
        except DiscoveryError:
            raise
        except HTTPError, URLError, OSError, ValueError:
            raise DiscoveryError("discovery_adapter_unavailable") from None
        if len(payload) > MAX_RESPONSE_BYTES:
            raise DiscoveryError("discovery_response_too_large")
        return payload


def _parse_candidates(payload: bytes, *, limit: int) -> tuple[DiscoveryCandidate, ...]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryError("discovery_provider_response_invalid") from error
    if not isinstance(document, dict):
        raise DiscoveryError("discovery_provider_response_invalid")
    headers = document.get("headers")
    results = document.get("results")
    if (
        not isinstance(headers, dict)
        or headers.get("status") != "success"
        or headers.get("code") != 0
    ):
        raise DiscoveryError("discovery_adapter_unavailable")
    if not isinstance(results, list) or len(results) > limit:
        raise DiscoveryError("discovery_provider_response_invalid")
    candidates: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    for raw in results:
        if not isinstance(raw, dict):
            raise DiscoveryError("discovery_provider_response_invalid")
        candidate = _parse_candidate(raw)
        if candidate.provider_track_id not in seen:
            seen.add(candidate.provider_track_id)
            candidates.append(candidate)
    return tuple(candidates)


def _parse_full_count(payload: bytes) -> int:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryError("discovery_provider_response_invalid") from error
    headers = document.get("headers") if isinstance(document, dict) else None
    raw = headers.get("results_fullcount") if isinstance(headers, dict) else None
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1_000_000:
        raise DiscoveryError("discovery_provider_response_invalid")
    return raw


def _parse_candidate(value: Mapping[str, object]) -> DiscoveryCandidate:
    track_id = _digits(value, "id")
    allowed = value.get("audiodownload_allowed")
    if not isinstance(allowed, bool):
        raise DiscoveryError("discovery_provider_response_invalid")
    download_url = _optional_text(value, "audiodownload", 2_048)
    if not allowed:
        download_url = None
    try:
        return DiscoveryCandidate(
            provider_track_id=track_id,
            provider_artist_id=_digits(value, "artist_id"),
            title=_text(value, "name", 500),
            artist=_text(value, "artist_name", 500),
            album=_optional_text(value, "album_name", 500),
            duration_seconds=_integer(value, "duration", 24 * 60 * 60),
            license_url=_text(value, "license_ccurl", 1_000),
            share_url=_text(value, "shareurl", 1_000),
            acquisition_allowed=allowed,
            download_url=download_url,
        )
    except ValueError as error:
        raise DiscoveryError("discovery_provider_response_invalid") from error


def _text(value: Mapping[str, object], key: str, limit: int) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise DiscoveryError("discovery_provider_response_invalid")
    cleaned = " ".join(raw.split())
    if not 1 <= len(cleaned) <= limit:
        raise DiscoveryError("discovery_provider_response_invalid")
    return cleaned


def _optional_text(value: Mapping[str, object], key: str, limit: int) -> str | None:
    if value.get(key) in {None, ""}:
        return None
    return _text(value, key, limit)


def _digits(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str) or re.fullmatch(r"\d{1,20}", raw) is None:
        raise DiscoveryError("discovery_provider_response_invalid")
    return raw


def _integer(value: Mapping[str, object], key: str, upper: int) -> int:
    raw = value.get(key)
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= upper:
        raise DiscoveryError("discovery_provider_response_invalid")
    return raw


def _looks_like_mp3(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(512)
    except OSError as error:
        raise DiscoveryError("discovery_content_invalid") from error
    return header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
    )


__all__ = ("JAMENDO_ADAPTER_VERSION", "JamendoProvider")
