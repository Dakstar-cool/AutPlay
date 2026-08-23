"""Ports for provider discovery and independently authorized byte acquisition."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from autplay.domain.discovery import DiscoveryCandidate, ProviderArtist, ProviderArtistTracks


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


__all__ = ("DiscoveryProvider",)
