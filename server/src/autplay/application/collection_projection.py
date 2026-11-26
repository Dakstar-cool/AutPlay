"""Project a confirmed provider snapshot into ordinary owner library and sync records."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.import_runtime import PostgresImportRepository
from autplay.adapters.postgresql.models import (
    CollectionImportRow,
    ImportedTrackReferenceRow,
    ImportEntryRow,
    LibraryEntryRow,
    PlaylistEntryRow,
    PlaylistRow,
    SyncEventRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.adapters.postgresql.shared_catalog import (
    OWNER_AUDIO,
    PUBLISHED_AUDIO,
    refresh_playlist_catalog_handles,
)
from autplay.application.metadata_projection import metadata_view
from autplay.domain.external_collections import CollectionError
from autplay.domain.import_identity import (
    ImportEnvelope,
    ImportFormat,
    ParsedImport,
    ParsedImportRow,
    extract_version_markers,
    normalize_text,
)


def track_key(owner: UUID, provider: str, track: dict[str, Any]) -> UUID | None:
    key = track.get("track_id")
    return uuid5(owner, f"external-track:{provider}:{key}") if key else None


def recording_matches(
    session: Session, row: CollectionImportRow, items: list[dict[str, Any]] | None = None
) -> tuple[dict[str, UUID], set[UUID]]:
    """A unique version-compatible ID can identify a recording before bytes are available."""
    tracks = {
        t["track_id"]: t
        for t in (items if items is not None else row.snapshot.get("tracks", []))
        if t.get("track_id")
    }
    if not tracks:
        return {}, set()
    local_album = (row.provider == "spotify" and row.kind == "ALBUM") or row.snapshot.get(
        "catalog_selection"
    ) is True
    candidates = (
        "SELECT r.recording_id::text AS key, r.recording_id, 'catalog' AS proof,"
        " NULL::text AS edition_title, NULL::text AS edition_artist FROM catalog.recording r "
        "WHERE r.recording_id=ANY(CAST(:ids AS uuid[]))"
        if local_album
        else """SELECT x.external_id AS key, x.recording_id, 'provider' AS proof,
             NULL::text AS edition_title, NULL::text AS edition_artist
           FROM identity.external_reference x JOIN identity.source_provider p USING(provider_id)
           WHERE lower(p.provider_key)=:provider AND p.deleted_at IS NULL AND x.deleted_at IS NULL
             AND x.external_entity_type IN ('RECORDING','TRACK')
             AND x.external_id=ANY(CAST(:ids AS text[]))
           UNION ALL
           SELECT e.raw_external_id,e.selected_recording_id,'manual',NULL,NULL
           FROM importing.import_entry e
           JOIN importing.import_job j USING(import_job_id) WHERE j.user_id=:owner
             AND j.adapter_id='collection.' || :provider AND e.match_status='MANUAL_MATCH'
             AND e.raw_external_id=ANY(CAST(:ids AS text[]))
           UNION ALL
           SELECT t.key,rt.recording_id,'edition',rt.title,a.display_name
           FROM jsonb_to_recordset(CAST(:editions AS jsonb))
                AS t(key text, album text, disc integer, position integer)
           JOIN identity.external_reference x ON x.external_entity_type='RELEASE'
                AND x.external_id=t.album AND x.deleted_at IS NULL
           JOIN identity.source_provider p ON p.provider_id=x.provider_id
                AND lower(p.provider_key)=:provider AND p.deleted_at IS NULL
           JOIN catalog.release rel ON rel.release_id=x.release_id AND rel.deleted_at IS NULL
           JOIN catalog.medium m ON m.release_id=rel.release_id AND m.position=t.disc
                AND m.deleted_at IS NULL
           JOIN catalog.release_track rt ON rt.medium_id=m.medium_id AND rt.sequence_no=t.position
                AND rt.deleted_at IS NULL
           JOIN catalog.artist_credit a ON a.artist_credit_id=rt.artist_credit_id"""
    )
    editions = [
        {"key": key, "album": item["album_id"], "disc": item["disc"], "position": item["number"]}
        for key, item in tracks.items()
        if item.get("album_id") and item.get("disc") and item.get("number")
    ]
    matches = session.execute(
        text(f"""
        WITH visible AS (SELECT recording_id FROM ({PUBLISHED_AUDIO}) pub UNION
            SELECT recording_id FROM ({OWNER_AUDIO}) own WHERE user_id=:owner)
        SELECT c.key,c.proof,c.edition_title,c.edition_artist,r.recording_id,r.duration_ms,
               r.version_text,r.recording_kind,
               r.explicit,r.title,
               v.recording_id IS NOT NULL AS playable
        FROM ({candidates}) c JOIN catalog.recording r USING(recording_id)
        LEFT JOIN visible v USING(recording_id)
        WHERE r.deleted_at IS NULL AND (v.recording_id IS NOT NULL OR EXISTS (
            SELECT FROM library.user_track_ref u WHERE u.recording_id=r.recording_id
            AND u.user_id=:owner AND u.deleted_at IS NULL))
    """),
        {
            "owner": row.owner,
            "provider": row.provider,
            "ids": list(tracks),
            "editions": json.dumps(editions),
        },
    )
    by_id: dict[str, set[UUID]] = {}
    manual: dict[str, set[UUID]] = {}
    playable: set[UUID] = set()
    for candidate in matches:
        item = tracks[candidate.key]
        if candidate.proof == "manual":
            manual.setdefault(candidate.key, set()).add(candidate.recording_id)
            if candidate.playable:
                playable.add(candidate.recording_id)
            continue
        if candidate.proof == "edition" and (
            not item["title"]
            or not item["artist"]
            or normalize_text(item["title"]) != normalize_text(candidate.edition_title)
            or normalize_text(item["artist"]) != normalize_text(candidate.edition_artist)
            or not item.get("duration_ms")
            or not candidate.duration_ms
        ):
            continue
        if item.get("explicit") is not None and item["explicit"] != candidate.explicit:
            continue
        if (
            item.get("duration_ms")
            and candidate.duration_ms
            and abs(item["duration_ms"] - candidate.duration_ms) > 2000
        ):
            continue
        source = extract_version_markers((item["title"] or "") + " " + (item["version"] or ""))
        target = extract_version_markers(
            " ".join(
                filter(None, (candidate.title, candidate.version_text, candidate.recording_kind))
            )
        )
        if set(source) != set(target) or (
            item.get("version")
            and not source
            and normalize_text(item["version"]) != normalize_text(candidate.version_text or "")
        ):
            continue
        by_id.setdefault(candidate.key, set()).add(candidate.recording_id)
        if candidate.playable:
            playable.add(candidate.recording_id)
    by_id.update(manual)
    return {k: next(iter(v)) for k, v in by_id.items() if len(v) == 1}, playable


def enqueue_authorized_recordings(session: Session, owner: UUID, recordings: set[UUID]) -> None:
    """Reuse manual discovery's license, owner, cancellation and Vault commit gates."""
    if not recordings:
        return
    from autplay.adapters.postgresql.discovery_runtime import (
        BulkDiscoveryError,
        PostgresBulkDiscoveryRepository,
    )

    rows = session.execute(
        text("""
        SELECT c.candidate_id,x.recording_id FROM discovery.candidate c
        JOIN identity.external_reference x ON x.provider_id=c.provider_id
          AND x.market_scope=c.market_scope
            AND x.external_entity_type='TRACK' AND x.external_id=c.provider_track_id
          AND x.deleted_at IS NULL
        JOIN identity.source_provider p ON p.provider_id=c.provider_id
          AND p.provider_key='jamendo' AND p.deleted_at IS NULL
        WHERE x.recording_id=ANY(CAST(:ids AS uuid[])) AND c.user_id=:owner
          AND c.canonical_artist_id IS NOT NULL
          AND c.disposition='SELECTABLE' AND c.acquisition_state='NOT_REQUESTED'
        ORDER BY c.candidate_id
    """),
        {"owner": owner, "ids": list(recordings)},
    ).all()
    queued: set[UUID] = set()
    for candidate, recording in rows:
        if recording in queued:
            continue
        try:
            with session.begin_nested():
                PostgresBulkDiscoveryRepository(session).enqueue_explicit_manual_candidate(
                    owner_user_id=owner,
                    candidate_id=candidate,
                    operation_id=uuid5(owner, f"collection-audio:{recording}"),
                    action="SELECT",
                    now=datetime.now(UTC),
                )
            queued.add(recording)
        except BulkDiscoveryError:
            continue


