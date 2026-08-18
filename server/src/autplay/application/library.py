"""P07 application facade; sync will call these commands in P09."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.library_runtime import LibraryRepository
from autplay.adapters.postgresql.models import LibraryEntryRow, ListeningEventRow, PlaylistRow
from autplay.domain.auth import Principal
from autplay.domain.library import AppendListeningEvent, CreateUnresolvedTrack, PreferenceValue


class LibraryService:
    """One short transaction per owner-scoped library command or projection."""

    def __init__(self, sessions: Callable[[], Session], clock: Callable[[], datetime]) -> None:
        self._sessions, self._clock = sessions, clock

    def query_library(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[LibraryEntryRow]:
        with self._sessions() as session:
            return LibraryRepository(session).library_page(
                principal, limit=limit, before=before, before_id=before_id
            )

    def query_playlists(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[PlaylistRow]:
        with self._sessions() as session:
            return LibraryRepository(session).playlists_page(
                principal, limit=limit, before=before, before_id=before_id
            )

    def query_history(
        self,
        principal: Principal,
        limit: int,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> Sequence[ListeningEventRow]:
        with self._sessions() as session:
            return LibraryRepository(session).history_page(
                principal, limit=limit, before=before, before_id=before_id
            )

    def query_search(
        self, principal: Principal, query: str, limit: int
    ) -> Sequence[LibraryEntryRow]:
        with self._sessions() as session:
            return LibraryRepository(session).search_library(principal, query=query, limit=limit)

    # These are deliberately application commands, not HTTP writes. P09 sync is their transport.
    def create_unresolved(self, principal: Principal, command: CreateUnresolvedTrack) -> UUID:
        with self._sessions() as session:
            result = LibraryRepository(session).create_unresolved(
                principal, command, now=self._clock()
            )
            session.commit()
            return result

    def add_library_entry(self, principal: Principal, **values: object) -> UUID:
        return self._write_untyped(principal, "add_library_entry", values)  # type: ignore[return-value]

    def remove_library_entry(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "remove_library_entry", values)

    def restore_library_entry(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "restore_library_entry", values)

    def set_preference(self, principal: Principal, **values: object) -> int:
        return self._write_untyped(principal, "set_preference", values)  # type: ignore[return-value]

    def create_playlist(self, principal: Principal, **values: object) -> UUID:
        return self._write_untyped(principal, "create_playlist", values)  # type: ignore[return-value]

    def update_playlist(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "update_playlist", values)

    def delete_playlist(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "delete_playlist", values)

    def add_playlist_entry(self, principal: Principal, **values: object) -> UUID:
        return self._write_untyped(principal, "add_playlist_entry", values)  # type: ignore[return-value]

    def remove_playlist_entry(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "remove_playlist_entry", values)

    def move_playlist_entry(self, principal: Principal, **values: object) -> None:
        self._write_untyped(principal, "move_playlist_entry", values)

    def append_listening(self, principal: Principal, command: AppendListeningEvent) -> UUID:
        with self._sessions() as session:
            result = LibraryRepository(session).append_listening(
                principal, command, now=self._clock()
            )
            session.commit()
            return result

    def _write_untyped(
        self, principal: Principal, method_name: str, values: dict[str, object]
    ) -> object:
        with self._sessions() as session:
            method = getattr(LibraryRepository(session), method_name)
            result = method(principal, now=self._clock(), **values)
            session.commit()
            return result


__all__ = ("LibraryService", "PreferenceValue")
