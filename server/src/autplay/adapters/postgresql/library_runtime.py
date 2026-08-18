"""P07 owner-scoped PostgreSQL commands and read projections."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autplay.application.authorization import require_same_owner
from autplay.domain.auth import OwnedObjectNotFoundError, Principal
from autplay.domain.library import (
    AppendListeningEvent,
    CreateUnresolvedTrack,
    LibraryCommandError,
    PreferenceValue,
    StaleVersionError,
    validate_playlist_name,
    validate_position_key,
)

from .models import (
    LibraryEntryRow,
    ListeningEventRow,
    PlaylistEntryRow,
    PlaylistRow,
    RecommendationRequestRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)


class LibraryRepository:
    """Repository deliberately scoped to an authenticated owner transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_unresolved(
        self, principal: Principal, command: CreateUnresolvedTrack, *, now: datetime
    ) -> UUID:
        row = UserTrackRefRow(
            user_track_ref_id=command.user_track_ref_id,
            user_id=principal.user_id,
            resolution_status="UNRESOLVED",
            raw_title=command.title,
            raw_artist=command.artist,
            raw_album=command.album,
            raw_duration_ms=command.duration_ms,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row.user_track_ref_id

    def add_library_entry(
        self,
        principal: Principal,
        *,
        library_entry_id: UUID,
        user_track_ref_id: UUID,
        source: str,
        availability_status: str,
        now: datetime,
    ) -> UUID:
        self._owned_ref(principal, user_track_ref_id)
        if source not in {"LOCAL", "IMPORT", "SEARCH", "SHARE", "RESTORE"}:
            raise LibraryCommandError("library_source_invalid")
        if availability_status not in {
            "LOCAL",
            "VAULT",
            "EXTERNAL",
            "PENDING",
            "NOT_FOUND",
            "AMBIGUOUS",
        }:
            raise LibraryCommandError("availability_status_invalid")
        existing = self._session.scalar(
            select(LibraryEntryRow).where(
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.user_track_ref_id == user_track_ref_id,
                LibraryEntryRow.removed_at.is_(None),
            )
        )
        if existing is not None:
            return existing.library_entry_id
        row = LibraryEntryRow(
            library_entry_id=library_entry_id,
            user_id=principal.user_id,
            user_track_ref_id=user_track_ref_id,
            source=source,
            availability_status=availability_status,
            added_at=now,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row.library_entry_id

    def remove_library_entry(
        self, principal: Principal, library_entry_id: UUID, *, base_version: int, now: datetime
    ) -> None:
        result = self._session.execute(
            update(LibraryEntryRow)
            .where(
                LibraryEntryRow.library_entry_id == library_entry_id,
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.removed_at.is_(None),
                LibraryEntryRow.row_version == base_version,
            )
            .values(removed_at=now, updated_at=now, row_version=LibraryEntryRow.row_version + 1)
        )
        if cast(int, result.rowcount) == 0:  # type: ignore[attr-defined]
            self._raise_owned_or_stale(
                LibraryEntryRow,
                LibraryEntryRow.library_entry_id,
                library_entry_id,
                principal,
                base_version,
            )

    def restore_library_entry(
        self, principal: Principal, library_entry_id: UUID, *, base_version: int, now: datetime
    ) -> None:
        """Restore a tombstoned intent without creating a duplicate active row."""
        row = self._session.scalar(
            select(LibraryEntryRow).where(
                LibraryEntryRow.library_entry_id == library_entry_id,
                LibraryEntryRow.user_id == principal.user_id,
            )
        )
        if row is None:
            raise OwnedObjectNotFoundError
        if row.row_version != base_version:
            raise StaleVersionError
        if row.removed_at is None:
            return
        conflict = self._session.scalar(
            select(LibraryEntryRow.library_entry_id).where(
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.user_track_ref_id == row.user_track_ref_id,
                LibraryEntryRow.removed_at.is_(None),
            )
        )
        if conflict is not None:
            raise LibraryCommandError("library_restore_conflict")
        row.removed_at = None
        row.updated_at = now
        row.row_version += 1
        self._session.flush()

    def set_preference(
        self,
        principal: Principal,
        *,
        user_track_ref_id: UUID,
        preference: PreferenceValue,
        rating: int | None,
        excluded_from_taste: bool,
        now: datetime,
    ) -> int:
        self._owned_ref(principal, user_track_ref_id)
        if rating is not None and not 1 <= rating <= 5:
            raise LibraryCommandError("rating_invalid")
        row = self._session.get(UserTrackPreferenceRow, user_track_ref_id)
        if row is None:
            row = UserTrackPreferenceRow(
                user_track_ref_id=user_track_ref_id,
                preference=preference.value,
                rating=rating,
                excluded_from_taste=excluded_from_taste,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.preference, row.rating, row.excluded_from_taste = (
                preference.value,
                rating,
                excluded_from_taste,
            )
            row.updated_at, row.row_version = now, row.row_version + 1
        self._session.flush()
        return row.row_version

    def create_playlist(
        self,
        principal: Principal,
        *,
        playlist_id: UUID,
        name: str,
        description: str | None,
        now: datetime,
    ) -> UUID:
        row = PlaylistRow(
            playlist_id=playlist_id,
            owner_user_id=principal.user_id,
            name=validate_playlist_name(name),
            description=description,
            visibility="PRIVATE",
            playlist_type="MANUAL",
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row.playlist_id

    def update_playlist(
        self,
        principal: Principal,
        playlist_id: UUID,
        *,
        name: str,
        description: str | None,
        base_version: int,
        now: datetime,
    ) -> None:
        row = self._owned_playlist(principal, playlist_id)
        if row.row_version != base_version:
            raise StaleVersionError
        row.name = validate_playlist_name(name)
        row.description = description
        row.updated_at = now
        row.row_version += 1
        self._session.flush()

    def delete_playlist(
        self, principal: Principal, playlist_id: UUID, *, base_version: int, now: datetime
    ) -> None:
        row = self._owned_playlist(principal, playlist_id)
        if row.row_version != base_version:
            raise StaleVersionError
        row.deleted_at = now
        row.updated_at = now
        row.row_version += 1
        self._session.execute(
            update(PlaylistEntryRow)
            .where(
                PlaylistEntryRow.playlist_id == playlist_id,
                PlaylistEntryRow.removed_at.is_(None),
            )
            .values(removed_at=now, updated_at=now, row_version=PlaylistEntryRow.row_version + 1)
        )
        self._session.flush()

    def add_playlist_entry(
        self,
        principal: Principal,
        *,
        playlist_entry_id: UUID,
        playlist_id: UUID,
        user_track_ref_id: UUID,
        position_key: str,
        now: datetime,
    ) -> UUID:
        self._owned_playlist(principal, playlist_id)
        self._owned_ref(principal, user_track_ref_id)
        row = PlaylistEntryRow(
            playlist_entry_id=playlist_entry_id,
            playlist_id=playlist_id,
            user_track_ref_id=user_track_ref_id,
            position_key=validate_position_key(position_key),
            added_by_user_id=principal.user_id,
            added_at=now,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as error:
            raise LibraryCommandError("playlist_position_conflict") from error
        return row.playlist_entry_id

    def remove_playlist_entry(
        self, principal: Principal, playlist_entry_id: UUID, *, base_version: int, now: datetime
    ) -> None:
        result = self._session.execute(
            update(PlaylistEntryRow)
            .where(
                PlaylistEntryRow.playlist_entry_id == playlist_entry_id,
                PlaylistEntryRow.row_version == base_version,
                PlaylistEntryRow.removed_at.is_(None),
                PlaylistEntryRow.playlist_id.in_(
                    select(PlaylistRow.playlist_id).where(
                        PlaylistRow.owner_user_id == principal.user_id,
                        PlaylistRow.deleted_at.is_(None),
                    )
                ),
            )
            .values(removed_at=now, updated_at=now, row_version=PlaylistEntryRow.row_version + 1)
        )
        if cast(int, result.rowcount) == 0:  # type: ignore[attr-defined]
            raise OwnedObjectNotFoundError

    def move_playlist_entry(
        self,
        principal: Principal,
        playlist_entry_id: UUID,
        *,
        position_key: str,
        base_version: int,
        now: datetime,
    ) -> None:
        row = self._session.scalar(
            select(PlaylistEntryRow)
            .join(PlaylistRow, PlaylistRow.playlist_id == PlaylistEntryRow.playlist_id)
            .where(
                PlaylistEntryRow.playlist_entry_id == playlist_entry_id,
                PlaylistEntryRow.removed_at.is_(None),
                PlaylistRow.owner_user_id == principal.user_id,
                PlaylistRow.deleted_at.is_(None),
            )
        )
        if row is None:
            raise OwnedObjectNotFoundError
        if row.row_version != base_version:
            raise StaleVersionError
        row.position_key = validate_position_key(position_key)
        row.updated_at = now
        row.row_version += 1
        try:
            self._session.flush()
        except IntegrityError as error:
            raise LibraryCommandError("playlist_position_conflict") from error

    def append_listening(
        self, principal: Principal, command: AppendListeningEvent, *, now: datetime
    ) -> UUID:
        ref = self._owned_ref(principal, command.user_track_ref_id)
        if command.attribution is not None:
            request_owner = self._session.scalar(
                select(RecommendationRequestRow.user_id).where(
                    RecommendationRequestRow.recommendation_request_id
                    == command.attribution.recommendation_request_id
                )
            )
            if request_owner is None:
                raise OwnedObjectNotFoundError
            require_same_owner(principal, request_owner)
        row = ListeningEventRow(
            listening_event_id=command.listening_event_id,
            user_id=principal.user_id,
            device_id=principal.device_id,
            user_track_ref_id=ref.user_track_ref_id,
            recording_id=ref.recording_id,
            started_at=command.started_at,
            played_ms=command.played_ms,
            track_duration_ms=command.track_duration_ms,
            event_origin=command.event_origin,
            context=command.context,
            recommendation_request_id=(
                command.attribution.recommendation_request_id
                if command.attribution is not None
                else None
            ),
            explicit_feedback=command.explicit_feedback,
            excluded_from_taste=command.excluded_from_taste,
            created_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row.listening_event_id

    def library_page(
        self,
        principal: Principal,
        *,
        limit: int,
        before: datetime | None,
        before_id: UUID | None = None,
    ) -> list[LibraryEntryRow]:
        statement: Select[tuple[LibraryEntryRow]] = select(LibraryEntryRow).where(
            LibraryEntryRow.user_id == principal.user_id, LibraryEntryRow.removed_at.is_(None)
        )
        if (before is None) != (before_id is None):
            raise LibraryCommandError("cursor_invalid")
        if before is not None and before_id is not None:
            statement = statement.where(
                or_(
                    LibraryEntryRow.added_at < before,
                    and_(
                        LibraryEntryRow.added_at == before,
                        LibraryEntryRow.library_entry_id < before_id,
                    ),
                )
            )
        return list(
            self._session.scalars(
                statement.order_by(
                    LibraryEntryRow.added_at.desc(), LibraryEntryRow.library_entry_id.desc()
                ).limit(limit)
            )
        )

    def playlists_page(
        self,
        principal: Principal,
        *,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[PlaylistRow]:
        statement: Select[tuple[PlaylistRow]] = select(PlaylistRow).where(
            PlaylistRow.owner_user_id == principal.user_id, PlaylistRow.deleted_at.is_(None)
        )
        if (before is None) != (before_id is None):
            raise LibraryCommandError("cursor_invalid")
        if before is not None and before_id is not None:
            statement = statement.where(
                or_(
                    PlaylistRow.updated_at < before,
                    and_(PlaylistRow.updated_at == before, PlaylistRow.playlist_id < before_id),
                )
            )
        return list(
            self._session.scalars(
                statement.order_by(
                    PlaylistRow.updated_at.desc(), PlaylistRow.playlist_id.desc()
                ).limit(limit)
            )
        )

    def history_page(
        self,
        principal: Principal,
        *,
        limit: int,
        before: datetime | None,
        before_id: UUID | None = None,
    ) -> list[ListeningEventRow]:
        statement: Select[tuple[ListeningEventRow]] = select(ListeningEventRow).where(
            ListeningEventRow.user_id == principal.user_id
        )
        if (before is None) != (before_id is None):
            raise LibraryCommandError("cursor_invalid")
        if before is not None and before_id is not None:
            statement = statement.where(
                or_(
                    ListeningEventRow.started_at < before,
                    and_(
                        ListeningEventRow.started_at == before,
                        ListeningEventRow.listening_event_id < before_id,
                    ),
                )
            )
        return list(
            self._session.scalars(
                statement.order_by(
                    ListeningEventRow.started_at.desc(), ListeningEventRow.listening_event_id.desc()
                ).limit(limit)
            )
        )

    def search_library(
        self, principal: Principal, *, query: str, limit: int
    ) -> list[LibraryEntryRow]:
        # LIKE wildcards are literal user characters; this avoids accidental broad scans.
        normalized_query = query.strip()
        if not normalized_query:
            raise LibraryCommandError("search_query_invalid")
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{escaped}%"
        return list(
            self._session.scalars(
                select(LibraryEntryRow)
                .join(
                    UserTrackRefRow,
                    UserTrackRefRow.user_track_ref_id == LibraryEntryRow.user_track_ref_id,
                )
                .where(
                    LibraryEntryRow.user_id == principal.user_id,
                    LibraryEntryRow.removed_at.is_(None),
                    or_(
                        UserTrackRefRow.raw_title.ilike(needle, escape="\\"),
                        UserTrackRefRow.raw_artist.ilike(needle, escape="\\"),
                        UserTrackRefRow.raw_album.ilike(needle, escape="\\"),
                    ),
                )
                .order_by(LibraryEntryRow.added_at.desc(), LibraryEntryRow.library_entry_id)
                .limit(limit)
            )
        )

    def _owned_ref(self, principal: Principal, ref_id: UUID) -> UserTrackRefRow:
        row = self._session.scalar(
            select(UserTrackRefRow).where(
                UserTrackRefRow.user_track_ref_id == ref_id, UserTrackRefRow.deleted_at.is_(None)
            )
        )
        if row is None:
            raise OwnedObjectNotFoundError
        require_same_owner(principal, row.user_id)
        return row

    def _owned_playlist(self, principal: Principal, playlist_id: UUID) -> PlaylistRow:
        row = self._session.scalar(
            select(PlaylistRow).where(
                PlaylistRow.playlist_id == playlist_id, PlaylistRow.deleted_at.is_(None)
            )
        )
        if row is None:
            raise OwnedObjectNotFoundError
        require_same_owner(principal, row.owner_user_id)
        return row

    def _raise_owned_or_stale(
        self,
        model: type[LibraryEntryRow],
        column: object,
        value: UUID,
        principal: Principal,
        version: int,
    ) -> None:
        del model, column, value, principal, version
        raise StaleVersionError


__all__ = ("LibraryRepository",)
