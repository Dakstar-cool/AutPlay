"""Private catalogue reads over one explicitly owned provider context per operation."""

import base64
import json
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from autplay.adapters.discogs_tracklist import (
    annotate_discogs_positions,
    discogs_edition,
    edition_choices,
    linked_discogs,
)
from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
from autplay.adapters.public_track_metadata import is_cover_url
from autplay.application.artist_overview import artist_overview_view, build_artist_overview
from autplay.application.music_library import MusicError
from autplay.domain.auth import Principal
from autplay.domain.catalog_artwork import image_url
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
        discogs_search: bool = False,
    ) -> None:
        self.provider_factory = provider_factory
        self.page_cache = page_cache
        self.read_public = read_public
        self.discogs_search = discogs_search

    def _image(self, principal: Principal, url: str) -> dict[str, object]:
        assert self.read_public is not None
        is_portrait = bool(image_url(url, "artist"))
        for _ in range(5):
            if not is_cover_url(url) or bool(image_url(url, "artist")) != is_portrait:
                break
            data = self.read_public(principal, url)
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

    def _public_document(self, principal: Principal, url: str) -> dict[str, Any]:
        assert self.read_public is not None
        data = self.read_public(principal, url)
        if data is None:
            return {}
        try:
            value = json.loads(data)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        raise MetadataProviderError("metadata_response_invalid", retryable=False)

    def _cached_image(self, key: str, refresh: Callable[[], dict[str, object]]) -> bytes:
        if self.read_public is None or self.page_cache is None:
            raise MusicError("catalog_cover_missing", "Image unavailable.", 404)
        try:
            saved = self.page_cache.read(key, refresh).get("image")
        except MetadataProviderError as exc:
            raise MusicError(
                "catalog_cover_unavailable",
                "Image temporarily unavailable.",
                503,
                retryable=exc.retryable,
            ) from exc
        if not isinstance(saved, str):
            raise MusicError("catalog_cover_missing", "Image unavailable.", 404)
        return base64.b64decode(saved, validate=True)

    def release_cover(self, principal: Principal, release: UUID) -> bytes:
        def refresh() -> dict[str, object]:
            if image := self._image(
                principal, f"https://coverartarchive.org/release/{release}/front-500"
            ):
                return image
            doc = self._public_document(
                principal,
                f"https://musicbrainz.org/ws/2/release/{release}?inc=release-groups&fmt=json",
            )
            group = doc.get("release-group")
            if doc.get("id") != str(release) or not isinstance(group, dict):
                return {}
            try:
                identity = UUID(str(group.get("id")))
            except ValueError:
                return {}
            return self._image(
                principal, f"https://coverartarchive.org/release-group/{identity}/front-500"
            )

        return self._cached_image(f"coverartarchive:front-500:{release}", refresh)

    def artist_portrait(self, principal: Principal, artist: UUID) -> bytes:
        def refresh() -> dict[str, object]:
            doc = self._public_document(
                principal, f"https://musicbrainz.org/ws/2/artist/{artist}?inc=url-rels&fmt=json"
            )
            if doc.get("id") != str(artist):
                return {}
            ids = set()
            relations = doc.get("relations", [])
            if not isinstance(relations, list):
                raise MetadataProviderError("metadata_response_invalid", retryable=False)
            for relation in relations:
                if (
                    not isinstance(relation, dict)
                    or not isinstance(relation.get("url"), dict)
                    or relation.get("ended")
                ):
                    continue
                try:
                    link = urlsplit(str(relation["url"].get("resource", "")))
                except ValueError:
                    continue
                match = re.fullmatch(r"/(?:[a-z]{2}/)?artist/([1-9][0-9]{0,16})/?", link.path)
                if (
                    link.scheme in {"https", "http"}
                    and link.netloc in {"deezer.com", "www.deezer.com"}
                    and match
                ):
                    ids.add(match[1])
            if len(ids) != 1:
                return {}
            identity = ids.pop()
            entry = self._public_document(principal, f"https://api.deezer.com/artist/{identity}")
            photo = image_url(entry.get("picture_big"), "artist")
            if str(entry.get("id")) != identity or not photo:
                return {}
            return self._image(principal, photo)

        return self._cached_image(f"musicbrainz:artist-portrait:{artist}", refresh)

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

    def artist_overview(
        self,
        principal: Principal,
        artist: UUID,
        schedule: Callable[[Callable[[], None]], None],
        known_baseline: UUID | None = None,
    ) -> dict[str, object]:
        cache = self.page_cache
        key = f"musicbrainz:artist-overview-v1:{artist}"

        def refresh(previous: dict[str, object] | None) -> dict[str, object]:
            now = datetime.now(UTC)
            try:
                with self.provider_factory(principal) as provider:
                    updated = build_artist_overview(provider, artist, previous, now.date())
                updated.pop("retry_at", None)
                return updated
            except MetadataProviderError:
                if previous is None:
                    raise
                return {**previous, "retry_at": (now + timedelta(minutes=5)).isoformat()}

        def pending(doc: dict[str, object]) -> bool:
            if isinstance(retry_at := doc.get("retry_at"), str):
                return datetime.now(UTC) >= datetime.fromisoformat(retry_at)
            return doc.get("delta_offset") is not None

        def update_saved() -> None:
            assert cache is not None
            # Commit each provider page separately. Another worker can continue from that cursor.
            prior_cursor: object = -1
            for _ in range(4):
                try:
                    updated = cache.update(key, refresh, pending=pending)
                except MetadataProviderError:
                    return
                cursor = updated.get("delta_offset")
                if cursor is None or cursor == prior_cursor or "retry_at" in updated:
                    return
                prior_cursor = cursor

        try:
            saved = cache.saved(key) if cache is not None else None
            if saved is None:
                data = cache.update(key, refresh) if cache is not None else refresh(None)
                due = pending(data)
            else:
                data, observed = saved
                due = pending(data) or datetime.now(UTC) - observed >= timedelta(hours=12)
            if due and cache is not None:
                schedule(update_saved)
            return {
                **artist_overview_view(data, known_baseline),
                "artist_mbid": str(artist),
                "library_artist_id": (
                    cache.library_artist_id(principal.user_id, artist)
                    if cache is not None
                    else None
                ),
                "refreshing": due and cache is not None,
            }
        except MetadataProviderError as exc:
            raise MusicError(
                "music_discovery_unavailable",
                "Artist overview is unavailable.",
                503,
                retryable=exc.retryable,
            ) from exc

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
            key=f"artist_tracks_v2:{artist_id}",
        )

    def official_recordings(self, principal: Principal, recordings: list[UUID]) -> set[str]:
        """Check one discography page by recording ID. Missing release evidence is neutral."""
        if not recordings or self.read_public is None:
            return set()
        discovery_page_bounds(len(recordings), 0)
        ids = {str(recording) for recording in recordings}
        query = "status:official AND (" + " OR ".join(f'rid:"{i}"' for i in sorted(ids)) + ")"
        params = urlencode({"query": query, "limit": len(ids), "fmt": "json"})
        url = "https://musicbrainz.org/ws/2/recording?" + params
        try:
            page = self._public_document(principal, url)
        except MetadataProviderError as exc:
            if exc.code != "metadata_provider_busy":
                return set()
            # Browsing a cold page just used the same rate-limited gate.
            time.sleep(min(2, exc.retry_after_seconds))
            try:
                page = self._public_document(principal, url)
            except MetadataProviderError:
                return set()
        rows = page.get("recordings")
        if not isinstance(rows, list) or len(rows) > len(ids):
            return set()
        return {
            r["id"]
            for r in rows
            if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"] in ids
        }

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

        try:
            public = self._mb_edition(principal, release_id)
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

    def _mb_edition(self, principal: Principal, release_id: UUID) -> dict[str, Any]:
        def edition() -> dict[str, object]:
            with self.provider_factory(principal) as provider:
                page = provider.release_tracks(release_id, limit=50, offset=0)
                if page.edition_items is None:
                    raise MetadataProviderError("metadata_response_invalid", retryable=False)
                return {
                    **page.view(),
                    "items": [item.view() for item in page.edition_items],
                    "edition_title": page.edition_title,
                    "edition_artist": page.edition_artist,
                }

        if self.page_cache is None:
            return edition()
        return self.page_cache.read(f"musicbrainz:album-edition:{release_id}", edition)

    def _album_mb_rows(self, principal: Principal, mbid: UUID) -> list[dict[str, Any]]:
        edition = self._mb_edition(principal, mbid)
        rows = edition["items"]
        cache = self.page_cache
        playable = (
            cache.available_recordings(principal.user_id, [r["recording_id"] for r in rows])
            if cache
            else {}
        )
        tracks = [
            {
                "position": str(r["track_number"] or ""),
                "disc": r["disc_number"],
                "title": r["title"],
                "artist": r["artist"],
                "duration_ms": r["duration_ms"],
                "mbid": r["recording_id"],
                "recording": playable.get(r["recording_id"]),
                "in_vault": r["recording_id"] in playable if cache else None,
            }
            for r in rows
        ]
        unresolved = [t for t in tracks if not t["recording"] and t["artist"]]
        if cache and unresolved and edition.get("edition_title") and edition.get("edition_artist"):
            evidence = cache.visible_album_positions(
                principal.user_id, edition["edition_title"], edition["edition_artist"], None
            )
            annotate_discogs_positions(unresolved, evidence)
        for track in tracks:
            track.pop("mbid")
        return tracks

    def album_tracklist(
        self,
        principal: Principal,
        mbid: UUID | None,
        discogs_id: int | None,
        title: str,
        artist: str,
        year: int | None,
    ) -> dict[str, Any]:
        def read(url: str) -> dict[str, Any]:
            if self.read_public is None:
                raise MetadataProviderError("metadata_provider_unavailable")
            return self._public_document(principal, url)

        primary_error: MetadataProviderError | None = None
        try:
            if not discogs_id:
                try:
                    if mbid is None and title and artist:
                        options, complete = edition_choices(
                            read, "musicbrainz", title, artist, year
                        )
                        if len(options) == 1 and complete:
                            mbid = UUID(options[0]["id"])
                        elif options:
                            return {"choices": options}
                    if mbid:
                        rows = self._album_mb_rows(principal, mbid)
                        if rows:
                            return {
                                "source": "musicbrainz",
                                "id": str(mbid),
                                "tracks": rows,
                            }
                except MetadataProviderError as exc:
                    primary_error = exc
                except ValueError, KeyError, TypeError, AttributeError:
                    primary_error = MetadataProviderError(
                        "metadata_response_invalid", retryable=False
                    )
                if mbid:
                    try:
                        discogs_id, mb_title, mb_artist = linked_discogs(read, mbid)
                        title, artist = mb_title or title, mb_artist or artist
                    except MetadataProviderError as exc:
                        primary_error = primary_error or exc
                    except ValueError, KeyError, TypeError, AttributeError:
                        primary_error = primary_error or MetadataProviderError(
                            "metadata_response_invalid", retryable=False
                        )
                if not discogs_id and self.discogs_search and title and artist:
                    options, complete = edition_choices(read, "discogs", title, artist, year)
                    if len(options) == 1 and complete:
                        discogs_id = int(options[0]["id"])
                    elif options:
                        return {"choices": options}
            if not discogs_id:
                if primary_error and primary_error.retryable:
                    raise primary_error
                return {"choices": []}
            edition = discogs_edition(read, discogs_id)
            cache = self.page_cache
            evidence = (
                cache.visible_album_positions(
                    principal.user_id, edition["title"], edition["artist"], edition["year"]
                )
                if cache
                else []
            )
            annotate_discogs_positions(edition["tracks"], evidence)
            return edition
        except MetadataProviderError as exc:
            if exc.code == "metadata_release_missing":
                return {"choices": []}
            raise MusicError(
                "music_discovery_unavailable",
                "Tracklist temporarily unavailable.",
                503,
                retryable=exc.retryable,
            ) from exc
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MusicError(
                "music_discovery_unavailable", "Invalid catalogue tracklist.", 503
            ) from exc

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
