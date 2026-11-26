"""Owner-scoped catalog discovery and first-Wave artist evidence."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.shared_catalog import PUBLISHED_AUDIO, ensure_catalog_refs
from autplay.adapters.postgresql.stage_credits import CATALOG_MEMBERS
from autplay.application.sync import _acquire_sync_owner_publish_lock

# Read only confirmed favorites from sources enabled for taste. Playlist rows,
# session previews and Spotify metadata must never enter the taste profile.
_ELIGIBLE_IMPORT = """(c.provider='yandex' OR
    (c.provider='soundcloud' AND c.snapshot->>'catalog_selection'='true'))"""
_FAVORITES = f"""
    SELECT DISTINCT ref.user_track_ref_id, ref.raw_artist, ref.raw_title,
           coalesce(ref.recording_id, link.source_recording_id) AS recording_id
    FROM importing.collection_import c JOIN importing.import_entry e USING(import_job_id)
    JOIN library.user_track_ref ref ON ref.user_track_ref_id=e.user_track_ref_id
    JOIN library.user_track_preference pref ON pref.user_track_ref_id=ref.user_track_ref_id
    JOIN library.library_entry le ON le.user_track_ref_id=ref.user_track_ref_id
    LEFT JOIN playlist.imported_track_reference link ON link.user_track_ref_id=ref.user_track_ref_id
    WHERE c.owner=:owner AND {_ELIGIBLE_IMPORT} AND c.action='FAVORITES' AND c.state='SAVED'
      AND ref.user_id=:owner AND ref.deleted_at IS NULL AND pref.preference='LIKED'
      AND le.user_id=:owner AND le.removed_at IS NULL
      AND (NOT pref.excluded_from_taste OR (pref.row_version=1 AND EXISTS (
        SELECT FROM sync.sync_event ev WHERE ev.user_id=c.owner
          AND ev.aggregate_id=ref.user_track_ref_id
          AND ev.aggregate_type='USER_TRACK_PREFERENCE'
          AND ev.event_type='EXTERNAL_COLLECTION_PROJECTED'
          AND ev.server_row_version=1)))
