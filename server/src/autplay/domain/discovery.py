"""Provider evidence values for manual release discovery and acquisition staging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit
from uuid import UUID

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
class AcquisitionAuthorizationReceipt:
    """Ephemeral evidence from a fresh provider check at one ingest boundary."""

    candidate_id: UUID
    provider_track_id: str
    provider_artist_id: str
    boundary: str
    checked_at: datetime

    def __post_init__(self) -> None:
        if _TRACK_ID.fullmatch(self.provider_track_id) is None:
            raise ValueError("provider track id is invalid")
        if _TRACK_ID.fullmatch(self.provider_artist_id) is None:
            raise ValueError("provider artist id is invalid")
        if self.boundary not in {"PRE_PUBLISH", "PRE_MATERIALIZE"}:
            raise ValueError("acquisition authorization boundary is invalid")
        if self.checked_at.tzinfo is None:
            raise ValueError("authorization receipt time must be timezone-aware")


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
class ProviderTrackObservation:
    """One release-ordered provider observation, never Catalog identity or a locator."""

    candidate: DiscoveryCandidate
    release_date: date
    release_timezone: str

    def __post_init__(self) -> None:
        # Jamendo exposes a calendar date rather than a local release instant. Keeping its
        # explicit UTC interpretation prevents adapter-local time from changing checkpoints.
        if self.release_timezone != "UTC":
            raise ValueError("provider release timezone is invalid")


@dataclass(frozen=True, slots=True)
class ProviderTrackPage:
    """One fixed scheduled-discovery page with a bounded opaque resume marker."""

    provider_artist_id: str
    offset: int
    observations: tuple[ProviderTrackObservation, ...]
    next_offset: int | None
    checkpoint: str | None

    def __post_init__(self) -> None:
        if _TRACK_ID.fullmatch(self.provider_artist_id) is None:
            raise ValueError("provider artist id is invalid")
        if self.offset not in {0, 25}:
            raise ValueError("provider page offset is invalid")
        if len(self.observations) > 25:
            raise ValueError("provider page is too large")
        if any(
            observation.candidate.provider_artist_id != self.provider_artist_id
            for observation in self.observations
        ):
            raise ValueError("provider page contains another artist")
        track_ids = tuple(
            observation.candidate.provider_track_id for observation in self.observations
        )
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("provider page contains duplicate tracks")
        order = tuple(
            (observation.release_date, int(observation.candidate.provider_track_id))
            for observation in self.observations
        )
        if order != tuple(sorted(order, reverse=True)):
            raise ValueError("provider page ordering is invalid")
        expected_next_offset = 25 if self.offset == 0 and len(self.observations) == 25 else None
        if self.next_offset != expected_next_offset:
            raise ValueError("provider page continuation is invalid")
        if self.checkpoint is not None and not 1 <= len(self.checkpoint) <= 2_048:
            raise ValueError("provider page checkpoint is invalid")
        if bool(self.observations) != (self.checkpoint is not None):
            raise ValueError("provider page checkpoint is inconsistent")


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
    "ProviderTrackObservation",
    "ProviderTrackPage",
    "StagedAcquisition",
)
