"""Deterministic, owner-local favorite-artist evidence for release monitoring.

Version 1 uses a trailing 90-day window. A play qualifies at 30 seconds and, when
known, at least half the track duration. It requires three qualifying plays on two
canonical recordings. Each recording contributes at most three plays to the score;
recency weights are 1000 (<7 days), 700 (<30 days), and 400 (remaining window).
Sparse history yields no inferred favorites. Refresh is scheduled at most daily.
Names, emotion/Face signals, feed impressions, and training consent are not inputs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

ALGORITHM_VERSION = "favorite_artist_v1"
EVIDENCE_WINDOW = timedelta(days=90)
REFRESH_INTERVAL = timedelta(days=1)
MAX_EVIDENCE_EVENTS = 50_000


@dataclass(frozen=True, slots=True)
class ListeningEvidence:
    event_id: UUID
    artist_id: UUID
    recording_id: UUID
    started_at: datetime


@dataclass(frozen=True, slots=True)
class FavoriteArtist:
    artist_id: UUID
    score_milli: int
    qualified_play_count: int
    distinct_recording_count: int
    last_played_at: datetime
    evidence_sha256: bytes


def evidence_digest(events: list[ListeningEvidence]) -> bytes:
    """Hash canonical IDs and instants, without retaining listening details in rows."""
    digest = sha256()
    ordered = sorted(events, key=lambda e: (e.artist_id, e.recording_id, e.started_at, e.event_id))
    for item in ordered:
        digest.update(item.artist_id.bytes)
        digest.update(item.recording_id.bytes)
        digest.update(item.event_id.bytes)
        digest.update(item.started_at.isoformat().encode("ascii"))
    return digest.digest()


def project_favorites(events: list[ListeningEvidence], *, as_of: datetime) -> list[FavoriteArtist]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone aware")
    if len({event.event_id for event in events}) > MAX_EVIDENCE_EVENTS:
        raise ValueError("favorite evidence exceeds bounded refresh")
    grouped: dict[UUID, list[ListeningEvidence]] = defaultdict(list)
    for event in events:
        if event.started_at.tzinfo is None:
            raise ValueError("evidence instant must be timezone aware")
        if as_of - EVIDENCE_WINDOW <= event.started_at <= as_of:
            grouped[event.artist_id].append(event)
    result: list[FavoriteArtist] = []
    for artist_id, artist_events in grouped.items():
        recordings = {event.recording_id for event in artist_events}
        if len(artist_events) < 3 or len(recordings) < 2:
            continue
        repeat_counts: dict[UUID, int] = defaultdict(int)
        score = 0
        for event in sorted(artist_events, key=lambda e: (e.started_at, e.event_id), reverse=True):
            if repeat_counts[event.recording_id] >= 3:
                continue
            repeat_counts[event.recording_id] += 1
            age = as_of - event.started_at
            score += 1000 if age < timedelta(days=7) else 700 if age < timedelta(days=30) else 400
        result.append(
            FavoriteArtist(
                artist_id=artist_id,
                score_milli=min(score, 1_000_000),
                qualified_play_count=len(artist_events),
                distinct_recording_count=len(recordings),
                last_played_at=max(event.started_at for event in artist_events),
                evidence_sha256=evidence_digest(artist_events),
            )
        )
    return sorted(result, key=lambda item: (-item.score_milli, item.artist_id))
