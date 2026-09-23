"""Bounded database-clock cleanup for expired Sona native capture bundles."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import SonaCaptureBundleRow

MAX_CAPTURE_PURGE_BATCH = 100


class SqlAlchemySonaCaptureRetention:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def purge_expired(self, *, limit: int = MAX_CAPTURE_PURGE_BATCH) -> int:
        """Delete at most `limit` expired roots; FK cascades remove all derived evidence."""
        if type(limit) is not int or not 1 <= limit <= MAX_CAPTURE_PURGE_BATCH:
            raise ValueError("Sona capture purge limit is invalid")
        with self._sessions() as session, session.begin():
            ids = tuple(
                session.scalars(
                    select(SonaCaptureBundleRow.recommendation_request_id)
                    .where(SonaCaptureBundleRow.expires_at <= func.clock_timestamp())
                    .order_by(
                        SonaCaptureBundleRow.expires_at,
                        SonaCaptureBundleRow.recommendation_request_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            if ids:
                session.execute(
                    delete(SonaCaptureBundleRow).where(
                        SonaCaptureBundleRow.recommendation_request_id.in_(ids)
                    )
                )
            return len(ids)
