"""Publication and account-specific playback references for the server music catalog."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

# The operator publishes an exact variant. A later canonical replacement needs its own publication.
PUBLISHED_AUDIO = """
    SELECT x.external_reference_id, x.recording_id, c.audio_variant_id
    FROM identity.source_provider p
    JOIN identity.external_reference x USING (provider_id)
    JOIN vault.recording_canonical_variant c ON c.recording_id = x.recording_id
        AND c.audio_variant_id::text = x.external_id
    JOIN vault.audio_variant v ON v.audio_variant_id = c.audio_variant_id
        AND v.recording_id = c.recording_id AND v.validation_status = 'VALID'
        AND v.deleted_at IS NULL
    JOIN catalog.recording r ON r.recording_id = x.recording_id AND r.deleted_at IS NULL
    JOIN vault.vault_object o ON o.vault_object_id = v.vault_object_id
        AND o.commit_status = 'COMMITTED'
    WHERE p.provider_key = 'autplay.server-catalog' AND p.enabled AND p.deleted_at IS NULL
      AND 'STREAM' = ANY(p.capabilities) AND x.deleted_at IS NULL
      AND x.external_entity_type = 'AUDIO_VARIANT' AND x.market_scope = 'GLOBAL'
      AND NOT EXISTS (SELECT FROM identity.recording_redirect rr
                      WHERE rr.source_recording_id = r.recording_id)
      AND EXISTS (SELECT FROM vault.vault_replica vr WHERE vr.vault_object_id = o.vault_object_id
          AND vr.replica_status = 'AVAILABLE' AND vr.storage_backend = 'LOCAL_FILESYSTEM'
          AND vr.verified_at IS NOT NULL)
"""

# The owner must still hold a playable library entry for this recording.
OWNER_AUDIO = """
    SELECT DISTINCT ref.user_id, ref.recording_id, cv.audio_variant_id
    FROM library.user_track_ref ref
    JOIN library.library_entry le ON le.user_track_ref_id=ref.user_track_ref_id
      AND le.user_id=ref.user_id
    JOIN vault.recording_canonical_variant cv ON cv.recording_id=ref.recording_id
    JOIN vault.audio_variant v ON v.audio_variant_id=cv.audio_variant_id
      AND v.recording_id=cv.recording_id
    JOIN vault.vault_object o ON o.vault_object_id=v.vault_object_id
    JOIN catalog.recording r ON r.recording_id=ref.recording_id
    WHERE ref.resolution_status='RESOLVED' AND ref.deleted_at IS NULL AND le.removed_at IS NULL
      AND v.validation_status='VALID' AND v.deleted_at IS NULL AND o.commit_status='COMMITTED'
      AND r.deleted_at IS NULL
      AND NOT EXISTS (SELECT FROM identity.recording_redirect rr
        WHERE rr.source_recording_id=r.recording_id)
      AND EXISTS (SELECT FROM vault.vault_replica vr WHERE vr.vault_object_id=o.vault_object_id
        AND vr.replica_status='AVAILABLE' AND vr.storage_backend='LOCAL_FILESYSTEM'
          AND vr.verified_at IS NOT NULL)
"""

# A catalog download confirms the owner's choice of audio for this public recording.
# A free-form source search by itself does not establish that association.
OWNER_CATALOG_DOWNLOAD = f"""
    SELECT a.user_id, ctx.card->>'recording_mbid' AS mbid, own.recording_id, own.audio_variant_id
    FROM discovery.internet_acquisition a
      JOIN discovery.internet_search_context sc USING (user_id, search_id)
      JOIN discovery.internet_catalogue_context ctx USING (user_id, context_id)
      JOIN library.user_track_ref source ON source.user_track_ref_id=a.user_track_ref_id
        AND source.user_id=a.user_id AND source.deleted_at IS NULL
      JOIN ({OWNER_AUDIO}) own ON own.user_id=a.user_id AND own.recording_id=source.recording_id
    WHERE a.state='READY' AND EXISTS (
        SELECT FROM discovery.catalog_download op WHERE op.user_id=a.user_id
          AND op.document->'recordings'->(ctx.card->>'recording_mbid')->>'acquisition'
            = a.acquisition_id::text)
