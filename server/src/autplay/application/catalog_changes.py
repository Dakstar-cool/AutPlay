"""Application transaction facade for explicit P10 catalog change sets."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.catalog_changes import (
    CatalogChangeResult,
    PostgresCatalogChangeRepository,
)
from autplay.domain.auth import Principal


class CatalogChangeService:
    """Commit reviewed proposals/apply/undo operations in short transactions."""

    def __init__(self, sessions: Callable[[], Session], clock: Callable[[], datetime]) -> None:
        self._sessions = sessions
        self._clock = clock

    def propose_recording_change(
        self,
        principal: Principal,
        *,
        operation_type: str,
        source_recording_id: UUID,
        target_recording_id: UUID,
        reason: str,
    ) -> CatalogChangeResult:
        with self._sessions() as session:
            result = PostgresCatalogChangeRepository(session).propose_recording_change(
                principal=principal,
                operation_type=operation_type,
                source_recording_id=source_recording_id,
                target_recording_id=target_recording_id,
                reason=reason,
                now=self._clock(),
            )
            session.commit()
            return result

    def propose_release_track_reassign(
        self,
        principal: Principal,
        *,
        release_track_id: UUID,
        target_recording_id: UUID,
        reason: str,
    ) -> CatalogChangeResult:
        with self._sessions() as session:
            result = PostgresCatalogChangeRepository(session).propose_release_track_reassign(
                principal=principal,
                release_track_id=release_track_id,
                target_recording_id=target_recording_id,
                reason=reason,
                now=self._clock(),
            )
            session.commit()
            return result

    def apply(self, principal: Principal, change_set_id: UUID) -> CatalogChangeResult:
        with self._sessions() as session:
            result = PostgresCatalogChangeRepository(session).apply(
                principal=principal,
                change_set_id=change_set_id,
                now=self._clock(),
            )
            session.commit()
            return result

    def undo(self, principal: Principal, change_set_id: UUID) -> CatalogChangeResult:
        with self._sessions() as session:
            result = PostgresCatalogChangeRepository(session).undo(
                principal=principal,
                change_set_id=change_set_id,
                now=self._clock(),
            )
            session.commit()
            return result


__all__ = ("CatalogChangeService",)
