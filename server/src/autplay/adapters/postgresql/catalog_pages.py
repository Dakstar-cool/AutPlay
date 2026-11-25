"""Durable public metadata pages, with a single provider read per cache key."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import CatalogBrowsePageRow
from autplay.adapters.postgresql.shared_catalog import (
    OWNER_AUDIO,
    OWNER_CATALOG_DOWNLOAD,
    PUBLISHED_AUDIO,
)
from autplay.ports.track_metadata import MetadataProviderError


class CatalogPageCache:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def available_recordings(self, owner: UUID, mbids: list[str]) -> dict[str, str]:
        if not mbids:
            return {}
        with self.sessions() as session:
            return dict(
                session.execute(
                    text(f"""
                SELECT DISTINCT ON (mbid) mbid, recording_id::text FROM (
                SELECT x.external_id AS mbid, x.recording_id, 0 AS priority
                FROM identity.external_reference x
                  JOIN identity.source_provider p USING(provider_id)
                WHERE p.provider_key='musicbrainz' AND x.external_entity_type='RECORDING'
                  AND x.deleted_at IS NULL AND p.deleted_at IS NULL AND x.external_id = ANY(:mbids)
                  AND (EXISTS (SELECT FROM ({OWNER_AUDIO}) own WHERE own.user_id=:owner
                    AND own.recording_id=x.recording_id)
                    OR EXISTS (SELECT FROM ({PUBLISHED_AUDIO}) pub
                      WHERE pub.recording_id=x.recording_id))
                UNION ALL
                SELECT d.mbid, d.recording_id, 1 FROM ({OWNER_CATALOG_DOWNLOAD}) d
                    WHERE d.user_id=:owner AND d.mbid = ANY(:mbids)
                      AND NOT EXISTS (
                        SELECT FROM identity.external_reference x
                        JOIN identity.source_provider p USING(provider_id)
                        WHERE p.provider_key='musicbrainz' AND p.deleted_at IS NULL
                          AND x.external_entity_type='RECORDING' AND x.deleted_at IS NULL
                          AND x.external_id=d.mbid AND x.recording_id <> d.recording_id)
                ) visible ORDER BY mbid, priority, recording_id
            """),
                    {"owner": owner, "mbids": mbids},
                )
                .tuples()
                .all()
            )

    def library_artist_id(self, owner: UUID, mbid: UUID) -> str | None:
        with self.sessions() as session:
            artist = session.scalar(
                text("""
                    SELECT x.artist_id FROM identity.external_reference x
                    JOIN identity.source_provider p USING (provider_id)
                    WHERE p.provider_key = 'musicbrainz' AND p.deleted_at IS NULL
                        AND x.deleted_at IS NULL AND x.market_scope = 'GLOBAL'
                        AND x.external_entity_type = 'ARTIST' AND x.external_id = :mbid
                        AND EXISTS (
                            SELECT 1 FROM catalog.artist_credit_name n
                            JOIN catalog.recording r ON r.artist_credit_id = n.artist_credit_id
                            JOIN library.user_track_ref u ON u.recording_id = r.recording_id
                            WHERE n.artist_id = x.artist_id AND r.deleted_at IS NULL
                                AND u.deleted_at IS NULL AND u.user_id = :owner)
                """),
                {"mbid": str(mbid), "owner": owner},
            )
            return str(artist) if artist is not None else None

    def artist_recordings(self, owner: UUID, artist: UUID) -> tuple[str | None, dict[str, str]]:
        with self.sessions() as session:
            rows = session.execute(
                text("""
                SELECT DISTINCT artist_ref.external_id, recording_ref.external_id, rec.recording_id
                FROM library.user_track_ref ref
                JOIN catalog.recording rec ON rec.recording_id = ref.recording_id
                  AND rec.deleted_at IS NULL
                JOIN catalog.artist_credit_name member
                  ON member.artist_credit_id = rec.artist_credit_id
                JOIN identity.source_provider p ON p.provider_key='musicbrainz'
                  AND p.deleted_at IS NULL
                JOIN identity.external_reference artist_ref ON artist_ref.provider_id=p.provider_id
                  AND artist_ref.artist_id=member.artist_id
                    AND artist_ref.external_entity_type='ARTIST'
                  AND artist_ref.deleted_at IS NULL
                LEFT JOIN identity.external_reference recording_ref
                  ON recording_ref.provider_id=p.provider_id
                  AND recording_ref.recording_id=rec.recording_id
                    AND recording_ref.external_entity_type='RECORDING'
                  AND recording_ref.deleted_at IS NULL
                WHERE ref.user_id=:owner AND ref.deleted_at IS NULL AND member.artist_id=:artist
            """),
                {"owner": owner, "artist": artist},
            ).all()
            artists = {row[0] for row in rows}
            if len(artists) != 1:
                return None, {}
            return artists.pop(), {row[1]: str(row[2]) for row in rows if row[1]}

    def read(self, key: str, refresh: Callable[[], dict[str, object]]) -> dict[str, object]:
        return self.update(key, lambda _: refresh())

    def saved(self, key: str) -> tuple[dict[str, object], datetime] | None:
        with self.sessions() as session:
            page = session.get(CatalogBrowsePageRow, key)
            return (page.document, page.observed_at) if page is not None else None

    def update(
        self,
        key: str,
        refresh: Callable[[dict[str, object] | None], dict[str, object]],
        *,
        pending: Callable[[dict[str, object]], bool] | None = None,
    ) -> dict[str, object]:
        with self.sessions.begin() as session:
            cutoff = datetime.now(UTC) - timedelta(hours=12)
            page = session.get(CatalogBrowsePageRow, key)
            if (
                page is not None
                and page.observed_at >= cutoff
                and not (pending and pending(page.document))
            ):
                return page.document
            # Serve stale data during refresh. Cold readers share the completed provider response.
            locked = session.scalar(
                select(func.pg_try_advisory_xact_lock(func.hashtextextended(key, 0)))
            )
            if not locked:
                if page is not None:
                    return page.document
                session.execute(text("SET LOCAL lock_timeout='8s'"))
                try:
                    session.scalar(
                        select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
                    )
                except OperationalError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "55P03":
                        raise MetadataProviderError(
                            "metadata_provider_busy", retryable=True
                        ) from exc
                    raise
            page = session.get(CatalogBrowsePageRow, key, populate_existing=True)
            if (
                page is not None
                and page.observed_at >= cutoff
                and not (pending and pending(page.document))
            ):
                return page.document
            try:
                document = refresh(page.document if page is not None else None)
            except MetadataProviderError as exc:
                if page is not None and exc.retryable:
                    return page.document
                raise
            session.merge(
                CatalogBrowsePageRow(page_key=key, document=document, observed_at=datetime.now(UTC))
            )
            return document