def project_reviewed_recording(
    session: Session, owner: UUID, job: UUID, original_ref: UUID | None, recording: UUID
) -> None:
    row = session.scalar(
        select(CollectionImportRow)
        .where(CollectionImportRow.owner == owner, CollectionImportRow.import_job_id == job)
        .limit(1)
    )
    if row is None or original_ref is None:
        return
    ref = session.get(UserTrackRefRow, original_ref)
    if not ref or ref.user_id != owner or ref.deleted_at:
        return
    if ref.recording_id is None:
        linked = session.get(ImportedTrackReferenceRow, original_ref)
        if linked is None:
            session.add(
                ImportedTrackReferenceRow(
                    user_track_ref_id=original_ref, source_recording_id=recording
                )
            )
        else:
            linked.source_recording_id = recording
    session.flush()
    refresh_playlist_catalog_handles(session, owner)
    if row.action != "PLAYLIST":
        available = session.scalar(
            text(f"""SELECT EXISTS(SELECT FROM ({PUBLISHED_AUDIO}) p WHERE recording_id=:recording
                  UNION ALL SELECT FROM ({OWNER_AUDIO}) o
                  WHERE user_id=:owner AND recording_id=:recording)"""),
            {"owner": owner, "recording": recording},
        )
        if not available:
            enqueue_authorized_recordings(session, owner, {recording})