"""

_CATALOG = f"""
WITH favorites AS ({_FAVORITES}),
visible AS (
    SELECT recording_id FROM ({PUBLISHED_AUDIO}) published
    UNION SELECT recording_id FROM favorites WHERE recording_id IS NOT NULL
    UNION SELECT ref.recording_id FROM library.library_entry le
    JOIN library.user_track_ref ref USING(user_track_ref_id)
    WHERE le.user_id=:owner AND ref.user_id=:owner AND le.removed_at IS NULL
      AND ref.deleted_at IS NULL AND ref.recording_id IS NOT NULL
      AND NOT EXISTS (SELECT FROM importing.import_entry e
          JOIN importing.collection_import c USING(import_job_id)
          WHERE c.owner=:owner AND e.user_track_ref_id=ref.user_track_ref_id
            AND NOT {_ELIGIBLE_IMPORT})
), credits AS (
    SELECT r.recording_id, r.artist_credit_id,
        coalesce(a.artist_id,c.artist_credit_id) AS artist_id,
           coalesce(a.name,c.display_name) AS name, r.title
    FROM visible JOIN catalog.recording r USING(recording_id)
    JOIN catalog.artist_credit c USING(artist_credit_id)
    LEFT JOIN catalog.artist_credit_name n USING(artist_credit_id)
    LEFT JOIN catalog.artist a ON a.artist_id=n.artist_id
    WHERE r.deleted_at IS NULL AND c.deleted_at IS NULL
      AND (n.artist_id IS NULL OR (a.deleted_at IS NULL
          AND a.identity_status IN ('ACTIVE','PROVISIONAL')))
), names AS (
    SELECT lower(trim(name)) AS name, (array_agg(DISTINCT artist_id))[1] AS artist_id
    FROM credits GROUP BY lower(trim(name)) HAVING count(DISTINCT artist_id)=1
), unresolved AS (
    SELECT md5(CAST(:owner AS text)||'/favorite-artist/'||lower(trim(f.raw_artist)))::uuid
        AS artist_id,
           f.raw_artist AS name, f.raw_title AS title, f.user_track_ref_id
    FROM favorites f WHERE f.recording_id IS NULL AND trim(f.raw_artist)<>''
    AND NOT EXISTS (SELECT FROM credits c WHERE lower(trim(c.name))=lower(trim(f.raw_artist)))
), artists AS (
    SELECT artist_id,name,title,recording_id FROM credits UNION ALL
    SELECT artist_id,name,title,NULL::uuid FROM unresolved
), imported AS (
    SELECT c.artist_id, f.user_track_ref_id, f.raw_title FROM favorites f
    JOIN credits c ON c.recording_id=f.recording_id
    UNION SELECT n.artist_id, f.user_track_ref_id, f.raw_title FROM favorites f
    JOIN names n ON n.name=lower(trim(f.raw_artist)) WHERE f.recording_id IS NULL
    UNION SELECT artist_id,user_track_ref_id,title FROM unresolved
), affinity AS (
    SELECT artist_id, count(DISTINCT user_track_ref_id) AS hits, min(raw_title) AS example
    FROM imported GROUP BY artist_id
), seeds AS (
    SELECT artist_id, 4 AS weight FROM affinity
    UNION ALL SELECT unnest(onboarding_artists), 5 FROM account.user_account WHERE user_id=:owner
), weighted AS (
    SELECT artist_id, max(weight) AS weight FROM (
        SELECT * FROM seeds
        UNION ALL SELECT n.artist_id,5 FROM names n JOIN seeds s ON s.weight=5
          AND s.artist_id=md5(CAST(:owner AS text)||'/favorite-artist/'||n.name)::uuid
    ) mapped GROUP BY artist_id
)
"""

# Collaborations, shared releases and independently generated recording tags.
_RELATED = """
, anchor AS (SELECT recording_id FROM credits WHERE artist_id=:parent),
rel AS (SELECT DISTINCT m.release_id FROM catalog.release_track t
    JOIN catalog.medium m USING(medium_id)
        JOIN anchor USING(recording_id) WHERE t.deleted_at IS NULL AND m.deleted_at IS NULL),
