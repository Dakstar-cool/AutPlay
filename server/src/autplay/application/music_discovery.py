"""Private catalogue reads over one explicitly owned provider context per operation."""

import base64
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID

from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
from autplay.adapters.public_track_metadata import is_cover_url
from autplay.application.music_library import MusicError
from autplay.domain.auth import Principal
from autplay.domain.music_discovery import (
    MAX_BROWSE_OFFSET,
    MAX_DISCOVERY_OFFSET,
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
    def __init__(
        self,
        provider_factory: MusicDiscoveryProviderFactory,
        page_cache: CatalogPageCache | None = None,
        read_public: Callable[[Principal, str], bytes | None] | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.page_cache = page_cache
        self.read_public = read_public

    def release_cover(self, principal: Principal, release: UUID) -> bytes:
        reader, cache = self.read_public, self.page_cache
        if reader is None or cache is None:
            raise MusicError("catalog_cover_missing", "Cover unavailable.", 404)

        def refresh() -> dict[str, object]:
            url = f"https://coverartarchive.org/release/{release}/front-250"
            for _ in range(5):
                if not is_cover_url(url):
                    break
                data = reader(principal, url)
                if data is None:
                    return {}
                if data.startswith((bytes.fromhex("ffd8ff"), bytes.fromhex("89504e470d0a1a0a"))):
                    return {"image": base64.b64encode(data).decode("ascii")}
                try:
                    url = json.loads(data)["redirect"]
                    if not isinstance(url, str):
                        break
                except KeyError, TypeError, ValueError:
                    break
            raise MetadataProviderError("metadata_artwork_invalid", retryable=False)

        try:
            saved = cache.read(f"coverartarchive:front-250:{release}", refresh).get("image")
        except MetadataProviderError as exc:
            raise MusicError(
                "catalog_cover_unavailable",
                "Cover temporarily unavailable.",
                503,
                retryable=exc.retryable,
            ) from exc
        if not isinstance(saved, str):
            raise MusicError("catalog_cover_missing", "Cover unavailable.", 404)
        return base64.b64decode(saved, validate=True)

    def artist_ranking(self, principal: Principal, artist: UUID) -> dict[str, object]:
        empty: dict[str, object] = {"source": "ListenBrainz", "counts": {}}
        cache, reader = self.page_cache, self.read_public
        if cache is None:
            return empty
        mbid, recordings = cache.artist_recordings(principal.user_id, artist)
        if not mbid:
            return empty

        identity = {**empty, "artist_mbid": mbid, "library_recordings": recordings}
        if reader is None:
            return {**identity, "ranking_available": False}

        def refresh() -> dict[str, object]:
            data = reader(
                principal,
                f"https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/{UUID(mbid)}",
            )
            if data is None:
                raise MetadataProviderError("metadata_ranking_unavailable", retryable=True)
            try:
                rows = json.loads(data)
                if not isinstance(rows, list):
                    raise ValueError()
                return {
                    str(UUID(row["recording_mbid"])): row["total_listen_count"]
                    for row in rows
                    if isinstance(row, dict)
                    and mbid in (row.get("artist_mbids") or [])
                    and type(row.get("total_listen_count")) is int
                    and row["total_listen_count"] >= 0
                }
            except (ValueError, KeyError, TypeError) as exc:
                raise MetadataProviderError("metadata_response_invalid", retryable=False) from exc

        try:
            counts = cache.read(f"listenbrainz:top-recordings:{mbid}", refresh)
        except MetadataProviderError:
            return {**identity, "ranking_available": False}
        return {
            **identity,
            "recording_counts": counts,
            "counts": {
                recordings[key]: count for key, count in counts.items() if key in recordings
            },
        }

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

    def artist_releases(
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
            lambda provider: provider.artist_releases(artist_id, limit=limit, offset=offset),
            key=f"artist_releases:{artist_id}",
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
            key=f"artist_tracks:{artist_id}",
        )

    def release_tracks(
        self,
        principal: Principal,
        release_id: UUID,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, object]:
        cache = self.page_cache
        if cache is None:
            return self._read(
                principal,
                limit,
                offset,
                lambda provider: provider.release_tracks(release_id, limit=limit, offset=offset),
                key=f"release_tracks:{release_id}",
            )
        try:
            discovery_page_bounds(limit, offset, MAX_BROWSE_OFFSET)
        except MusicDiscoveryValidationError as exc:
            raise MusicError(str(exc), "Check the catalogue page.", 422) from exc

        def edition() -> dict[str, object]:
            with self.provider_factory(principal) as provider:
                page = provider.release_tracks(release_id, limit=50, offset=0)
                if page.edition_items is None:
                    raise MetadataProviderError("metadata_response_invalid", retryable=False)
                return {**page.view(), "items": [item.view() for item in page.edition_items]}

        try:
            public = cache.read(f"musicbrainz:edition:{release_id}", edition)
        except MetadataProviderError as exc:
            if exc.code == "metadata_release_missing":
                raise MusicError(
                    "music_discovery_entity_not_found", "The edition is unavailable.", 404
                ) from exc
            raise MusicError(
                "music_discovery_unavailable",
                "The catalogue is temporarily unavailable.",
                503,
                retryable=exc.retryable,
            ) from exc
        rows = public["items"]
        if not isinstance(rows, list):
            raise MusicError("music_discovery_unavailable", "The edition is unavailable.", 503)
        playable = cache.available_recordings(
            principal.user_id, [row["recording_id"] for row in rows]
        )
        items = [
            {
                **row,
                "vault_available": row["recording_id"] in playable,
                "playable_recording_id": playable.get(row["recording_id"]),
            }
            for row in rows[offset : offset + limit]
        ]
        return {
            **public,
            "items": items,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "next_offset": offset + limit if offset + limit < len(rows) else None,
            "vault_count": sum(row["recording_id"] in playable for row in rows),
        }

    def _read(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        action: Callable[[MusicDiscoveryProvider], MusicDiscoveryPage],
        key: str | None = None,
    ) -> dict[str, object]:
        try:
            discovery_page_bounds(limit, offset, MAX_BROWSE_OFFSET if key else MAX_DISCOVERY_OFFSET)
        except MusicDiscoveryValidationError as error:
            raise MusicError(str(error), "Check the catalogue page.", 422) from error

        def refresh() -> dict[str, object]:
            with self.provider_factory(principal) as provider:
                return action(provider).view()

        try:
            if self.page_cache is not None and key is not None:
                return self.page_cache.read(f"musicbrainz:{key}:{limit}:{offset}", refresh)
            return refresh()
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
