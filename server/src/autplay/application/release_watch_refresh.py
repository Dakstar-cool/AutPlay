"""Bounded CPU-only favorite refresh ahead of the discovery transaction."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.release_watch import (
    FavoriteEvidenceOverflow,
    ReleaseWatchRepository,
)
from autplay.domain.release_watch import ALGORITHM_VERSION, REFRESH_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FavoriteSweep:
    attempted: int
    refreshed: int
    rejected: int


class ScheduledFavoriteRefresh:
    """One owner transaction at a time; durable backoff follows bounded refusal.

    Oldest due owners run first, with UUID tie breaking. Successful refreshes
    persist the daily cadence; failures do not masquerade as success.
    Owner locks serialize refresh, follow and account deletion. SKIP LOCKED keeps
    another worker or a deleting owner from blocking this sweep.
    """

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def run(self, *, limit: int = 20) -> FavoriteSweep:
        if not 1 <= limit <= 20:
            raise ValueError("favorite sweep limit must be 1..20")
        attempted: list[UUID] = []
        refreshed = rejected = 0
        for _ in range(limit):
            owner_id: UUID | None = None
            with self._sessions() as session, session.begin():
                # Obtain the authoritative instant anew for every owner.
                now = session.scalar(text("SELECT clock_timestamp()"))
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise ValueError("database clock must be timezone aware")
                owner_id = session.scalar(
                    text("""
                            SELECT owner.user_id FROM account.user_account owner
                            LEFT JOIN library.favorite_artist_refresh r
                              ON r.user_id=owner.user_id
                            LEFT JOIN library.favorite_artist_refresh_retry retry
                              ON retry.user_id=owner.user_id
                            WHERE owner.status='ACTIVE' AND owner.deleted_at IS NULL
                              AND NOT (owner.user_id=ANY(CAST(:attempted AS uuid[])))
                              AND (retry.user_id IS NULL OR retry.retry_after<=:now)
                              AND (r.user_id IS NULL OR r.algorithm_version<>:algorithm
                                   OR r.refreshed_at<=:stale OR r.refreshed_at>:now)
                            ORDER BY COALESCE(retry.retry_after,
                              r.refreshed_at+INTERVAL '24 hours',
                              '-infinity'::timestamptz),owner.user_id LIMIT 1
                            FOR UPDATE OF owner SKIP LOCKED
                        """),
                    {
                        "attempted": attempted,
                        "algorithm": ALGORITHM_VERSION,
                        "stale": now - REFRESH_INTERVAL,
                        "now": now,
                    },
                )
                if owner_id is None:
                    break
                attempted.append(owner_id)
                # The candidate snapshot can precede another worker's commit.
                # Re-read time after acquiring the owner lock so that its newer
                # successful refresh is not mistaken for future-dated evidence.
                now = session.scalar(text("SELECT clock_timestamp()"))
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise ValueError("database clock must be timezone aware")
                # Rechecks cadence under the same owner lock. A concurrent
                # worker cannot publish a duplicate generation after waiting.
                try:
                    with session.begin_nested():
                        ReleaseWatchRepository(session).refresh_favorites(owner_id, now=now)
                except FavoriteEvidenceOverflow:
                    session.execute(
                        text("""
                            INSERT INTO library.favorite_artist_refresh_retry
                              (user_id,attempted_at,retry_after)
                            VALUES(:owner,:now,:retry_after)
                            ON CONFLICT (user_id) DO UPDATE SET
                              attempted_at=EXCLUDED.attempted_at,
                              retry_after=EXCLUDED.retry_after
                        """),
                        {"owner": owner_id, "now": now, "retry_after": now + REFRESH_INTERVAL},
                    )
                    rejected += 1
                    _LOGGER.warning("favorite_refresh_evidence_limit")
                else:
                    session.execute(
                        text(
                            "DELETE FROM library.favorite_artist_refresh_retry WHERE user_id=:owner"
                        ),
                        {"owner": owner_id},
                    )
                    refreshed += 1
        return FavoriteSweep(len(attempted), refreshed, rejected)