labels AS (
    SELECT s.recording_id,lower(tag->>'key') AS name FROM ml.recording_tag_set s
    CROSS JOIN LATERAL jsonb_array_elements(s.tag_document) tag
    WHERE s.retired_at IS NULL AND (tag->>'score')::float>=0.5
    UNION SELECT coalesce(ref.recording_id, link.source_recording_id), lower(genre)
    FROM library.user_track_ref ref JOIN library.track_metadata m USING(user_track_ref_id)
    LEFT JOIN playlist.imported_track_reference link USING(user_track_ref_id)
    CROSS JOIN LATERAL jsonb_array_elements_text(CASE
        WHEN jsonb_typeof(m.document->'fields'->'genres')='array'
            THEN m.document->'fields'->'genres'
        ELSE '[]'::jsonb END) genre
    WHERE ref.user_id=:owner AND ref.deleted_at IS NULL
), tags AS (SELECT DISTINCT name FROM labels JOIN anchor USING(recording_id)),
related AS (
    SELECT c.artist_id, 4 AS strength FROM credits c JOIN anchor USING(recording_id)
    UNION ALL SELECT c.artist_id, 2 FROM credits c JOIN catalog.release_track t USING(recording_id)
        JOIN catalog.medium m USING(medium_id) JOIN rel USING(release_id)
        WHERE t.deleted_at IS NULL AND m.deleted_at IS NULL
    UNION ALL SELECT c.artist_id, 1 FROM credits c JOIN labels USING(recording_id) JOIN tags
        ON tags.name=labels.name
)
"""


class ArtistOnboarding:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def pending(self, owner: UUID) -> bool:
        with self.sessions() as session:
            return bool(
                session.scalar(
                    text(
                        "SELECT onboarding_artists IS NULL FROM account.user_account "
                        "WHERE user_id=:owner"
                    ),
                    {"owner": owner},
                )
            )

    def browse(
        self, owner: UUID, parent: UUID | None, query: str, seen: list[UUID]
    ) -> list[dict[str, str]]:
        related = parent is not None
        sql = (
            _CATALOG
            + (_RELATED if related else "")
            + """
        SELECT c.artist_id AS id, min(c.name) AS name,
            coalesce(min(f.example),min(c.title)) AS track
        FROM artists c LEFT JOIN affinity f USING(artist_id)
        """
            + (
                "JOIN (SELECT artist_id,sum(strength) AS score FROM related "
                "GROUP BY artist_id) sim USING(artist_id)"
                if related
                else ""
            )
            + """
        WHERE c.artist_id <> ALL(CAST(:seen AS uuid[])) AND strpos(lower(c.name),lower(:query))>0
        GROUP BY c.artist_id ORDER BY """
            + ("max(sim.score) DESC," if related else "")
            + """
        max(f.hits) DESC NULLS LAST, count(DISTINCT c.recording_id) DESC, c.artist_id LIMIT :limit
        """
        )
        with self.sessions() as session:
            rows = session.execute(
                text(sql),
                {
                    "owner": owner,
                    "parent": parent,
                    "query": query,
                    "seen": [*seen, *([parent] if parent else [])],
                    "limit": 2 if related else 18,
                },
            )
            page = list(rows)
            if not related and not query:
                page = page[: max(2, min(12, (len(page) + 1) // 2))]
            return [{"id": str(r.id), "name": r.name, "track": r.track} for r in page]

    def finish(self, owner: UUID, artists: list[UUID]) -> None:
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, owner)
            current = session.scalar(
                text(
                    "SELECT onboarding_artists FROM account.user_account "
                    "WHERE user_id=:owner FOR UPDATE"
                ),
                {"owner": owner},
            )
            if current is not None:
                return  # Two devices may finish the same welcome flow.
            if artists:
                visible = set(
                    session.scalars(
                        text(
                            _CATALOG
                            + "SELECT DISTINCT artist_id FROM artists WHERE artist_id=ANY(:ids)"
                        ),
                        {"owner": owner, "ids": artists},
                    )
                )
                if visible != set(artists):
                    raise ValueError("artist_unavailable")
            session.execute(
                text(
                    "UPDATE account.user_account SET onboarding_artists=CAST(:ids AS uuid[]) "
                    "WHERE user_id=:owner"
                ),
                {"ids": artists, "owner": owner},
            )
            picks = (
                session.execute(
                    text(
                        _CATALOG
                        + f"""
                , matches AS (
                    SELECT c.recording_id,w.artist_id,w.weight, row_number() OVER
                      (PARTITION BY w.artist_id ORDER BY c.recording_id) AS pos
                    FROM credits c JOIN weighted w USING(artist_id)
                    JOIN ({PUBLISHED_AUDIO}) published USING(recording_id)
                    JOIN catalog.recording r USING(recording_id)
                    WHERE r.identity_status='ACTIVE'
                ), sample AS (
                    SELECT recording_id,min(pos) AS pos,max(weight) AS weight FROM matches
                    WHERE pos<=6 GROUP BY recording_id
                        ORDER BY min(pos),max(weight) DESC,recording_id LIMIT 144
                )
                SELECT pub.*,r.title,ac.display_name AS artist,v.duration_ms,
                    {CATALOG_MEMBERS} AS members
                FROM sample JOIN ({PUBLISHED_AUDIO}) pub USING(recording_id)
                JOIN catalog.recording r USING(recording_id)
                    JOIN catalog.artist_credit ac USING(artist_credit_id)
                JOIN vault.audio_variant v USING(audio_variant_id)
                """
                    ),
                    {"owner": owner},
                )
                .mappings()
                .all()
            )
            if picks:
                ensure_catalog_refs(session, owner, picks)


def starter_evidence(session: Session, owner: UUID) -> tuple[dict[str, int], Sequence[RowMapping]]:
    enabled = session.scalar(
        text(f"""SELECT EXISTS(SELECT FROM account.user_account WHERE user_id=:owner
        AND cardinality(onboarding_artists)>0) OR EXISTS(SELECT FROM importing.collection_import c
        WHERE c.owner=:owner AND c.action='FAVORITES' AND c.state='SAVED'
            AND {_ELIGIBLE_IMPORT})"""),
        {"owner": owner},
    )
    if not enabled:
        return {}, ()
    weights = dict(
        session.execute(
            text(
                _CATALOG
                + """
        SELECT artist_credit_id::text,max(weight) FROM credits JOIN weighted USING(artist_id)
        GROUP BY artist_credit_id"""
            ),
            {"owner": owner},
        )
        .tuples()
        .all()
    )
    if not weights:
        return {}, ()
    # Only exact, currently published variants with an existing handle for this owner.
    public = (
        session.execute(
            text(f"""
        SELECT DISTINCT ON (r.recording_id) r.recording_id,u.user_track_ref_id,
            r.artist_credit_id::text AS artist_key,
            NULL AS release_key, ARRAY[r.recording_kind,r.normalized_title] AS metadata_tokens,
            'VAULT' AS availability,true AS authorized,r.identity_status,
                'NEUTRAL' AS preference,false AS excluded,
            h.play_count,h.organic_play_count,h.recommended_play_count,h.last_played_at_ms,
            floor(extract(epoch FROM u.created_at)*1000)::bigint AS added_at_ms,
                NULL AS release_date_ordinal
        FROM ({PUBLISHED_AUDIO}) p JOIN catalog.recording r USING(recording_id)
        JOIN library.user_track_ref_external_reference x
            ON x.external_reference_id=p.external_reference_id
        JOIN library.user_track_ref u USING(user_track_ref_id)
        LEFT JOIN LATERAL (
            SELECT count(*) AS play_count,
                count(*) FILTER (WHERE event_origin<>'RECOMMENDED') AS organic_play_count,
                count(*) FILTER (WHERE event_origin='RECOMMENDED') AS recommended_play_count,
                floor(extract(epoch FROM max(started_at))*1000)::bigint AS last_played_at_ms
            FROM library.listening_event WHERE user_id=:owner
              AND recording_id=r.recording_id AND NOT excluded_from_taste
        ) h ON true
        WHERE u.user_id=:owner AND u.deleted_at IS NULL AND x.relation_role='PRIMARY_SOURCE'
          AND (u.recording_id IS NULL OR u.recording_id=r.recording_id)
              AND r.artist_credit_id=ANY(CAST(:credits AS uuid[]))
          AND NOT EXISTS (SELECT FROM library.user_track_ref other
            LEFT JOIN playlist.imported_track_reference imported
                ON imported.user_track_ref_id=other.user_track_ref_id
            JOIN library.user_track_preference pref
                ON pref.user_track_ref_id=other.user_track_ref_id
            WHERE other.user_id=:owner AND other.deleted_at IS NULL AND
            (other.user_track_ref_id=u.user_track_ref_id
                OR coalesce(other.recording_id,imported.source_recording_id)=r.recording_id)
            AND (pref.preference='DISLIKED' OR pref.excluded_from_taste))
          AND NOT coalesce((
            SELECT sig.interaction='EXCLUDE'
                OR (sig.interaction IN ('DISLIKE','SKIP') AND sig.weight<0)
            FROM library.portable_taste_signal sig
            JOIN library.user_track_ref ref ON ref.user_track_ref_id=sig.user_track_ref_id
            WHERE sig.user_id=:owner AND ref.user_id=:owner AND ref.deleted_at IS NULL
              AND (ref.recording_id=r.recording_id OR ref.user_track_ref_id=u.user_track_ref_id)
              AND sig.occurred_at_ms<=floor(extract(epoch FROM now())*1000)
                  AND sig.updated_at<=now()
            ORDER BY sig.occurred_at_ms DESC,sig.record_id LIMIT 1),false)
        ORDER BY r.recording_id,u.user_track_ref_id LIMIT 144
        """),
            {"owner": owner, "credits": list(weights)},
        )
        .mappings()
        .all()
    )
    return weights, public
