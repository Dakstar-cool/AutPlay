"""One explicitly injected catalogue provider over an owned, globally gated transport."""

from typing import Any, Protocol
from uuid import UUID

from autplay.domain.music_discovery import MusicDiscoveryKind, MusicDiscoveryPage


class MusicDiscoveryHttp(Protocol):
    def json(self, url: str) -> dict[str, Any]: ...


class MusicDiscoveryProvider(Protocol):
    def search(
        self, query: str, kind: MusicDiscoveryKind, *, limit: int, offset: int
    ) -> MusicDiscoveryPage: ...

    def artist_tracks(self, artist_id: UUID, *, limit: int, offset: int) -> MusicDiscoveryPage: ...

    def release_tracks(
        self, release_id: UUID, *, limit: int, offset: int
    ) -> MusicDiscoveryPage: ...