"""

ACCOUNT_CATALOG = f"""
    SELECT u.user_id, u.user_track_ref_id, published.recording_id, published.audio_variant_id
    FROM library.user_track_ref u
    JOIN library.user_track_ref_external_reference link USING (user_track_ref_id)
    JOIN LATERAL (
        SELECT recording_id, audio_variant_id FROM ({PUBLISHED_AUDIO}) candidate
        WHERE candidate.external_reference_id = link.external_reference_id
        LIMIT 1
    ) published ON true
    WHERE link.relation_role = 'PRIMARY_SOURCE' AND u.deleted_at IS NULL
      AND (u.recording_id IS NULL OR u.recording_id = published.recording_id)
    UNION
    SELECT u.user_id, u.user_track_ref_id, playable.recording_id, playable.audio_variant_id
    FROM library.user_track_ref u
      JOIN library.user_track_ref_external_reference link USING(user_track_ref_id)
    JOIN identity.external_reference x USING(external_reference_id)
    JOIN identity.source_provider sp USING(provider_id)
    JOIN LATERAL (
        SELECT recording_id, audio_variant_id FROM ({OWNER_AUDIO}) own
         WHERE own.user_id=u.user_id AND own.recording_id=x.recording_id
        UNION SELECT recording_id, audio_variant_id FROM ({PUBLISHED_AUDIO}) pub
          WHERE pub.recording_id=x.recording_id
        UNION SELECT recording_id, audio_variant_id FROM ({OWNER_CATALOG_DOWNLOAD}) download
          WHERE download.user_id=u.user_id AND download.mbid=x.external_id
            AND (x.recording_id IS NULL OR x.recording_id=download.recording_id)
    ) playable ON true
    WHERE u.deleted_at IS NULL AND (u.recording_id IS NULL OR u.recording_id=playable.recording_id)
      AND link.relation_role='IMPORT_EVIDENCE' AND sp.provider_key='musicbrainz'
        AND sp.deleted_at IS NULL
      AND x.external_entity_type='RECORDING' AND x.deleted_at IS NULL
    UNION
    SELECT u.user_id, u.user_track_ref_id, a.recording_id, a.audio_variant_id
    FROM playlist.imported_track_reference imported
    JOIN library.user_track_ref u USING(user_track_ref_id)
    JOIN LATERAL (
        SELECT recording_id, audio_variant_id FROM ({OWNER_AUDIO}) own
          WHERE own.user_id=u.user_id AND own.recording_id=imported.source_recording_id
        UNION SELECT recording_id, audio_variant_id FROM ({PUBLISHED_AUDIO}) published
          WHERE published.recording_id=imported.source_recording_id
    ) a ON true
    WHERE u.deleted_at IS NULL AND u.recording_id IS NULL