def refresh_saved_collection(session: Session, row: CollectionImportRow) -> None:
    """Link newly confirmed recordings without recreating entries or changing personal choices."""
    exact, playable = recording_matches(session, row)
    if not exact:
        return
    entries = session.execute(
        select(
            ImportEntryRow.raw_external_id,
            UserTrackRefRow,
            ImportedTrackReferenceRow.source_recording_id,
        )
        .join(
            UserTrackRefRow, ImportEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id
        )
        .outerjoin(
            ImportedTrackReferenceRow,
            ImportedTrackReferenceRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
        )
        .where(
            ImportEntryRow.import_job_id == row.import_job_id,
            UserTrackRefRow.user_id == row.owner,
            UserTrackRefRow.deleted_at.is_(None),
        )
    )
    links: dict[UUID, UUID] = {}
    audible: set[UUID] = set()
    for key, ref, already_linked in entries:
        recording = exact.get(key)
        if recording is None:
            continue
        if ref.recording_id is None and already_linked is None:
            links[ref.user_track_ref_id] = recording
        if (ref.recording_id or already_linked or recording) in playable:
            audible.add(ref.user_track_ref_id)
    if links:
        session.execute(
            insert(ImportedTrackReferenceRow).on_conflict_do_nothing(),
            [
                {"user_track_ref_id": ref, "source_recording_id": recording}
                for ref, recording in links.items()
            ],
        )
    if row.action != "PLAYLIST":
        for entry in session.scalars(
            select(LibraryEntryRow).where(
                LibraryEntryRow.user_id == row.owner,
                LibraryEntryRow.user_track_ref_id.in_(audible),
                LibraryEntryRow.removed_at.is_(None),
                LibraryEntryRow.availability_status == "PENDING",
            )
        ):
            entry.availability_status = "VAULT"
            entry.updated_at = datetime.now(UTC)
            entry.row_version += 1
            _sync(
                session,
                row.owner,
                "LIBRARY_ENTRY",
                entry.library_entry_id,
                {
                    "source": entry.source,
                    "availability_status": "VAULT",
                    "personal_save": True,
                    "server_user_track_ref_id": str(entry.user_track_ref_id),
                },
                entry.row_version,
            )
        enqueue_authorized_recordings(session, row.owner, set(exact.values()) - playable)
    refresh_playlist_catalog_handles(session, row.owner)


