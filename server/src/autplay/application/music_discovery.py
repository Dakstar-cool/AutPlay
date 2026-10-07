"""Private catalogue reads over one explicitly owned provider context per operation."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID

from autplay.application.music_library import MusicError
from autplay.domain.auth import Principal
from autplay.domain.music_discovery import (
    MusicDiscoveryKind,
    MusicDiscoveryPage,
    MusicDiscoveryValidationError,
    discovery_page_bounds,
    discovery_query,
)
from autplay.ports.music_discovery import MusicDiscoveryProvider
from autplay.ports.track_metadata import MetadataProviderError

type MusicDiscoveryProviderFactory = Callable[
    [Principal], AbstractContextManager[MusicDiscoveryProvider]
]


class MusicDiscoveryService:
    def __init__(self, provider_factory: MusicDiscoveryProviderFactory) -> None:
        self.provider_factory = provider_factory

    def search(
        self,
        principal: Principal,
        query: str,
        kind: MusicDiscoveryKind,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, object]:
        try:
            query = discovery_query(query)
            kind = MusicDiscoveryKind(kind)
        except ValueError as error:
            raise MusicError(
                "music_discovery_query_invalid", "Check the catalogue query.", 422
            ) from error
        return self._read(
            principal,
            limit,
            offset,
            lambda provider: provider.search(query, kind, limit=limit, offset=offset),
        )

    def artist_tracks(
        self,
        principal: Principal,
        artist_id: UUID,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, object]:
        return self._read(
            principal,
            limit,
            offset,
            lambda provider: provider.artist_tracks(artist_id, limit=limit, offset=offset),
        )

    def release_tracks(
        self,
        principal: Principal,
        release_id: UUID,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, object]:
        return self._read(
            principal,
            limit,
            offset,
            lambda provider: provider.release_tracks(release_id, limit=limit, offset=offset),
        )

    def _read(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        action: Callable[[MusicDiscoveryProvider], MusicDiscoveryPage],
    ) -> dict[str, object]:
        try:
            discovery_page_bounds(limit, offset)
        except MusicDiscoveryValidationError as error:
            raise MusicError(str(error), "Check the catalogue page.", 422) from error
        try:
            with self.provider_factory(principal) as provider:
                return action(provider).view()
        except MetadataProviderError as error:
            if error.code in {"metadata_release_missing", "metadata_artist_missing"}:
                raise MusicError(
                    "music_discovery_entity_not_found", "The catalogue entity is unavailable.", 404
                ) from error
            code = (
                "music_discovery_busy"
                if error.code == "metadata_provider_busy"
                else "music_discovery_unavailable"
            )
            raise MusicError(
                code, "Catalogue search is temporarily unavailable.", 503, retryable=error.retryable
            ) from error
