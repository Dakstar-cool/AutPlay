"""Application-owned transactions for A1B manual bulk preview and start."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.discovery_runtime import (
    BulkPreviewResult,
    BulkStartResult,
    PostgresBulkDiscoveryRepository,
)
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    ProviderArtistTracks,
)
from autplay.domain.web_admin import WebActor


class BulkDiscoveryService:
    """Keep Web transport outside durable preview/start transactions."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def save_preview(
        self,
        actor: WebActor,
        *,
        import_job_id: UUID,
        operation_id: UUID,
        resolutions: tuple[BulkArtistResolution, ...],
        pages: tuple[ProviderArtistTracks, ...],
    ) -> BulkPreviewResult:
        with self._sessions() as session:
            result = PostgresBulkDiscoveryRepository(session).save_preview(
                owner_user_id=actor.user_id,
                import_job_id=import_job_id,
                operation_id=operation_id,
                resolutions=resolutions,
                pages=pages,
            )
            session.commit()
            return result

    def require_eligible_artists(self, actor: WebActor, artist_names: tuple[str, ...]) -> None:
        """Authorize selected canonical artists before provider metadata I/O."""

        with self._sessions() as session:
            PostgresBulkDiscoveryRepository(session).require_eligible_artists(
                owner_user_id=actor.user_id, artist_names=artist_names
            )

    def require_provider_available(self, actor: WebActor) -> None:
        """Authorize the configured adapter immediately before provider I/O."""

        del actor
        with self._sessions() as session:
            PostgresBulkDiscoveryRepository(session).require_provider_available()

    def start(
        self,
        actor: WebActor,
        *,
        bulk_operation_id: UUID,
        operation_id: UUID,
    ) -> BulkStartResult:
        with self._sessions() as session:
            result = PostgresBulkDiscoveryRepository(session).start(
                owner_user_id=actor.user_id,
                bulk_operation_id=bulk_operation_id,
                operation_id=operation_id,
            )
            session.commit()
            return result

    def start_search_acquisition(
        self,
        actor: WebActor,
        *,
        operation_id: UUID,
        evidence: DiscoveryCandidate,
    ) -> BulkStartResult:
        """Convert an explicit search selection into the same durable Vault-first queue."""

        with self._sessions() as session:
            result = PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=actor.user_id,
                operation_id=operation_id,
                evidence=evidence,
            )
            session.commit()
            return result

    def status(self, actor: WebActor, *, bulk_operation_id: UUID) -> BulkStartResult:
        """Read the current operation state without widening owner scope."""

        with self._sessions() as session:
            return PostgresBulkDiscoveryRepository(session).status(
                owner_user_id=actor.user_id,
                bulk_operation_id=bulk_operation_id,
            )

    def cleanup_expired(
        self,
        *,
        now: datetime,
        limit: int = 10_000,
        retention: timedelta = timedelta(days=30),
    ) -> int:
        """Apply the accepted bounded raw/failed discovery retention policy."""

        with self._sessions() as session:
            deleted = PostgresBulkDiscoveryRepository(session).cleanup_expired(
                now=now,
                limit=limit,
                retention=retention,
            )
            session.commit()
            return deleted


__all__ = ("BulkDiscoveryService",)