def _sync(
    session: Session, owner: UUID, kind: str, key: UUID, payload: dict[str, Any], version: int = 1
) -> None:
    session.add(
        SyncEventRow(
            user_id=owner,
            event_type="EXTERNAL_COLLECTION_PROJECTED",
            schema_version=1,
            aggregate_type=kind,
            aggregate_id=key,
            operation="UPSERT",
            server_row_version=version,
            payload=payload,
        )
    )


def _edition(track: dict[str, Any], provider: str, now: datetime) -> dict[str, Any]:
    fields = {
        "title": track["title"],
        "artist": track["artist"],
        "album": track["album"],
        "album_artist": track["album_artist"],
        "track_number": track["number"],
        "disc_number": track["disc"],
    }
    key = f"native:{provider}:album:{track['album_id']}"
    proof = {
        "source": "SOURCE_NATIVE",
        "source_id": key,
        "observed_at": now.isoformat(),
        "locked": False,
        "normalization_version": "1",
    }
    return {
        "fields": fields,
        "provenance": {k: proof for k, v in fields.items() if v is not None},
        "album_group_v1": {
            "schema_version": 1,
            "key": key,
            "provider": provider.upper(),
            "release_id": track["album_id"],
            "title": track["album"],
            "album_artist": track["album_artist"],
            "release_date": None,
            "disc_number": track["disc"],
            "track_number": track["number"],
            "evidence": proof,
        },
    }


