"""Durable playlist requests consumed by the operator's verified catalog publisher."""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Collection, Iterable
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import UserTrackRefRow
from autplay.adapters.postgresql.shared_catalog import OWNER_AUDIO, PUBLISHED_AUDIO
from autplay.adapters.postgresql.stage_credits import CATALOG_MEMBERS
from autplay.application.job_worker import JobExecutionContext
from autplay.domain.import_identity import normalize_text
from autplay.domain.jobs import JobKey, JobLease, JsonValue, RetryableJobError, TerminalJobError
from autplay.ports.jobs import EnqueueJob

PLAYLIST_TRACK = JobKey("music.playlist.track", 1)
PLAYLIST_ARTIST = JobKey("music.playlist.artist", 1)
PLAYLIST_PLAN = JobKey("music.playlist.plan", 1)
ARTIST_LIMIT = 5


def enqueue_playlist_track(
    session: Session, owner: UUID, playlist: UUID, ref_id: UUID, *, complete: bool
) -> None:
    ref = session.get(UserTrackRefRow, ref_id)
    if ref is None or ref.user_id != owner:
        return
    scope = f"playlist-vault:{playlist}"
    repo = PostgresJobRepository(session)
    repo.enqueue(
        EnqueueJob(
            key=PLAYLIST_TRACK,
            user_id=owner,
            priority=3,
            payload={"playlist": str(playlist), "ref": str(ref_id)},
            idempotency_scope=scope,
            idempotency_key=f"track:{ref_id}",
        )
    )
    if complete:
        repo.enqueue(
            EnqueueJob(
                key=PLAYLIST_PLAN,
                user_id=owner,
                priority=4,
                payload={"playlist": str(playlist)},
                idempotency_scope=scope,
                idempotency_key="plan",
            )
        )