"""


def enable_bridge_catalog(session: Session) -> int:
    """Explicit operator opt-in, including eligible tracks already on the server."""
    session.execute(
        text("""
        INSERT INTO identity.source_provider
            (provider_key,display_name,adapter_id,adapter_version,capabilities)
        VALUES ('autplay.server-catalog','Server music catalog','autplay.server-catalog',
                '1',ARRAY['METADATA','STREAM'])
        ON CONFLICT (provider_key) DO NOTHING
    """)
    )
    return publish_bridge_catalog(session)


def publish_bridge_catalog(session: Session, recording_id: UUID | None = None) -> int:
    """Publish operator-bridge imports only when the operator enabled this source."""
    result = session.execute(
        text("""
        WITH added AS (
            INSERT INTO identity.external_reference
                (provider_id, external_entity_type, external_id, recording_id)
            SELECT p.provider_id, 'AUDIO_VARIANT', c.audio_variant_id::text, c.recording_id
            FROM identity.source_provider p CROSS JOIN vault.recording_canonical_variant c
            WHERE p.provider_key='autplay.server-catalog' AND p.enabled AND p.deleted_at IS NULL
              AND 'STREAM'=ANY(p.capabilities) AND c.policy_version='acquisition-verified-upload-v1'
              AND (CAST(:recording AS uuid) IS NULL OR c.recording_id=CAST(:recording AS uuid))
            ON CONFLICT ON CONSTRAINT uq_external_reference_namespace DO NOTHING
            RETURNING 1
        ) SELECT count(*) FROM added
    """),
        {"recording": recording_id},
    )
    return int(result.scalar_one())


def prepare_catalog_wave(session: Session, user_id: UUID) -> None:
    """Warm a bounded rotating sample through ordinary sync, without creating library entries."""
    from autplay.application.sync import _acquire_sync_owner_publish_lock

    # The first successful warm-up is atomic. Subsequent pulls need only an indexed owner lookup.
    ready = session.scalar(
        text("""
        SELECT EXISTS (
            SELECT FROM library.user_track_ref u
            JOIN library.user_track_ref_external_reference link USING (user_track_ref_id)
            JOIN identity.external_reference x USING (external_reference_id)
            JOIN identity.source_provider p USING (provider_id)
            WHERE u.user_id=:owner AND u.deleted_at IS NULL
              AND p.provider_key='autplay.server-catalog'
              AND u.created_at >= date_trunc('day', now(), 'UTC')
        )
    """),
        {"owner": user_id},
    )
    if ready:
        return
    # Read just today's sample. Existing handles remain valid for playback history and playlists.
    picks = (
        session.execute(
            text(f"""
        SELECT published.*, r.title, ac.display_name AS artist, v.duration_ms
        FROM ({PUBLISHED_AUDIO}) published
        JOIN catalog.recording r USING(recording_id)
        JOIN catalog.artist_credit ac USING(artist_credit_id)
        JOIN vault.audio_variant v USING(audio_variant_id)
        ORDER BY md5(published.recording_id::text || (now() AT TIME ZONE 'UTC')::date::text)
        LIMIT 96
    """)
        )
        .mappings()
        .all()
    )
    if not picks:
        return
    _acquire_sync_owner_publish_lock(session, user_id)
    ensure_catalog_refs(session, user_id, picks)


def ensure_catalog_refs(session: Session, user_id: UUID, picks: Sequence[RowMapping]) -> list[UUID]:
    """Publish account-scoped handles under the caller's owner publication lock."""
    refs = [
        uuid5(user_id, f"autplay-server-catalog:{pick['external_reference_id']}") for pick in picks
    ]
    present = set(
        session.scalars(
            text("""
        SELECT user_track_ref_id FROM library.user_track_ref WHERE user_track_ref_id=ANY(:ids)
    """),
            {"ids": refs},
        )
    )
    entries: list[dict[str, Any]] = []
    for ref, pick in zip(refs, picks, strict=True):
        if ref not in present:
            entries.append(dict(pick, ref=ref, owner=user_id))
    if not entries:
        return refs
    session.execute(
        text("""
        INSERT INTO library.user_track_ref
            (user_track_ref_id,user_id,raw_title,raw_artist,raw_duration_ms)
        VALUES (:ref,:owner,:title,:artist,:duration_ms)
    """),
        entries,
    )
    session.execute(
        text("""
        INSERT INTO library.user_track_ref_external_reference
            (user_track_ref_id,external_reference_id,relation_role)
        VALUES (:ref,:external_reference_id,'PRIMARY_SOURCE')
    """),
        entries,
    )
    session.execute(
        text("""
        INSERT INTO sync.sync_event(user_id,event_type,schema_version,aggregate_type,
                                    aggregate_id,server_row_version,operation,payload)
        VALUES (:owner,'USER_TRACK_REF_PATCHED',1,'USER_TRACK_REF',:ref,1,'UPSERT',
            jsonb_build_object('recording_id',CAST(:recording_id AS text),
                'title',CAST(:title AS text),
                'artist',CAST(:artist AS text),'duration_ms',CAST(:duration_ms AS bigint),
                'resolution_status','RESOLVED'))
    """),
        entries,
    )
    return refs