def project_collection(session: Session, row: CollectionImportRow) -> None:
    owner, tracks = row.owner, row.snapshot["tracks"]
    now = datetime.now(UTC)
    exact, playable = recording_matches(session, row)
    evidence_provider = "autplay" if row.snapshot.get("catalog_selection") else row.provider
    if row.action == "ALBUM":
        # A recording can occupy two editions and two positions even within one edition.
        keys = [uuid5(row.collection_id, f"medium:{t['disc']}:{t['number']}") for t in tracks]
    else:
        keys = [
            track_key(owner, evidence_provider, t) or uuid5(row.collection_id, f"unidentified:{i}")
            for i, t in enumerate(tracks)
        ]
    unique = dict(zip(keys, tracks, strict=True))
    existing = {
        ref.user_track_ref_id: ref
        for ref in session.scalars(
            select(UserTrackRefRow).where(
                UserTrackRefRow.user_id == owner, UserTrackRefRow.user_track_ref_id.in_(unique)
            )
        )
    }
    # Reuse a personally resolved recording, including its manually edited preference.
    resolved = {
        r.recording_id: r
        for r in session.scalars(
            select(UserTrackRefRow).where(
                UserTrackRefRow.user_id == owner,
                UserTrackRefRow.recording_id.in_(set(exact.values())),
                UserTrackRefRow.deleted_at.is_(None),
            )
        )
    }
    refs = {}
    created = []
    for key, t in unique.items():
        ref = existing.get(key)
        if ref is None and row.action != "ALBUM" and (recording_id := exact.get(t["track_id"])):
            ref = resolved.get(recording_id)
        if ref is None:
            ref = UserTrackRefRow(
                user_track_ref_id=key,
                user_id=owner,
                raw_title=t["title"],
                raw_artist=t["artist"],
                raw_album=t["album"],
                raw_duration_ms=t["duration_ms"],
            )
            session.add(ref)
            created.append((ref, t))
        refs[key] = ref.user_track_ref_id
    session.flush()
    for ref, t in created:
        recording = exact.get(t["track_id"])
        payload: dict[str, Any] = {
            "title": ref.raw_title,
            "artist": ref.raw_artist,
            "album": ref.raw_album,
            "duration_ms": ref.raw_duration_ms,
            "recording_id": str(recording) if recording in playable else None,
            "resolution_status": "RESOLVED" if recording in playable else ref.resolution_status,
        }
        if recording:
            session.add(
                ImportedTrackReferenceRow(
                    user_track_ref_id=ref.user_track_ref_id, source_recording_id=recording
                )
            )
        if row.action == "ALBUM" and t["album"] and t["album_id"]:
            metadata = TrackMetadataRow(
                user_track_ref_id=ref.user_track_ref_id,
                document=_edition(t, evidence_provider, now),
                state="READY",
                revision=1,
                updated_at=now,
                candidates=[],
            )
            session.add(metadata)
            payload["metadata_v1"] = metadata_view(metadata)
        _sync(session, owner, "USER_TRACK_REF", ref.user_track_ref_id, payload)

    if row.action == "PLAYLIST":
        _playlist(session, row, [refs[k] for k in keys])
    else:
        active = {refs[k] for k in unique if not (k in existing and existing[k].deleted_at)}
        library = set(
            session.scalars(
                select(LibraryEntryRow.user_track_ref_id).where(
                    LibraryEntryRow.user_id == owner, LibraryEntryRow.user_track_ref_id.in_(active)
                )
            )
        )
        preferences = (
            set(
                session.scalars(
                    select(UserTrackPreferenceRow.user_track_ref_id).where(
                        UserTrackPreferenceRow.user_track_ref_id.in_(active)
                    )
                )
            )
            if row.action == "FAVORITES"
            else set()
        )
        for key, t in unique.items():
            ref_id = refs[key]
            if ref_id not in active:
                continue
            if ref_id not in library:
                entry_id = uuid5(owner, f"external-library:{ref_id}")
                availability = "VAULT" if exact.get(t["track_id"]) in playable else "PENDING"
                session.add(
                    LibraryEntryRow(
                        library_entry_id=entry_id,
                        user_id=owner,
                        user_track_ref_id=ref_id,
                        source="IMPORT",
                        availability_status=availability,
                    )
                )
                _sync(
                    session,
                    owner,
                    "LIBRARY_ENTRY",
                    entry_id,
                    {
                        "source": "IMPORT",
                        "availability_status": availability,
                        "server_user_track_ref_id": str(ref_id),
                        "personal_save": True,
                    },
                )
                library.add(ref_id)
            if row.action == "FAVORITES" and ref_id not in preferences and t["track_id"]:
                session.add(
                    UserTrackPreferenceRow(
                        user_track_ref_id=ref_id, preference="LIKED", excluded_from_taste=True
                    )
                )
                _sync(
                    session,
                    owner,
                    "USER_TRACK_PREFERENCE",
                    ref_id,
                    {
                        "server_user_track_ref_id": str(ref_id),
                        "preference": "LIKED",
                        "rating": None,
                        "excluded_from_taste": True,
                        "updated_at_ms": int(now.timestamp() * 1000),
                    },
                )
                preferences.add(ref_id)
    # The job and collection commit together. Row keys represent occurrences, including duplicates.
    parsed_rows = tuple(
        ParsedImportRow(
            f"{i:08d}",
            i + 1,
            {
                **t,
                "provider_key": evidence_provider,
                "familiar_from_import": row.action == "FAVORITES",
            },
            t["title"] or "",
            t["artist"] or "",
            t["album"],
            t["duration_ms"],
            t["track_id"],
            "UNAVAILABLE" if t["unavailable"] else "UNKNOWN",
            error_code=None if t["title"] and t["artist"] else "import.metadata_missing",
        )
        for i, t in enumerate(tracks)
    )
    envelope = ImportEnvelope(
        ImportFormat.JSON,
        row.operation_id.bytes
        + hashlib.sha256(json.dumps(tracks, sort_keys=True).encode()).digest(),
        adapter_id=f"collection.{row.provider}",
    )
    result = PostgresImportRepository(session).start(
        owner_user_id=owner,
        parsed=ParsedImport(envelope, "utf-8", parsed_rows),
        mode="LIBRARY_ONLY",
    )
    row.import_job_id = result.import_job_id
    session.flush()
    for entry in session.scalars(
        select(ImportEntryRow).where(ImportEntryRow.import_job_id == row.import_job_id)
    ):
        entry.user_track_ref_id = refs[keys[int(entry.source_row_key)]]


