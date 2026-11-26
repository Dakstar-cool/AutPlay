"""Durable public metadata pages, with a single provider read per cache key."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
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

    def visible_album_positions(
        self, owner: UUID, album: str, artist: str, year: int | None
    ) -> list[dict[str, Any]]:
        """Position evidence only from recordings this account can play."""
        with self.sessions() as session:
            rows = (
                session.execute(
                    text(f"""
                SELECT DISTINCT t.recording_id::text AS recording, t.title,
                    credit.display_name AS artist,
                    m.position AS disc, t.sequence_no AS sequence, t.number_text AS number,
                    coalesce(t.duration_ms, r.duration_ms) AS duration_ms,
                    NULL::text AS mb_recording
                FROM catalog.release rel
                JOIN catalog.artist_credit a ON a.artist_credit_id=rel.artist_credit_id
                JOIN catalog.medium m USING(release_id)
                JOIN catalog.release_track t USING(medium_id)
                JOIN catalog.artist_credit credit ON credit.artist_credit_id=t.artist_credit_id
                JOIN catalog.recording r ON r.recording_id=t.recording_id
                WHERE lower(rel.title)=lower(:album) AND lower(a.display_name)=lower(:artist)
                  AND (CAST(:year AS integer) IS NULL OR rel.release_date IS NULL
                       OR extract(year FROM rel.release_date)=:year)
                  AND rel.deleted_at IS NULL AND m.deleted_at IS NULL AND t.deleted_at IS NULL
                  AND (EXISTS (SELECT FROM ({OWNER_AUDIO}) own WHERE own.user_id=:owner
                            AND own.recording_id=r.recording_id)
                    OR EXISTS (SELECT FROM ({PUBLISHED_AUDIO}) pub
                               WHERE pub.recording_id=r.recording_id))
                UNION
                SELECT u.recording_id::text, coalesce(nullif(f->>'title',''),u.raw_title,r.title),
                    coalesce(nullif(f->>'artist',''),u.raw_artist,credit.display_name),
                    CASE WHEN f->>'disc_number' ~ '^[1-9][0-9]{{0,3}}$'
                         THEN (f->>'disc_number')::int END,
                    CASE WHEN f->>'track_number' ~ '^[1-9][0-9]{{0,3}}$'
                         THEN (f->>'track_number')::int END,
                    NULL, v.duration_ms, f->>'mb_recording_id'
                FROM library.user_track_ref u
                JOIN catalog.recording r USING(recording_id)
                JOIN catalog.artist_credit credit USING(artist_credit_id)
                LEFT JOIN library.track_metadata md USING(user_track_ref_id)
                CROSS JOIN LATERAL (SELECT md.document->'fields' AS f) meta
                JOIN ({OWNER_AUDIO}) own ON own.user_id=u.user_id
                  AND own.recording_id=u.recording_id
                JOIN vault.audio_variant v ON v.audio_variant_id=own.audio_variant_id
                WHERE u.user_id=:owner AND u.deleted_at IS NULL AND u.resolution_status='RESOLVED'
                  AND lower(coalesce(nullif(f->>'album',''),u.raw_album))=lower(:album)
                  AND lower(coalesce(nullif(f->>'album_artist',''),
                            (regexp_split_to_array(coalesce(nullif(f->>'artist',''), u.raw_artist,
                                    credit.display_name), ' +(feat[.]?|ft[.]?|featuring) +','i'))[1]
                          ))=lower(:artist)
                LIMIT 5001
            """),
                    {"owner": owner, "album": album, "artist": artist, "year": year},
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows] if len(rows) <= 5000 else []

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
                UNION ALL
                SELECT f->>'mb_recording_id', min(u.recording_id::text)::uuid, 2
                FROM library.track_metadata md
                JOIN library.user_track_ref u USING(user_track_ref_id)
                CROSS JOIN LATERAL (SELECT md.document->'fields' AS f) tags
                JOIN ({OWNER_AUDIO}) own ON own.recording_id=u.recording_id
                  AND own.user_id=u.user_id
                WHERE u.user_id=:owner AND u.deleted_at IS NULL AND u.resolution_status='RESOLVED'
                  AND f->>'mb_recording_id'=ANY(:mbids)
                  AND md.document#>>'{{provenance,mb_recording_id,source}}'='MUSICBRAINZ'
                  AND md.document#>>'{{provenance,mb_recording_id,source_id}}'=
                      'musicbrainz:release:' || (f->>'mb_release_id')
                  AND NOT EXISTS (SELECT FROM identity.external_reference x
                    JOIN identity.source_provider p USING(provider_id)
                    WHERE p.provider_key='musicbrainz' AND p.deleted_at IS NULL
                      AND x.external_entity_type='RECORDING' AND x.deleted_at IS NULL
                      AND x.external_id=f->>'mb_recording_id' AND x.recording_id<>u.recording_id)
                GROUP BY f->>'mb_recording_id' HAVING count(DISTINCT u.recording_id)=1
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
