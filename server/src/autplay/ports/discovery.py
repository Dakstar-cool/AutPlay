"""Ports for provider discovery and independently authorized byte acquisition."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from autplay.domain.discovery import (
    AcquisitionAuthorizationReceipt,
    DiscoveryCandidate,
    ProviderArtist,
    ProviderArtistTracks,
    ProviderTrackPage,
)


class AcquisitionBoundaryAuthorizer(Protocol):
    """Recheck a provider source without exposing its locator to Vault code."""

    def authorize(
        self,
        candidate_id: UUID,
        provider_track_id: str,
        *,
        boundary: str,
        owner_user_id: UUID,
        acquisition_attempt_id: UUID,
    ) -> AcquisitionAuthorizationReceipt: ...


class DiscoveryProvider(Protocol):
    """One named provider with separate metadata and playable capabilities."""

    def search(self, query: str, *, limit: int) -> tuple[DiscoveryCandidate, ...]: ...

    def lookup(self, provider_track_id: str) -> DiscoveryCandidate: ...

    def search_artists(self, name: str, *, limit: int) -> tuple[ProviderArtist, ...]: ...

    def top_tracks(self, provider_artist_id: str, *, limit: int) -> ProviderArtistTracks: ...

    def acquire(
        self,
        candidate: DiscoveryCandidate,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int: ...


class ReleaseDiscoveryProvider(Protocol):
    """Separate bounded release scan; it cannot be substituted for manual search."""

    def release_tracks(self, provider_artist_id: str, *, offset: int) -> ProviderTrackPage: ...


__all__ = (
    "AcquisitionBoundaryAuthorizer",
    "DiscoveryProvider",
    "ReleaseDiscoveryProvider",
)
