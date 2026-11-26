"""Owner-scoped, resumable collection previews and revision-checked confirmation."""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter
from dataclasses import asdict, dataclass, field
from math import ceil
from threading import Lock, Timer
from time import monotonic, time
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters import collection_access
from autplay.adapters.external_collections import CollectionReader, resolve_link
from autplay.adapters.postgresql.models import CollectionImportRow
from autplay.adapters.postgresql.shared_catalog import OWNER_AUDIO, PUBLISHED_AUDIO
from autplay.application.collection_projection import (
    project_collection,
    recording_matches,
    refresh_saved_collection,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.external_collections import (
    CollectionError,
    CollectionLink,
    CollectionPage,
    SourceTrack,
)
from autplay.domain.import_identity import MAX_IMPORT_ROWS


@dataclass
class _SoundCloudSession:
    cursor: str | None = None
    tracks: list[dict[str, Any]] = field(default_factory=list)
    sets: list[tuple[str, str]] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    selected: list[UUID] = field(default_factory=list)
    selection_key: str | None = None
    release_key: str | None = None


class ExternalCollections:
    def __init__(
        self, sessions: sessionmaker[Session], reader: CollectionReader | None = None
    ) -> None:
        self.sessions = sessions
        self.reader = reader or CollectionReader()
        self._sc_lock = Lock()
        self._sc_sessions: dict[tuple[UUID, UUID], tuple[_SoundCloudSession, Timer]] = {}

    def _take_sc(self, owner: UUID, operation: UUID) -> _SoundCloudSession | None:
        with self._sc_lock:
            cached = self._sc_sessions.pop((owner, operation), None)
            if cached:
                cached[1].cancel()
                return cached[0]
            return None

    def _cache_sc(self, owner: UUID, operation: UUID, data: _SoundCloudSession) -> None:
        key = owner, operation

        def expire() -> None:
            with self._sc_lock:
                if key in self._sc_sessions and self._sc_sessions[key][0] is data:
                    del self._sc_sessions[key]

        timer = Timer(120, expire)
        timer.daemon = True
        with self._sc_lock:
            old = self._sc_sessions.pop(key, None)
            if old:
                old[1].cancel()
            if len(self._sc_sessions) >= 16:
                self._sc_sessions.pop(next(iter(self._sc_sessions)))[1].cancel()
            self._sc_sessions[key] = (data, timer)
            timer.start()

    @staticmethod
    def _row(
        session: Session, owner: UUID, operation: UUID, *, lock: bool = False
    ) -> CollectionImportRow:
        row = session.get(CollectionImportRow, (owner, operation), with_for_update=lock)
        if row is None:
            raise CollectionError("import_not_found", 404)
        return row

    def start(
        self, owner: UUID, operation: UUID, url: str, *, favorites: bool, new_copy: bool
    ) -> dict[str, Any]:
        link = resolve_link(url)
        action = link.action(favorites)
        collection = uuid5(owner, f"external-collection:{link.provider}:{link.key}:{action}")
        if new_copy:
            collection = uuid5(collection, str(operation))
        with self.sessions.begin() as session:
            session.execute(
                insert(CollectionImportRow)
                .values(
                    owner=owner,
                    operation_id=operation,
                    collection_id=collection,
                    provider=link.provider,
                    source_key=link.key,
                    kind=link.kind,
                    action=action,
                    state="READING",
                )
                .on_conflict_do_nothing()
            )
            row = self._row(session, owner, operation)
            if row.collection_id != collection:
                raise CollectionError("operation_conflict", 409)
        return self.advance(owner, operation)

    def report(self, owner: UUID, operation: UUID, offset: int = 0) -> dict[str, Any]:
        with self.sessions() as session:
            return self._view(session, self._row(session, owner, operation), offset)

    def cleanup_expired(self, limit: int = 500) -> int:
        with self.sessions.begin() as session:
            return len(
                session.execute(
                    text("""
                WITH expired AS (
                    SELECT owner, operation_id FROM importing.collection_import
                    WHERE state <> 'SAVED' AND created_at < now() - CASE provider
                        WHEN 'spotify' THEN interval '1 hour'
                        WHEN 'soundcloud' THEN interval '1 day'
                        ELSE interval '7 days' END
                    ORDER BY created_at LIMIT :limit FOR UPDATE SKIP LOCKED
                )
                DELETE FROM importing.collection_import c USING expired e
                WHERE (c.owner,c.operation_id)=(e.owner,e.operation_id) RETURNING c.operation_id
            """),
                    {"limit": limit},
                ).all()
            )

    def recent(self, owner: UUID) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(CollectionImportRow)
                .where(CollectionImportRow.owner == owner)
                .order_by(CollectionImportRow.created_at.desc())
                .limit(30)
            )
            return [
                {
                    "operation_id": str(r.operation_id),
                    "name": r.snapshot.get("name") or r.provider,
                    "state": r.state,
                    "action": r.action,
                }
                for r in rows
            ]

    def advance(self, owner: UUID, operation: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = self._row(session, owner, operation, lock=True)
            if row.state in {"PREVIEW", "SAVED", "CANCELLED"}:
                return self._view(session, row)
            snapshot, revision = row.snapshot, row.revision
            link = CollectionLink(row.provider, row.kind, row.source_key)
            favorites = row.action == "FAVORITES"
            remaining = ceil(snapshot.get("retry_at", 0) - time())
            if remaining > 0:
                raise CollectionError("rate_limited", 429, remaining)
            token = collection_access.access_token(row.provider, snapshot, owner)
            if link.provider == "spotify" and link.kind != "ALBUM" and not token:
                url, nonce = collection_access.spotify_authorization(
                    owner, operation, likes=link.kind == "LIKES"
                )
                row.snapshot = {**snapshot, "oauth_nonce": nonce}
                row.state = "AUTH_REQUIRED"
                return {**self._view(session, row), "authorization_url": url}
            if link.provider == "spotify" and link.kind == "ALBUM":
                page = self._catalog_album(session, link)
            else:
                page = None
        try:
            if link.provider == "soundcloud":
                preview = self._soundcloud_preview(owner, operation, link, favorites, token)
                preview.pop("_selected", None)
                preview.pop("_release_key", None)
                return preview
            page = page or self.reader.page(link, favorites, snapshot.get("cursor"), token)
        except CollectionError as error:
            with self.sessions.begin() as session:
                row = self._row(session, owner, operation, lock=True)
                if row.revision == revision and row.state not in {"CANCELLED", "SAVED"}:
                    updated = dict(row.snapshot)
                    if error.retry_after:
                        updated["retry_at"] = time() + error.retry_after
                    if error.status in {403, 404}:
                        # A rejected grant invalidates all earlier pages.
                        updated = {}
                    row.snapshot, row.state, row.revision = updated, "PARTIAL", revision + 1
            raise
        with self.sessions.begin() as session:
            row = self._row(session, owner, operation, lock=True)
            if row.revision != revision or row.state in {"CANCELLED", "SAVED"}:
                return self._view(session, row)
            if snapshot.get("cursor") and (
                page.source_revision != snapshot.get("source_revision")
                or page.expected != snapshot.get("expected")
            ):
                row.snapshot = {k: v for k, v in snapshot.items() if k == "grant"}
                row.state, row.revision = "READING", revision + 1
                return self._view(session, row)
            tracks = [*snapshot.get("tracks", []), *(asdict(t) for t in page.tracks)]
            if len(tracks) > MAX_IMPORT_ROWS:
                raise CollectionError("collection_too_large", 413)
            row.snapshot = {
                **snapshot,
                "name": page.name,
                "expected": page.expected,
                "likes_count": page.likes_count,
                "tracks": tracks,
                "cursor": page.next_cursor,
                "source_revision": page.source_revision,
            }
            row.revision += 1
            row.state = "READING" if page.next_cursor else "PREVIEW"
            return self._view(session, row)

    @staticmethod
    def _catalog_album(session: Session, link: CollectionLink) -> CollectionPage:
        rows = session.execute(
            text("""
            SELECT r.release_id,r.title AS album, ac.display_name AS album_artist, rt.title,
                   a.display_name AS artist,
                   rt.duration_ms, rt.recording_id, m.position AS disc, rt.sequence_no AS number
              FROM identity.external_reference x JOIN identity.source_provider p USING(provider_id)
              JOIN catalog.release r USING(release_id)
              JOIN catalog.artist_credit ac USING(artist_credit_id)
              JOIN catalog.medium m USING(release_id) JOIN catalog.release_track rt USING(medium_id)
              JOIN catalog.artist_credit a ON a.artist_credit_id=rt.artist_credit_id
             WHERE lower(p.provider_key)=:provider AND x.external_entity_type='RELEASE'
               AND x.external_id=:key AND p.deleted_at IS NULL AND x.deleted_at IS NULL
               AND r.deleted_at IS NULL AND m.deleted_at IS NULL AND rt.deleted_at IS NULL
             ORDER BY m.position,rt.sequence_no LIMIT 10001
        """),
            {"key": link.key, "provider": link.provider},
        ).all()
        if not rows or len({r.release_id for r in rows}) != 1:
            raise CollectionError("catalog_album_not_found", 404)
        return CollectionPage(
            rows[0].album,
            len(rows),
            [
                SourceTrack(
                    str(r.recording_id),
                    r.title,
                    r.artist,
                    r.duration_ms,
                    r.album,
                    str(r.release_id) if link.provider == "soundcloud" else link.key,
                    r.album_artist,
                    r.disc,
                    r.number,
                )
                for r in rows
            ],
        )

    def _soundcloud_preview(
        self, owner: UUID, operation: UUID, link: CollectionLink, favorites: bool, token: str | None
    ) -> dict[str, Any]:
        cached = self._take_sc(owner, operation)
        progress = cached if cached and cached.cursor else _SoundCloudSession()
        cursor = progress.cursor
        tracks, sets = progress.tracks, progress.sets
        deadline = monotonic() + 4
        while True:
            try:
                page = self.reader.page(link, favorites, cursor, token)
            except CollectionError as error:
                if cursor and error.status in {429, 502, 503}:
                    self._cache_sc(owner, operation, progress)
                raise
            tracks.extend(asdict(t) for t in page.tracks)
            sets.extend(page.sets)
            if len(tracks) + len(sets) > MAX_IMPORT_ROWS:
                raise CollectionError("collection_too_large", 413)
            cursor = page.next_cursor
            if not cursor:
                break
            if cursor in progress.visited:
                raise CollectionError("invalid_source_cursor", 502)
            progress.visited.add(cursor)
            progress.cursor = cursor
            if monotonic() >= deadline:
                break
        with self.sessions() as session:
            row = self._row(session, owner, operation)
            if row.state in {"CANCELLED", "SAVED"}:
                return self._view(session, row)
            matches, available = recording_matches(session, row, tracks)
            selected = [
                matches[t["track_id"]] for t in tracks if matches.get(t["track_id"]) in available
            ]
            release_key = None
            eligible = bool(selected) and not cursor
            if page.kind == "ALBUM":
                eligible = False
                if (
                    page.release_key
                    and len(tracks) == len(selected)
                    and page.expected in {None, len(tracks)}
                ):
                    try:
                        edition = self._catalog_album(
                            session, CollectionLink("soundcloud", "ALBUM", page.release_key)
                        )
                        eligible = [t.track_id for t in edition.tracks] == [
                            str(i) for i in selected
                        ]
                        release_key = page.release_key if eligible else None
                    except CollectionError as error:
                        if error.code != "catalog_album_not_found":
                            raise
            selection_key = (
                hashlib.sha256(
                    (release_key or "").encode() + b"".join(x.bytes for x in selected)
                ).hexdigest()
                if eligible
                else None
            )
            if cursor:
                self._cache_sc(owner, operation, progress)
            elif selection_key:
                # The completed receipt contains only independent AutPlay recording IDs.
                self._cache_sc(
                    owner,
                    operation,
                    _SoundCloudSession(
                        selected=selected, selection_key=selection_key, release_key=release_key
                    ),
                )
            return {
                **self._view(session, row),
                "_selected": selected,
                "_release_key": release_key,
                "selection_key": selection_key,
                "identified": sum(t["track_id"] in matches for t in tracks),
                "playable": len(selected),
                "state": "READING" if cursor else "SESSION_PREVIEW",
                "name": page.name,
                "action": page.kind or row.action,
                "expected": page.expected,
                "read": len(tracks),
                "expected_sets": page.expected_sets,
                "read_sets": len(sets),
                "sets": sets,
                "complete": not cursor
                and page.expected in {None, len(tracks)}
                and page.expected_sets in {None, len(sets)},
                "entries": tracks[:50],
            }

    @staticmethod
    def _previous(session: Session, row: CollectionImportRow) -> CollectionImportRow | None:
        return session.scalar(
            select(CollectionImportRow)
            .where(
                CollectionImportRow.owner == row.owner,
                CollectionImportRow.collection_id == row.collection_id,
                CollectionImportRow.state == "SAVED",
                CollectionImportRow.operation_id != row.operation_id,
                CollectionImportRow.created_at <= row.created_at,
            )
            .order_by(CollectionImportRow.created_at.desc())
            .limit(1)
        )

    def confirm(
        self, owner: UUID, operation: UUID, revision: int, selection_key: str | None = None
    ) -> dict[str, Any]:
        # Verify scoped access again before promoting a private Spotify page to the library.
        with self.sessions() as session:
            current = self._row(session, owner, operation)
            needs_access = (
                current.provider == "spotify"
                and current.kind != "ALBUM"
                and current.state == "PREVIEW"
            )
            link = CollectionLink(current.provider, current.kind, current.source_key)
            source_revision = current.snapshot.get("source_revision")
            token = (
                collection_access.access_token("spotify", current.snapshot)
                if needs_access
                else None
            )
        if needs_access:
            try:
                if not token:
                    raise CollectionError("source_access_required", 403)
                self.reader.check_spotify_access(link, token, source_revision)
            except CollectionError as error:
                if error.status in {403, 404, 409}:
                    with self.sessions.begin() as session:
                        current = self._row(session, owner, operation, lock=True)
                        if current.state == "PREVIEW" and current.revision == revision:
                            current.snapshot, current.state, current.revision = (
                                {},
                                "PARTIAL",
                                revision + 1,
                            )
                raise
        selection = None
        selected_release = None
        if selection_key:
            with self.sessions() as session:
                row = self._row(session, owner, operation)
                if row.provider != "soundcloud":
                    raise CollectionError("invalid_selection")
                if row.state == "SAVED":
                    return self._view(session, row)
                link = CollectionLink(row.provider, row.kind, row.source_key)
                favorites = row.action == "FAVORITES"
            sc_token = collection_access.access_token("soundcloud", {})
            receipt = self._take_sc(owner, operation)
            if (
                receipt
                and receipt.selection_key
                and hmac.compare_digest(receipt.selection_key, selection_key)
            ):
                # Recheck source access. Catalog rights are checked again under the owner lock.
                live = self.reader.page(link, favorites, None, sc_token)
                if (live.kind == "ALBUM" or receipt.release_key) and (
                    live.kind != "ALBUM"
                    or not receipt.release_key
                    or live.release_key != receipt.release_key
                ):
                    raise CollectionError("stale_preview", 409)
                selection, selected_release = receipt.selected, receipt.release_key
            else:
                preview = self._soundcloud_preview(owner, operation, link, favorites, sc_token)
                if not preview.get("selection_key") or not hmac.compare_digest(
                    preview["selection_key"], selection_key
                ):
                    raise CollectionError("stale_preview", 409)
                selection = preview["_selected"]
                selected_release = preview["_release_key"]

        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, owner)
            row = self._row(session, owner, operation, lock=True)
            if row.revision != revision:
                raise CollectionError("stale_preview", 409)
            if row.state == "SAVED":
                return self._view(session, row)
            if selection is not None and row.state in {"READING", "PARTIAL"}:
                # Save only the independently obtained catalog metadata.
                records = {
                    r.recording_id: r
                    for r in session.execute(
                        text("""
                    SELECT r.recording_id,r.title,r.duration_ms,c.display_name
                    FROM catalog.recording r
                    JOIN catalog.artist_credit c USING(artist_credit_id) WHERE r.deleted_at IS NULL
                    AND r.recording_id=ANY(CAST(:ids AS uuid[]))"""),
                        {"ids": selection},
                    )
                }
                if set(records) != set(selection):
                    raise CollectionError("stale_preview", 409)
                row.action = "FAVORITES" if row.kind == "LIKES" else "PLAYLIST"
                row.snapshot = {
                    "name": "SoundCloud",
                    "catalog_selection": True,
                    "expected": len(selection),
                    "tracks": [
                        asdict(
                            SourceTrack(
                                str(rid),
                                records[rid].title,
                                records[rid].display_name,
                                records[rid].duration_ms,
                            )
                        )
                        for rid in selection
                    ],
                }
                if selected_release:
                    edition = self._catalog_album(
                        session, CollectionLink("soundcloud", "ALBUM", selected_release)
                    )
                    if [t.track_id for t in edition.tracks] != [str(i) for i in selection]:
                        raise CollectionError("stale_preview", 409)
                    row.action = "ALBUM"
                    row.snapshot = {
                        **row.snapshot,
                        "name": edition.name,
                        "tracks": [asdict(t) for t in edition.tracks],
                    }
                _, still_available = recording_matches(session, row)
                if still_available != set(selection):
                    raise CollectionError("stale_preview", 409)
                row.state = "PREVIEW"
            if row.state != "PREVIEW":
                raise CollectionError("preview_required", 409)
            newer = session.scalar(
                select(CollectionImportRow.operation_id)
                .where(
                    CollectionImportRow.owner == owner,
                    CollectionImportRow.collection_id == row.collection_id,
                    CollectionImportRow.created_at > row.created_at,
                    CollectionImportRow.state == "SAVED",
                )
                .limit(1)
            )
            if newer:
                raise CollectionError("stale_preview", 409)
            previous = self._previous(session, row)
            if (
                previous is not None
                and previous.snapshot.get("tracks") == row.snapshot["tracks"]
                and previous.snapshot.get("name") == row.snapshot.get("name")
                and previous.import_job_id
            ):
                row.import_job_id = previous.import_job_id
                row.snapshot = {
                    **row.snapshot,
                    "source_order": previous.snapshot.get("source_order", []),
                }
            else:
                project_collection(session, row)
            session.flush()
            refresh_saved_collection(session, row)
            if (
                previous
                and self._complete(row)
                and self._complete(previous)
                and (
                    row.provider == "yandex"
                    or (
                        row.snapshot.get("source_revision")
                        and previous.snapshot.get("source_revision")
                    )
                )
            ):
                prior = Counter(t["track_id"] for t in previous.snapshot["tracks"] if t["track_id"])
                current_counts = Counter(
                    t["track_id"] for t in row.snapshot["tracks"] if t["track_id"]
                )
                row.snapshot = {
                    **row.snapshot,
                    "missing_occurrences": sum((prior - current_counts).values()),
                }
            row.state = "SAVED"
            row.snapshot = {
                k: v for k, v in row.snapshot.items() if k not in {"grant", "oauth_nonce"}
            }
            return self._view(session, row)

    def cancel(self, owner: UUID, operation: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = self._row(session, owner, operation, lock=True)
            if row.state == "SAVED":
                raise CollectionError("already_saved", 409)
            self._take_sc(owner, operation)
            row.state, row.snapshot, row.revision = "CANCELLED", {}, row.revision + 1
            return self._view(session, row)

    def authorize_spotify(self, state: str, code: str) -> None:
        owner, operation, verifier = collection_access.spotify_state(state)
        with self.sessions.begin() as session:
            row = self._row(session, owner, operation, lock=True)
            if row.state != "AUTH_REQUIRED" or not hmac.compare_digest(
                row.snapshot.get("oauth_nonce", ""), hashlib.sha256(state.encode()).hexdigest()
            ):
                raise CollectionError("authorization_expired", 403)
            row.snapshot = {"grant": collection_access.spotify_exchange(code, verifier)}
            row.state, row.revision = "READING", row.revision + 1

    @staticmethod
    def _complete(row: CollectionImportRow) -> bool:
        doc = row.snapshot
        count = len(doc.get("tracks", []))
        return (
            row.state in {"PREVIEW", "SAVED"}
            and doc.get("expected") in {None, count}
            and doc.get("likes_count") in {None, count}
        )

    @staticmethod
    def _view(session: Session, row: CollectionImportRow, offset: int = 0) -> dict[str, Any]:
        doc = row.snapshot
        tracks = doc.get("tracks", [])
        expected, likes = doc.get("expected"), doc.get("likes_count")
        matches, available = recording_matches(session, row)
        identified = sum(t["track_id"] in matches for t in tracks)
        playable = sum(matches.get(t["track_id"]) in available for t in tracks)
        if row.import_job_id:
            counts = session.execute(
                text(f"""
                WITH visible AS (SELECT recording_id FROM ({PUBLISHED_AUDIO}) pub UNION
                    SELECT recording_id FROM ({OWNER_AUDIO}) own WHERE user_id=:owner)
                SELECT count(r.id), count(v.recording_id) FROM importing.import_entry e
                LEFT JOIN library.user_track_ref u ON u.user_track_ref_id=e.user_track_ref_id
                    AND u.user_id=:owner
                LEFT JOIN playlist.imported_track_reference i
                    ON i.user_track_ref_id=u.user_track_ref_id
                CROSS JOIN LATERAL (SELECT coalesce(e.selected_recording_id,u.recording_id,
                    i.source_recording_id) AS id) r
                LEFT JOIN visible v ON v.recording_id=r.id WHERE e.import_job_id=:job
            """),
                {"owner": row.owner, "job": row.import_job_id},
            ).one()
            identified, playable = counts
        return {
            "operation_id": str(row.operation_id),
            "collection_id": str(row.collection_id),
            "action": row.action,
            "provider": row.provider,
            "state": row.state,
            "revision": row.revision,
            "name": doc.get("name") or row.provider,
            "expected": expected,
            "read": len(tracks),
            "likes_count": likes,
            "identified": identified,
            "playable": playable,
            "complete": ExternalCollections._complete(row),
            "missing_occurrences": doc.get("missing_occurrences"),
            "entries": tracks[offset : offset + 50],
            "offset": offset,
            "next_offset": offset + 50 if offset + 50 < len(tracks) else None,
            "import_job_id": str(row.import_job_id) if row.import_job_id else None,
        }
