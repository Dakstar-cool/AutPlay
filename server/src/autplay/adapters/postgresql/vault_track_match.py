"""Match search selections to playable Vault recordings before fetching provider audio."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.shared_catalog import PUBLISHED_AUDIO, ensure_catalog_refs
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.vault_uploads import VaultNotFoundError, VaultPrincipal
from autplay.domain.import_identity import normalize_text

# Catalogues round to whole seconds and encoders can add leading/trailing padding.
DURATION_TOLERANCE_MS = 2000


def same_track(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    duration = left.get("duration_ms")
    other = right.get("duration_ms")
    if not isinstance(duration, int) or not isinstance(other, int) or min(duration, other) <= 0:
        return False
    if abs(duration - other) > DURATION_TOLERANCE_MS:
        return False
    for field in ("artist", "title"):
        a, b = left.get(field), right.get(field)
        if not isinstance(a, str) or not isinstance(b, str):
            return False
        key = normalize_text(a)
        if not key or key != normalize_text(b):
            return False
    return True


def _matches(row: Mapping[Any, Any], candidate: Mapping[str, Any]) -> bool:
    version = normalize_text(row["version_text"] or "")
    if version and f" {version} " not in f" {normalize_text(candidate['title'])} ":
        return False
    if same_track(row, candidate):
        return True
    return bool(version) and same_track(dict(row, title=f"{row['title']} {version}"), candidate)


def owned_match(
    session: Session, owner: UUID, candidate: Mapping[str, Any]
) -> tuple[UUID, UUID] | None:
    rows = session.execute(
        text("""
        SELECT ref.user_track_ref_id, v.audio_variant_id, v.duration_ms, r.version_text,
               r.title, a.display_name AS artist, ref.raw_title, ref.raw_artist,
               m.document->'fields'->>'title' AS display_title,
               m.document->'fields'->>'artist' AS display_artist
        FROM library.user_track_ref ref
        JOIN vault.recording_canonical_variant cv USING (recording_id)
        JOIN vault.audio_variant v USING (audio_variant_id, recording_id)
        JOIN catalog.recording r USING (recording_id)
        JOIN catalog.artist_credit a USING (artist_credit_id)
        LEFT JOIN library.track_metadata m USING (user_track_ref_id)
        WHERE ref.user_id=:owner AND ref.deleted_at IS NULL
          AND v.duration_ms BETWEEN :duration - :tolerance AND :duration + :tolerance
          AND EXISTS (SELECT FROM library.library_entry l WHERE l.user_id=:owner
              AND l.user_track_ref_id=ref.user_track_ref_id AND l.removed_at IS NULL)
        ORDER BY abs(v.duration_ms - :duration), ref.user_track_ref_id
    """),
        {"owner": owner, "duration": candidate["duration_ms"], "tolerance": DURATION_TOLERANCE_MS},
    ).mappings()
    vault = PostgresVaultRuntime(session)
    for row in rows:
        pairs = [
            (row["title"], row["artist"]),
            (row["raw_title"], row["raw_artist"]),
            (row["display_title"] or row["raw_title"], row["display_artist"] or row["raw_artist"]),
        ]
        if not any(
            _matches(dict(row, title=title, artist=artist), candidate)
            for title, artist in pairs
            if title and artist
        ):
            continue
        try:
            playable = vault.resolve_owner_playback_variant(owner, row["user_track_ref_id"])
        except VaultNotFoundError:
            continue
        if playable == row["audio_variant_id"]:
            return row["user_track_ref_id"], playable
    return None


def published_match(session: Session, candidate: Mapping[str, Any]) -> RowMapping | None:
    rows = session.execute(
        text(f"""
        SELECT p.*, r.title, a.display_name AS artist, r.version_text, v.duration_ms
        FROM ({PUBLISHED_AUDIO}) p
        JOIN catalog.recording r USING (recording_id)
        JOIN catalog.artist_credit a USING (artist_credit_id)
        JOIN vault.audio_variant v USING (audio_variant_id)
        WHERE a.normalized_name=:artist
          AND v.duration_ms BETWEEN :duration - :tolerance AND :duration + :tolerance
        ORDER BY abs(v.duration_ms - :duration), p.audio_variant_id
    """),
        {
            "artist": normalize_text(candidate["artist"]),
            "duration": candidate["duration_ms"],
            "tolerance": DURATION_TOLERANCE_MS,
        },
    ).mappings()
    return next((row for row in rows if _matches(row, candidate)), None)


def attach_published_match(
    session: Session, principal: VaultPrincipal, candidate: Mapping[str, Any]
) -> tuple[UUID, UUID] | None:
    match = published_match(session, candidate)
    if match is None:
        return None
    with session.begin_nested() as attempt:
        (ref,) = ensure_catalog_refs(session, principal.user_id, [match])
        try:
            playable = PostgresVaultRuntime(session).resolve_playback_variant(principal, ref)
        except VaultNotFoundError:
            attempt.rollback()
            return None
        if playable != match["audio_variant_id"]:
            attempt.rollback()
            return None
        return ref, playable
