"""Release-watch application commands; subscription never grants acquisition."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.release_watch import (
    ArtistWatch,
    FavoriteRefresh,
    ReleaseWatchRepository,
    WatchMonitoringStatus,
)
from autplay.adapters.postgresql.watch_auto_download_consent import (
    WatchAutoDownloadRepository,
    WatchAutoDownloadState,
)
from autplay.application.release_watch_refresh import FavoriteSweep, ScheduledFavoriteRefresh
from autplay.domain.auth import Principal


class ReleaseWatchService:
    def __init__(self, sessions: Callable[[], Session], clock: Callable[[], datetime]) -> None:
        self._sessions = sessions
        self._clock = clock
        self._scheduled_refresh = ScheduledFavoriteRefresh(sessions)

    def follow(self, principal: Principal, artist_id: UUID) -> int:
        with self._sessions() as session:
            revision = ReleaseWatchRepository(session).set_manual_follow(
                principal.user_id, artist_id, follow=True, now=self._clock()
            )
            session.commit()
            return revision

    def unfollow(self, principal: Principal, artist_id: UUID) -> int:
        with self._sessions() as session:
            revision = ReleaseWatchRepository(session).set_manual_follow(
                principal.user_id, artist_id, follow=False, now=self._clock()
            )
            session.commit()
            return revision

    def followed_artists(
        self, principal: Principal, *, limit: int = 100, after_artist_id: UUID | None = None
    ) -> list[ArtistWatch]:
        with self._sessions() as session:
            return ReleaseWatchRepository(session).list_effective(
                principal.user_id, limit=limit, after_artist_id=after_artist_id
            )

    def excluded_artists(
        self, principal: Principal, *, limit: int = 100, after_artist_id: UUID | None = None
    ) -> list[UUID]:
        with self._sessions() as session:
            return ReleaseWatchRepository(session).list_exclusions(
                principal.user_id, limit=limit, after_artist_id=after_artist_id
            )

    def monitoring_status(self, principal: Principal, artist_id: UUID) -> WatchMonitoringStatus:
        with self._sessions() as session:
            return ReleaseWatchRepository(session).monitoring_status(principal.user_id, artist_id)

    def refresh_from_history(self, owner_id: UUID) -> FavoriteRefresh:
        """Explicit scheduled entry point, separate from follow and listening writes."""
        with self._sessions() as session:
            result = ReleaseWatchRepository(session).refresh_favorites(owner_id, now=self._clock())
            session.commit()
            return result

    def refresh_due_from_history(self, *, limit: int = 20) -> FavoriteSweep:
        """Refresh a fair bounded owner batch without provider or discovery I/O."""
        return self._scheduled_refresh.run(limit=limit)

    def auto_download(self, principal: Principal) -> WatchAutoDownloadState:
        with self._sessions() as session:
            return WatchAutoDownloadRepository(session).get(principal.user_id)

    def set_auto_download(
        self,
        principal: Principal,
        *,
        enabled: bool,
        expected_revision: int,
        confirmation_code: str | None,
    ) -> WatchAutoDownloadState:
        with self._sessions() as session:
            result = WatchAutoDownloadRepository(session).set(
                principal.user_id,
                enabled=enabled,
                expected_revision=expected_revision,
                confirmation_code=confirmation_code,
                now=self._clock(),
            )
            session.commit()
            return result