def _playlist(session: Session, row: CollectionImportRow, refs: list[UUID]) -> None:
    playlist = session.get(PlaylistRow, row.collection_id)
    if playlist is None:
        playlist = PlaylistRow(
            playlist_id=row.collection_id,
            owner_user_id=row.owner,
            name=row.snapshot["name"],
            visibility="PRIVATE",
            playlist_type="MANUAL",
        )
        session.add(playlist)
        session.flush()
        _sync(
            session,
            row.owner,
            "PLAYLIST",
            row.collection_id,
            {
                "name": playlist.name,
                "description": None,
                "visibility": "PRIVATE",
                "playlist_type": "MANUAL",
            },
        )
    elif playlist.deleted_at:
        raise CollectionError("collection_removed", 409)
    members = list(
        session.scalars(
            select(PlaylistEntryRow).where(PlaylistEntryRow.playlist_id == row.collection_id)
        )
    )
    indexed = {m.playlist_entry_id: m for m in members}
    active = sorted((m for m in members if not m.removed_at), key=lambda m: m.position_key)
    prior = session.scalar(
        select(CollectionImportRow)
        .where(
            CollectionImportRow.owner == row.owner,
            CollectionImportRow.collection_id == row.collection_id,
            CollectionImportRow.state == "SAVED",
        )
        .order_by(CollectionImportRow.created_at.desc())
        .limit(1)
    )
    if (
        prior
        and playlist.name == prior.snapshot.get("name")
        and playlist.name != row.snapshot["name"]
    ):
        playlist.name = row.snapshot["name"]
        playlist.row_version += 1
        _sync(
            session,
            row.owner,
            "PLAYLIST",
            row.collection_id,
            {
                "name": playlist.name,
                "description": playlist.description,
                "visibility": playlist.visibility,
                "playlist_type": playlist.playlist_type,
            },
            playlist.row_version,
        )
    seen: Counter[str] = Counter()
    positions: dict[UUID, UUID] = {}
    for index, (ref, item) in enumerate(zip(refs, row.snapshot["tracks"], strict=True)):
        identity = item["track_id"] or f"unidentified:{index}"
        seen[identity] += 1
        entry_id = uuid5(row.collection_id, f"occurrence:{identity}:{seen[identity]}")
        positions[entry_id] = ref
    saved_order = (prior.snapshot.get("source_order") if prior else []) or []
    edited = [str(m.playlist_entry_id) for m in active] != saved_order
    new_ids = [key for key in positions if key not in indexed]
    current_ids = [m.playlist_entry_id for m in active]
    if edited:
        order = current_ids + new_ids
    else:
        order = [key for key in positions if key not in indexed or not indexed[key].removed_at]
        order.extend(key for key in current_ids if key not in positions)
    for index, entry_id in enumerate(order):
        position = f"{index:08d}:{entry_id}"
        entry = indexed.get(entry_id)
        if entry is None:
            entry = PlaylistEntryRow(
                playlist_entry_id=entry_id,
                playlist_id=row.collection_id,
                user_track_ref_id=positions[entry_id],
                added_by_user_id=row.owner,
                position_key=position,
            )
            session.add(entry)
            version = 1
        elif entry.position_key != position:
            entry.position_key, entry.row_version = position, entry.row_version + 1
            version = entry.row_version
        else:
            continue
        _sync(
            session,
            row.owner,
            "PLAYLIST_ENTRY",
            entry_id,
            {
                "server_playlist_id": str(row.collection_id),
                "server_user_track_ref_id": str(entry.user_track_ref_id),
                "position_key": position,
            },
            version,
        )
    expected_order = list(positions) if edited else order
    row.snapshot = {**row.snapshot, "source_order": [str(x) for x in expected_order]}