def catalog_match(
    session: Session,
    artist: str,
    title: str,
    duration: int | None = None,
    *,
    owner: UUID | None = None,
) -> UUID | None:
    """Match a single authorized edition. A private library match stays with its owner."""
    audio = PUBLISHED_AUDIO
    if owner is not None:
        audio = f"""SELECT recording_id,audio_variant_id FROM ({PUBLISHED_AUDIO}) pub
            UNION SELECT recording_id,audio_variant_id
            FROM ({OWNER_AUDIO}) own WHERE own.user_id=:owner"""
    rows = (
        session.execute(
            text(f"""
        SELECT DISTINCT p.recording_id FROM ({audio}) p
        JOIN catalog.recording r USING (recording_id)
        JOIN catalog.artist_credit a USING (artist_credit_id)
        JOIN vault.audio_variant v USING (audio_variant_id)
        WHERE a.normalized_name=:artist AND r.normalized_title=:title
          AND coalesce(r.version_text,'')=''
          AND r.recording_kind IN ('STUDIO','UNKNOWN')
          AND (CAST(:duration AS bigint) IS NULL OR abs(v.duration_ms-:duration)<=2000)
        LIMIT 2
    """),
            {
                "artist": normalize_text(artist),
                "title": normalize_text(title),
                "duration": duration,
                "owner": owner,
            },
        )
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def in_vault(session: Session, artist: str, title: str) -> bool:
    return bool(
        session.scalar(
            text("""
        SELECT EXISTS(SELECT FROM catalog.recording r
          JOIN catalog.artist_credit a USING (artist_credit_id)
          JOIN vault.recording_canonical_variant c USING (recording_id)
          JOIN vault.audio_variant v USING (recording_id,audio_variant_id)
          JOIN vault.vault_object o USING (vault_object_id)
          WHERE a.normalized_name=:artist AND r.normalized_title=:title
            AND r.deleted_at IS NULL AND v.deleted_at IS NULL AND v.validation_status='VALID'
            AND o.commit_status='COMMITTED')
    """),
            {"artist": normalize_text(artist), "title": normalize_text(title)},
        )
    )


def choose_candidates(
    rows: Iterable[dict[str, Any]],
    artist: str,
    *,
    title: str | None = None,
    duration: int | None = None,
    excluded: Collection[str] = (),
    present: Callable[[str, str], bool],
) -> list[dict[str, Any]]:
    chosen = []
    seen = set(excluded)
    for row in rows:
        key = normalize_text(row["title"])
        if normalize_text(row["artist"]) != normalize_text(artist) or not key or key in seen:
            continue
        if title is not None:
            if key != normalize_text(title) or (
                duration and abs(row["duration_ms"] - duration) > 2000
            ):
                continue
            chosen.append(row)
            # Multiple provider recordings of the same name are ambiguous without more evidence.
            continue
        if not present(row["artist"], row["title"]):
            chosen.append(row)
        seen.add(key)
        if len(chosen) == ARTIST_LIMIT:
            break
    return chosen if title is None or len(chosen) == 1 else []


_MEMBERS = """
    SELECT u.user_track_ref_id AS ref,u.raw_artist AS artist,u.raw_title AS title,
           u.raw_duration_ms AS duration,coalesce(u.recording_id,i.source_recording_id) AS recording
    FROM playlist.playlist p JOIN playlist.playlist_entry e USING (playlist_id)
    JOIN account.user_account owner ON owner.user_id=p.owner_user_id
    AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
    JOIN library.user_track_ref u USING (user_track_ref_id)
    LEFT JOIN playlist.imported_track_reference i USING (user_track_ref_id)
    WHERE p.playlist_id=:playlist AND p.owner_user_id=:owner AND u.user_id=:owner
      AND p.deleted_at IS NULL AND e.removed_at IS NULL AND u.deleted_at IS NULL
"""


def plan_artists(session: Session, owner: UUID, playlist: UUID) -> None:
    scope = f"playlist-vault:{playlist}"
    if session.scalar(
        text("""SELECT EXISTS(SELECT FROM jobs.job WHERE user_id=:owner
            AND idempotency_scope=:scope AND job_type='music.playlist.artist')"""),
        {"owner": owner, "scope": scope},
    ):
        # All artist jobs commit together. A recovered lease keeps the original selection.
        return
    members = session.execute(
        text(f"""
        SELECT DISTINCT u.raw_artist,u.raw_title,{CATALOG_MEMBERS} AS credits
        FROM ({_MEMBERS}) m JOIN library.user_track_ref u ON u.user_track_ref_id=m.ref
        LEFT JOIN catalog.recording r ON r.recording_id=m.recording
        JOIN jobs.job j ON j.user_id=:owner AND j.idempotency_scope=:scope
          AND j.idempotency_key='track:' || m.ref::text
    """),
        {"owner": owner, "playlist": playlist, "scope": scope},
    ).mappings()
    artists: dict[str, tuple[str, set[str]]] = {}
    for member in members:
        names = [credit["name"] for credit in member["credits"] or ()]
        if not names:
            # A punctuation mark can be part of a band name. Explicit guest credits separate people.
            names = re.split(
                r"\s+(?:feat\.?|ft\.?|featuring)\s+", member["raw_artist"] or "", flags=re.I
            )
        for name in names:
            key = normalize_text(name)
            if key and member["raw_title"]:
                _, titles = artists.setdefault(key, (name, set()))
                titles.add(normalize_text(member["raw_title"]))
    repo = PostgresJobRepository(session)
    for key, (artist, titles) in sorted(artists.items()):
        # Fall back to reading membership for a large discography rather than overflowing a job.
        excluded = sorted(titles)
        payload: dict[str, JsonValue] = {
            "playlist": str(playlist),
            "artist": artist,
            "excluded": [*excluded],
        }
        if len(json.dumps(excluded).encode()) > 180_000:
            payload["excluded"] = None
        repo.enqueue(
            EnqueueJob(
                key=PLAYLIST_ARTIST,
                user_id=owner,
                priority=4,
                payload=payload,
                idempotency_scope=scope,
                idempotency_key="artist:" + hashlib.sha256(key.encode()).hexdigest(),
            )
        )


class AcquireCandidate(Protocol):
    def __call__(
        self, candidate: dict[str, Any], context: JobExecutionContext, *, supplemental: bool
    ) -> UUID | None: ...


class PlaylistVaultHandler:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        search: Callable[[str], Iterable[dict[str, Any]]],
        acquire: AcquireCandidate,
    ) -> None:
        self.sessions, self.search, self.acquire = sessions, search, acquire

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        owner = lease.user_id
        playlist_id = lease.payload.get("playlist")
        if owner is None or not isinstance(playlist_id, str):
            raise TerminalJobError("playlist_catalog_invalid_job")
        params = {"playlist": UUID(playlist_id), "owner": owner}
        if lease.key == PLAYLIST_PLAN:
            with self.sessions.begin() as session:
                plan_artists(session, owner, params["playlist"])
            return
        ref = None
        if lease.key == PLAYLIST_TRACK:
            ref_id = lease.payload.get("ref")
            if not isinstance(ref_id, str):
                raise TerminalJobError("playlist_catalog_missing_ref")
            ref = UUID(ref_id)
        with self.sessions() as session:
            if ref:
                member = (
                    session.execute(
                        text(_MEMBERS + " AND u.user_track_ref_id=:ref"), dict(params, ref=ref)
                    )
                    .mappings()
                    .first()
                )
                if not member or not member["artist"] or not member["title"]:
                    return
                if member["recording"]:
                    from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
                    from autplay.application.vault_uploads import VaultNotFoundError

                    try:
                        PostgresVaultRuntime(session).resolve_owner_playback_variant(
                            owner, ref, include_catalog=True
                        )
                        return
                    except VaultNotFoundError:
                        pass
                artist, title, duration = member["artist"], member["title"], member["duration"]
                recording = catalog_match(session, artist, title, duration, owner=owner)
                excluded: Collection[str] = ()
            else:
                if not session.execute(text(_MEMBERS + " LIMIT 1"), params).first():
                    return
                artist = lease.payload.get("artist")
                if not isinstance(artist, str):
                    raise TerminalJobError("playlist_catalog_missing_artist")
                title, duration, recording = None, None, None
                saved_titles = lease.payload.get("excluded")
                if isinstance(saved_titles, list) and all(isinstance(t, str) for t in saved_titles):
                    excluded = [t for t in saved_titles if isinstance(t, str)]
                elif saved_titles is not None:
                    raise TerminalJobError("playlist_catalog_invalid_exclusions")
                else:
                    excluded = {
                        normalize_text(m.title or "")
                        for m in session.execute(text(_MEMBERS), params)
                    }
        if not recording:
            saved_plan = (lease.checkpoint or {}).get("candidates")
            plan: list[dict[str, Any]]
            if isinstance(saved_plan, list) and all(isinstance(c, dict) for c in saved_plan):
                plan = [c for c in saved_plan if isinstance(c, dict)]
            elif saved_plan is not None:
                raise TerminalJobError("playlist_catalog_invalid_checkpoint")
            else:
                context.raise_if_cancelled()
                try:
                    rows = self.search(" ".join(s for s in (artist, title) if s))
                except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                    raise RetryableJobError("playlist_catalog_search_unavailable") from error
                with self.sessions() as session:
                    plan = choose_candidates(
                        rows,
                        artist,
                        title=title,
                        duration=duration,
                        excluded=excluded,
                        present=lambda a, t: in_vault(session, a, t),
                    )
                if not plan:
                    context.checkpoint(
                        {
                            "candidates": [],
                            "result": "NO_CONFIDENT_MATCH" if ref else "NO_NEW_TRACKS",
                        }
                    )
                    return
                context.checkpoint({"candidates": [*plan]})
            for candidate in plan:
                context.raise_if_cancelled()
                recording = self.acquire(candidate, context, supplemental=ref is None)
        if ref and recording:
            from autplay.application.sync import _acquire_sync_owner_publish_lock

            with self.sessions.begin() as session:
                _acquire_sync_owner_publish_lock(session, owner)
                # Guard deletion/account changes during the download and keep verified share
                # evidence.
                current_member = session.execute(
                    text(_MEMBERS + " AND u.user_track_ref_id=:ref"), dict(params, ref=ref)
                ).first()
                if current_member and current_member.recording is None:
                    session.execute(
                        text("""
                        INSERT INTO playlist.imported_track_reference(
                        user_track_ref_id,source_recording_id)
                        VALUES (:ref,:recording) ON CONFLICT (user_track_ref_id) DO UPDATE
                        SET source_recording_id=EXCLUDED.source_recording_id
                        WHERE playlist.imported_track_reference.source_recording_id IS NULL
                    """),
                        {"ref": ref, "recording": recording},
                    )
                elif current_member:
                    # Keep the original share evidence. An independently acquired public edition
                    # can back an unresolved portable handle without granting access to
                    # private audio.
                    session.execute(
                        text(f"""
                        INSERT INTO library.user_track_ref_external_reference
                            (user_track_ref_id,external_reference_id,relation_role)
                        SELECT u.user_track_ref_id,p.external_reference_id,'PRIMARY_SOURCE'
                        FROM library.user_track_ref u CROSS JOIN ({PUBLISHED_AUDIO}) p
                        WHERE u.user_track_ref_id=:ref AND u.user_id=:owner AND u.deleted_at IS NULL
                          AND (u.recording_id IS NULL OR u.recording_id=p.recording_id)
                          AND p.recording_id=:recording
                        ON CONFLICT DO NOTHING
                    """),
                        {"ref": ref, "owner": owner, "recording": recording},
                    )