def playlist_catalog_handles(session: Session, owner: UUID) -> dict[UUID, dict[str, Any]]:
    """Resolution hints for saved MBIDs, scoped to currently authorized audio."""
    rows = session.execute(
        text(f"""
        SELECT u.user_track_ref_id, x.external_id, visible.recording_id
        FROM library.user_track_ref u
          JOIN library.user_track_ref_external_reference link USING(user_track_ref_id)
        JOIN identity.external_reference x USING(external_reference_id)
        JOIN identity.source_provider sp USING(provider_id)
        LEFT JOIN LATERAL (SELECT recording_id FROM ({ACCOUNT_CATALOG}) a
            WHERE a.user_id=u.user_id AND a.user_track_ref_id=u.user_track_ref_id
            ORDER BY recording_id, audio_variant_id LIMIT 1) visible
              ON true
        WHERE u.user_id=:owner AND u.deleted_at IS NULL AND u.recording_id IS NULL
          AND link.relation_role='IMPORT_EVIDENCE' AND x.deleted_at IS NULL
          AND x.external_entity_type='RECORDING' AND sp.provider_key='musicbrainz'
            AND sp.deleted_at IS NULL
    """),
        {"owner": owner},
    )
    handles = {
        ref: {
            "catalogue_recording_mbid": mbid,
            "recording_id": str(recording) if recording else None,
            "resolution_status": "RESOLVED" if recording else "CATALOG_ONLY",
        }
        for ref, mbid, recording in rows
    }
    imported = session.execute(
        text(f"""
        SELECT u.user_track_ref_id, visible.recording_id
        FROM playlist.imported_track_reference i
        JOIN library.user_track_ref u USING(user_track_ref_id)
        LEFT JOIN LATERAL (SELECT recording_id FROM ({ACCOUNT_CATALOG}) a
            WHERE a.user_id=u.user_id AND a.user_track_ref_id=u.user_track_ref_id
            ORDER BY recording_id, audio_variant_id LIMIT 1) visible ON true
        WHERE u.user_id=:owner AND u.deleted_at IS NULL AND u.recording_id IS NULL
    """),
        {"owner": owner},
    )
    for ref, recording in imported:
        handles.setdefault(
            ref,
            {
                "catalogue_recording_mbid": None,
                "recording_id": str(recording) if recording else None,
                "resolution_status": "RESOLVED" if recording else "CATALOG_ONLY",
            },
        )
    return handles


def refresh_playlist_catalog_handles(session: Session, owner: UUID) -> None:
    from autplay.application.sync import _acquire_sync_owner_publish_lock

    handles = playlist_catalog_handles(session, owner)
    if not handles:
        return
    _acquire_sync_owner_publish_lock(session, owner)
    for identity, state in playlist_catalog_handles(session, owner).items():
        latest = session.scalar(
            text("""
            SELECT payload FROM sync.sync_event WHERE user_id=:owner AND aggregate_id=:id
                AND aggregate_type='USER_TRACK_REF' AND payload ? 'catalogue_recording_mbid'
            ORDER BY server_sequence DESC LIMIT 1
        """),
            {"owner": owner, "id": identity},
        )
        if latest is not None and all(latest.get(key) == value for key, value in state.items()):
            continue
        version = session.scalar(
            text("""
            UPDATE library.user_track_ref SET row_version=row_version+1, updated_at=now()
            WHERE user_track_ref_id=:id AND user_id=:owner RETURNING row_version
        """),
            {"id": identity, "owner": owner},
        )

        session.execute(
            text("""
            INSERT INTO
              sync.sync_event(user_id,event_type,schema_version,aggregate_type,aggregate_id,
                server_row_version,operation,payload) VALUES (:owner,'USER_TRACK_REF_PATCHED',1,
                'USER_TRACK_REF',:id,:version,'UPSERT',CAST(:payload AS jsonb))
        """),
            {"owner": owner, "id": identity, "version": version, "payload": json.dumps(state)},
        )
