"""Search the owner's library and the operator-published streaming catalog."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.shared_catalog import PUBLISHED_AUDIO, ensure_catalog_refs
from autplay.domain.auth import Principal
from autplay.domain.library import LibraryCommandError
from autplay.domain.music_search import MusicSearchKind, validate_music_search

_PUBLISHED_DETAILS = f"""
    SELECT p.*, r.title, r.version_text, a.display_name AS artist, v.duration_ms
    FROM ({PUBLISHED_AUDIO}) p
    JOIN catalog.recording r USING (recording_id)
    JOIN catalog.artist_credit a USING (artist_credit_id)
    JOIN vault.audio_variant v USING (audio_variant_id)
"""

_ALBUM_MATCH = """
    EXISTS (SELECT FROM catalog.release_track rt
        JOIN catalog.medium m USING (medium_id)
        JOIN catalog.release rel USING (release_id)
        WHERE rt.recording_id=p.recording_id AND rt.deleted_at IS NULL
          AND m.deleted_at IS NULL AND rel.deleted_at IS NULL AND rel.title ILIKE :needle)
"""


def _read_cursor(cursor: str | None) -> tuple[int, int, str, UUID]:
    if cursor is None:
        return -1, 0, "", UUID(int=0)
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        rank, age, title, identity = parts
        if not (
            isinstance(parts, list)
            and type(rank) is int
            and 0 <= rank <= 4
            and type(age) is int
            and -(2**63) <= age < 2**63
            and isinstance(title, str)
            and len(title) <= 96
            and "\x00" not in title
            and isinstance(identity, str)
        ):
            raise ValueError
        return rank, age, title, UUID(identity)
    except ValueError, TypeError, binascii.Error:
        raise LibraryCommandError("cursor_invalid") from None


def search_playback_page(
    session: Session,
    principal: Principal,
    query: str,
    limit: int,
    kind: MusicSearchKind,
    cursor: str | None = None,
) -> tuple[list[dict[str, object]], str | None]:
    query, kind = validate_music_search(query, limit, kind)
    rank, age, label, after_id = _read_cursor(cursor)
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    fields = {
        MusicSearchKind.TRACK: ("title",),
        MusicSearchKind.ARTIST: ("artist",),
        MusicSearchKind.ALBUM: ("album",),
        MusicSearchKind.ALL: ("title", "artist", "album"),
    }[kind]
    own_match = " OR ".join(f"{field} ILIKE :needle" for field in fields)
    title = "concat_ws(' ',r.title,nullif(r.version_text,'')) ILIKE :needle"
    artist = "a.display_name ILIKE :needle"
    if "artist" in fields:
        members = """EXISTS (SELECT FROM catalog.artist_credit_name n
            JOIN catalog.artist ar USING(artist_id)
            WHERE ar.deleted_at IS NULL AND n.artist_credit_id=r.artist_credit_id
              AND (ar.name ILIKE :needle OR n.credited_name ILIKE :needle))"""
        artist = f"({artist} OR {members})"
        own_match += (
            f" OR EXISTS (SELECT FROM catalog.recording r "
            f"WHERE r.recording_id=owner_tracks.recording_id AND {members})"
        )
    catalog_match = {
        MusicSearchKind.TRACK: title,
        MusicSearchKind.ARTIST: artist,
        MusicSearchKind.ALBUM: _ALBUM_MATCH,
        MusicSearchKind.ALL: (
            f"{title} OR {artist} OR {_ALBUM_MATCH} OR "
            "concat_ws(' ',a.display_name,r.title,r.version_text) ILIKE :needle OR "
            "concat_ws(' ',r.title,r.version_text,a.display_name) ILIKE :needle"
        ),
    }[kind]
    rows = (
        session.execute(
            text(f"""
        WITH owner_tracks AS (
            SELECT l.library_entry_id AS id, u.user_track_ref_id AS ref, u.recording_id,
                   l.source, l.availability_status, l.row_version, l.added_at,
                   CASE WHEN (m.document->'fields') ? 'title' THEN m.document->'fields'->>'title'
                        ELSE u.raw_title END AS title,
                   CASE WHEN (m.document->'fields') ? 'artist' THEN m.document->'fields'->>'artist'
                        ELSE u.raw_artist END AS artist,
                   CASE WHEN (m.document->'fields') ? 'album' THEN m.document->'fields'->>'album'
                        ELSE u.raw_album END AS album, u.raw_duration_ms
            FROM library.library_entry l JOIN library.user_track_ref u USING(user_track_ref_id)
            LEFT JOIN library.track_metadata m USING(user_track_ref_id)
            WHERE l.user_id=:owner AND u.user_id=:owner AND l.removed_at IS NULL
              AND u.deleted_at IS NULL
        ), owned AS MATERIALIZED (SELECT * FROM owner_tracks WHERE {own_match}),
        hits AS (
            SELECT o.id,o.ref,o.recording_id,o.source,o.availability_status,o.row_version,
                   o.title,o.artist,coalesce(v.duration_ms,o.raw_duration_ms) AS duration_ms,
                   CASE o.availability_status WHEN 'VAULT' THEN 0 WHEN 'LOCAL' THEN 1 ELSE 2 END
                       AS sort_rank,
                   -(extract(epoch FROM o.added_at)*1000000)::bigint AS sort_age,
                   ''::text AS sort_title
            FROM owned o LEFT JOIN vault.recording_canonical_variant cv USING(recording_id)
            LEFT JOIN vault.audio_variant v USING(audio_variant_id)
            UNION ALL
            SELECT p.external_reference_id,NULL::uuid,p.recording_id,'SERVER_CATALOG','VAULT',1,
                   concat_ws(' ',r.title,nullif(r.version_text,'')),a.display_name,v.duration_ms,
                   CASE WHEN lower(r.title)=lower(:query) THEN 3 ELSE 4 END, 0, left(r.title,96)
            FROM ({PUBLISHED_AUDIO}) p JOIN catalog.recording r USING(recording_id)
            JOIN catalog.artist_credit a USING(artist_credit_id)
            JOIN vault.audio_variant v USING(audio_variant_id)
            WHERE ({catalog_match})
              AND NOT EXISTS (SELECT FROM owned o WHERE o.availability_status='VAULT'
                              AND o.recording_id=p.recording_id)
              AND NOT EXISTS (SELECT FROM owned o
                  JOIN library.user_track_ref_external_reference link
                    ON link.user_track_ref_id=o.ref
                  WHERE link.external_reference_id=p.external_reference_id
                    AND link.relation_role='PRIMARY_SOURCE')
        )
        SELECT * FROM hits
        WHERE (sort_rank,sort_age,sort_title COLLATE "C",id) > (:rank,:age,:label,:after_id)
        ORDER BY sort_rank,sort_age,sort_title COLLATE "C",id LIMIT :limit
    """),
            {
                "owner": principal.user_id,
                "needle": f"%{escaped}%",
                "query": query,
                "limit": limit + 1,
                "rank": rank,
                "age": age,
                "label": label,
                "after_id": after_id,
            },
        )
        .mappings()
        .all()
    )
    results = []
    for row in rows[:limit]:
        identity = row["id"]
        ref = row["ref"] or uuid5(principal.user_id, f"autplay-server-catalog:{identity}")
        result = {
            k: row[k]
            for k in (
                "source",
                "availability_status",
                "row_version",
                "title",
                "artist",
                "duration_ms",
            )
        }
        result["library_entry_id" if row["ref"] else "catalog_id"] = str(identity)
        result["user_track_ref_id"] = str(ref)
        result["recording_id"] = str(row["recording_id"]) if row["recording_id"] else None
        results.append(result)
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        key = [last["sort_rank"], last["sort_age"], last["sort_title"], str(last["id"])]
        next_cursor = (
            base64.urlsafe_b64encode(json.dumps(key, ensure_ascii=False).encode())
            .decode()
            .rstrip("=")
        )
    return results, next_cursor


def search_playback(
    session: Session, principal: Principal, query: str, limit: int, kind: MusicSearchKind
) -> list[dict[str, object]]:
    return search_playback_page(session, principal, query, limit, kind)[0]


def prepare_playback(session: Session, owner: UUID, publications: Sequence[UUID]) -> list[str]:
    from autplay.application.sync import _acquire_sync_owner_publish_lock

    _acquire_sync_owner_publish_lock(session, owner)
    picks = (
        session.execute(
            text(f"""
        {_PUBLISHED_DETAILS} WHERE p.external_reference_id=ANY(:ids)
    """),
            {"ids": list(publications)},
        )
        .mappings()
        .all()
    )
    refs = ensure_catalog_refs(session, owner, picks) if picks else []
    return (
        list(
            session.scalars(
                text("""
        SELECT user_track_ref_id::text FROM library.user_track_ref
        WHERE user_id=:owner AND deleted_at IS NULL AND user_track_ref_id=ANY(:ids)
    """),
                {"owner": owner, "ids": refs},
            )
        )
        if refs
        else []
    )
