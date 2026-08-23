"""Provider evidence values for manual release discovery and acquisition staging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_TRACK_ID = re.compile(r"\d{1,20}")


class DiscoveryError(RuntimeError):
    """Stable provider- and credential-free discovery failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """Bounded Jamendo evidence; it is never canonical Catalog identity."""

    provider_track_id: str
    provider_artist_id: str
    title: str
    artist: str
    album: str | None
    duration_seconds: int
    license_url: str
    share_url: str
    acquisition_allowed: bool
    download_url: str | None = None

    def __post_init__(self) -> None:
        if _TRACK_ID.fullmatch(self.provider_track_id) is None:
            raise ValueError("provider track id is invalid")
        if _TRACK_ID.fullmatch(self.provider_artist_id) is None:
            raise ValueError("provider artist id is invalid")
        for value in (self.title, self.artist):
            if not 1 <= len(value) <= 500:
                raise ValueError("candidate display value is invalid")
        if self.album is not None and not 1 <= len(self.album) <= 500:
            raise ValueError("candidate album is invalid")
        if not 1 <= self.duration_seconds <= 24 * 60 * 60:
            raise ValueError("candidate duration is invalid")
        if not _is_creative_commons_url(self.license_url):
            raise ValueError("candidate license URL is invalid")
        if not _is_jamendo_share_url(self.share_url):
            raise ValueError("candidate share URL is invalid")
        if self.acquisition_allowed != (self.download_url is not None):
            raise ValueError("candidate acquisition evidence is inconsistent")
        if self.download_url is not None and not _is_jamendo_download_url(
            self.download_url, self.provider_track_id
        ):
            raise ValueError("candidate download URL is invalid")


@dataclass(frozen=True, slots=True)
class StagedAcquisition:
    """A downloaded provider object that has not entered the Vault or Catalog."""

    operation_id: str
    provider_track_id: str
    audio_name: str
    attribution_name: str
    byte_count: int
    duplicate: bool = False

    def __post_init__(self) -> None:
        if self.byte_count < 1:
            raise ValueError("staged acquisition byte count is invalid")


@dataclass(frozen=True, slots=True)
class ProviderArtist:
    """One explicit Jamendo artist identity proposed for manual confirmation."""

    provider_artist_id: str
    name: str
    share_url: str

    def __post_init__(self) -> None:
        if _TRACK_ID.fullmatch(self.provider_artist_id) is None:
            raise ValueError("provider artist id is invalid")
        if not 1 <= len(self.name) <= 500:
            raise ValueError("provider artist name is invalid")
        if not _is_jamendo_share_url(self.share_url):
            raise ValueError("provider artist share URL is invalid")


@dataclass(frozen=True, slots=True)
class ProviderArtistTracks:
    """A popularity-ordered, bounded prefix of one provider artist catalog."""

    provider_artist_id: str
    total_count: int
    tracks: tuple[DiscoveryCandidate, ...]

    def __post_init__(self) -> None:
        if _TRACK_ID.fullmatch(self.provider_artist_id) is None:
            raise ValueError("provider artist id is invalid")
        if not 0 <= self.total_count <= 1_000_000:
            raise ValueError("provider artist track count is invalid")
        if len(self.tracks) > self.total_count:
            raise ValueError("provider artist track page is inconsistent")
        if any(track.provider_artist_id != self.provider_artist_id for track in self.tracks):
            raise ValueError("provider artist track page contains another artist")


@dataclass(frozen=True, slots=True)
class BulkArtistResolution:
    """Safe mapping from an imported display name to one explicit provider identity."""

    collection_name: str
    collection_track_count: int
    state: str
    provider_artist: ProviderArtist | None

    def __post_init__(self) -> None:
        if not 1 <= len(self.collection_name) <= 500:
            raise ValueError("collection artist name is invalid")
        if not 1 <= self.collection_track_count <= 10_000:
            raise ValueError("collection artist track count is invalid")
        if self.state not in {"EXACT_MATCH", "AMBIGUOUS", "NOT_FOUND"}:
            raise ValueError("bulk artist resolution state is invalid")
        if (self.state == "EXACT_MATCH") != (self.provider_artist is not None):
            raise ValueError("bulk artist resolution is inconsistent")


def _is_creative_commons_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").casefold() in {"creativecommons.org", "www.creativecommons.org"}
        and parsed.path.startswith("/licenses/")
        and parsed.username is None
    )


def _is_jamendo_share_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and (host in {"jamendo.com", "jamen.do"} or host.endswith(".jamendo.com"))
        and parsed.username is None
    )


def _is_jamendo_download_url(value: str, track_id: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    expected_prefix = f"/download/track/{track_id}/"
    return (
        parsed.scheme == "https"
        and re.fullmatch(r"prod-\d+\.storage\.jamendo\.com", host) is not None
        and parsed.path.startswith(expected_prefix)
        and parsed.username is None
        and parsed.port in {None, 443}
    )


__all__ = (
    "BulkArtistResolution",
    "DiscoveryCandidate",
    "DiscoveryError",
    "ProviderArtist",
    "ProviderArtistTracks",
    "StagedAcquisition",
)
