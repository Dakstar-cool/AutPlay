"""Search the owner's library and the operator-published streaming catalog."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.shared_catalog import (
    PUBLIC_ARTIST,
    PUBLIC_LABEL_JOIN,
    PUBLIC_TITLE,
    PUBLISHED_AUDIO,
    ensure_catalog_refs,
)
from autplay.adapters.postgresql.stage_credits import CATALOG_MEMBERS
from autplay.domain.artist_credits import recording_credit, single_stage_credit
from autplay.domain.auth import Principal
from autplay.domain.library import LibraryCommandError
from autplay.domain.music_search import MusicSearchKind, validate_music_search
from autplay.domain.track_names import search_label, track_names

_PUBLISHED_DETAILS = f"""
    SELECT p.*, r.title, r.version_text, a.display_name AS artist, v.duration_ms, {CATALOG_MEMBERS} AS members
    FROM ({PUBLISHED_AUDIO}) p
    JOIN catalog.recording r USING (recording_id)
    JOIN catalog.artist_credit a USING (artist_credit_id)
    JOIN vault.audio_variant v USING (audio_variant_id)
"""


def _search_pattern(value: str) -> str:
    escaped = search_label(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _searchable(expression: str, parameter: str = "needle") -> str:
    folded = f"translate(normalize({expression}, NFKC), '’‘‐‑–—', chr(39)||chr(39)||'----')"
    spaced = rf"regexp_replace({folded}, '\s+', ' ', 'g')"
    return (
        f"regexp_replace({spaced}, "
        r"'\m25\s*([/-]|\s)\s*17\M', '25/17', 'g') ILIKE :" + parameter
    )


_ALBUM_MATCH = f"""
    EXISTS (SELECT FROM catalog.release_track rt
        JOIN catalog.medium m USING (medium_id)
        JOIN catalog.release rel USING (release_id)
        WHERE rt.recording_id=p.recording_id AND rt.deleted_at IS NULL
          AND m.deleted_at IS NULL AND rel.deleted_at IS NULL AND {_searchable("rel.title")})
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
    fields = {
        MusicSearchKind.TRACK: ("title",),
        MusicSearchKind.ARTIST: ("artist",),
        MusicSearchKind.ALBUM: ("album",),
        MusicSearchKind.ALL: ("title", "artist", "album"),
    }[kind]
    own_match = " OR ".join(_searchable(field) for field in fields)
    if "title" in fields:
        original = _searchable("original_label")
        spaced = _searchable("replace(original_label, '_', ' ')")
        own_match += (
            f" OR (title IS NOT NULL AND original_label IS NOT NULL AND ({original} OR {spaced}))"
        )
    display_title = _searchable(f"concat_ws(' ',{PUBLIC_TITLE},nullif(r.version_text,''))")
    title = f"({display_title} OR {_searchable('r.title')})"
    artist = _searchable(PUBLIC_ARTIST)
    alias = single_stage_credit(query) if "artist" in fields else None
    if "artist" in fields:
        if alias and search_label(alias.stage) != search_label(query):
            own_match += " OR " + _searchable("artist", "stage_needle")
            artist = f"({artist} OR {_searchable(PUBLIC_ARTIST, 'stage_needle')})"
        members = f"""EXISTS (SELECT FROM catalog.artist_credit_name n
            JOIN catalog.artist ar USING(artist_id)
            WHERE ar.deleted_at IS NULL AND n.artist_credit_id=r.artist_credit_id
              AND ({_searchable("ar.name")} OR {_searchable("n.credited_name")}
                OR (ar.identity_status='ACTIVE' AND EXISTS (
                  SELECT FROM catalog.artist_credit_name other WHERE other.artist_id=n.artist_id
                    AND {_searchable("other.credited_name")}))))"""
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
            + _searchable(f"concat_ws(' ',{PUBLIC_ARTIST},{PUBLIC_TITLE},r.version_text)")
            + " OR "
            + _searchable(f"concat_ws(' ',{PUBLIC_TITLE},r.version_text,{PUBLIC_ARTIST})")
        ),
    }[kind]
    rows = (
        session.execute(
            text(f"""
        WITH owner_tracks AS (
            SELECT l.library_entry_id AS id, u.user_track_ref_id AS ref, u.recording_id,
                   l.source, l.availability_status, l.row_version, l.added_at,
                   coalesce((m.document->'fields') ? 'title',false) AS has_title,
                   coalesce((m.document->'fields') ? 'artist',false) AS has_artist,
                   CASE WHEN u.raw_title ~* '[.](mp3|flac|m4a|aac|ogg|oga|opus|wav|aif|aiff|alac|wma|aifc)$'
                          OR m.document->'provenance'->'title'->>'normalization_version' LIKE 'display-%'
                          OR (NOT coalesce((m.document->'fields') ? 'title',false)
                              AND (strpos(u.raw_title,'_')>0 OR strpos(u.raw_title,'|')>0 OR strpos(u.raw_title,'(')>0
                                   OR strpos(u.raw_title,'[')>0 OR u.raw_title ~ ' [-–—] '))
                        THEN u.raw_title END AS original_label,
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
                   ''::text AS sort_title, o.has_title, o.has_artist, o.original_label,
                   NULL::text AS version_text
            FROM owned o LEFT JOIN vault.recording_canonical_variant cv USING(recording_id)
            LEFT JOIN vault.audio_variant v USING(audio_variant_id)
            UNION ALL
            SELECT p.external_reference_id,NULL::uuid,p.recording_id,'SERVER_CATALOG','VAULT',1,
                   {PUBLIC_TITLE},{PUBLIC_ARTIST},v.duration_ms,
                   CASE WHEN lower(r.title)=lower(:query) THEN 3 ELSE 4 END, 0, left(r.title,96),
                   label.page_key IS NOT NULL OR r.identity_status<>'PROVISIONAL',
                   label.page_key IS NOT NULL OR r.identity_status<>'PROVISIONAL',
                   r.title, r.version_text
            FROM ({PUBLISHED_AUDIO}) p JOIN catalog.recording r USING(recording_id)
            JOIN catalog.artist_credit a USING(artist_credit_id)
            {PUBLIC_LABEL_JOIN}
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
        SELECT page.*, CASE WHEN page.ref IS NULL THEN {CATALOG_MEMBERS} END AS members
        FROM (SELECT * FROM hits
            WHERE (sort_rank,sort_age,sort_title COLLATE "C",id) > (:rank,:age,:label,:after_id)
            ORDER BY sort_rank,sort_age,sort_title COLLATE "C",id LIMIT :limit) page
        LEFT JOIN catalog.recording r ON r.recording_id=page.recording_id AND page.ref IS NULL
        ORDER BY page.sort_rank,page.sort_age,page.sort_title COLLATE "C",page.id
    """),
            {
                "owner": principal.user_id,
                "needle": _search_pattern(query),
                "stage_needle": _search_pattern(alias.stage) if alias else None,
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
        fallback_title, fallback_artist = track_names(
            row["original_label"] or row["title"], row["artist"]
        )
        if not row["has_title"]:
            result["title"] = fallback_title
        linked = recording_credit(row["members"])
        if linked and row["artist"] and linked.matches(row["artist"]):
            result["artist"] = linked.preferred
        elif not row["has_artist"]:
            result["artist"] = fallback_artist
        if row["version_text"]:
            result["title"] = f"{result['title']} {row['version_text']}"
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
