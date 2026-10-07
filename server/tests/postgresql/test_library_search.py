from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.library_runtime import LibraryRepository
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserTrackRefRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.application.library import LibraryService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.library import CreateUnresolvedTrack
from autplay.domain.music_search import MusicSearchKind
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _owner(session: Session) -> Principal:
    principal = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    session.add(
        UserAccountRow(
            user_id=principal.user_id, display_name="Search owner", role="USER", status="ACTIVE"
        )
    )
    session.flush()
    session.add(
        DeviceRow(
            device_id=principal.device_id,
            user_id=principal.user_id,
            device_name="Search device",
            platform="ANDROID",
            app_version="search-test",
        )
    )
    session.flush()
    return principal


def _track(
    session: Session,
    principal: Principal,
    *,
    title: str,
    artist: str = "Artist",
    album: str = "Album",
    availability: str = "LOCAL",
    now: datetime | None = None,
) -> tuple[UUID, UUID]:
    repository = LibraryRepository(session)
    observed = now or datetime.now(UTC)
    ref = repository.create_unresolved(
        principal,
        CreateUnresolvedTrack(uuid4(), title, artist, album),
        now=observed,
    )
    entry = repository.add_library_entry(
        principal,
        library_entry_id=uuid4(),
        user_track_ref_id=ref,
        source="IMPORT",
        availability_status=availability,
        now=observed,
    )
    return ref, entry


def test_field_kinds_match_only_selected_effective_field_without_duplicate_tracks(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _owner(session)
            _, title_entry = _track(session, owner, title="Needle live")
            _, artist_entry = _track(session, owner, title="Song", artist="Needle artist")
            _, album_entry = _track(session, owner, title="Other song", album="Needle album")
            _, shared_entry = _track(
                session, owner, title="Needle", artist="Needle", album="Needle"
            )
            repository = LibraryRepository(session)
            expected = {
                MusicSearchKind.ALL: {title_entry, artist_entry, album_entry, shared_entry},
                MusicSearchKind.TRACK: {title_entry, shared_entry},
                MusicSearchKind.ARTIST: {artist_entry, shared_entry},
                MusicSearchKind.ALBUM: {album_entry, shared_entry},
            }
            for kind, identities in expected.items():
                rows = repository.search_library(owner, query="  nEeDlE  ", limit=100, kind=kind)
                assert {row.library_entry_id for row in rows} == identities
                assert len(rows) == len(identities)
            assert repository.search_library(owner, query="Needle remix", limit=100) == []
    finally:
        engine.dispose()


def test_applied_overlay_fallback_explicit_clears_and_unselected_candidates(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions.begin() as session:
            owner = _owner(session)
            ref, entry = _track(session, owner, title="Old title", album="Old album")
            cleared, _ = _track(session, owner, title="Cleared song", album="Cleared album")
            raw, raw_entry = _track(session, owner, title="Raw title", album="Raw album")
            document = {
                "fields": {"title": "Fresh title", "album": "Fresh album", "genres": ["Future"]},
                "provenance": {},
            }
            session.add_all(
                [
                    TrackMetadataRow(
                        user_track_ref_id=ref,
                        document=document,
                        state="REVIEW",
                        candidates=[{"fields": {"album": "Unselected album"}}],
                    ),
                    TrackMetadataRow(
                        user_track_ref_id=cleared, document={"fields": {"album": None}}
                    ),
                    TrackMetadataRow(
                        user_track_ref_id=raw,
                        document={"fields": {"label": "Label"}},
                        candidates=[{"fields": {"album": "Unselected album"}}],
                    ),
                ]
            )
        service = LibraryService(sessions, lambda: datetime.now(UTC))
        assert [
            row.library_entry_id
            for row in service.query_search(owner, "Fresh album", 100, kind=MusicSearchKind.ALBUM)
        ] == [entry]
        assert [
            row.library_entry_id
            for row in service.query_search(owner, "Fresh title", 100, kind=MusicSearchKind.TRACK)
        ] == [entry]
        assert [
            row.library_entry_id
            for row in service.query_search(owner, "Raw album", 100, kind=MusicSearchKind.ALBUM)
        ] == [raw_entry]
        assert len(service.query_search(owner, "Artist", 100, kind=MusicSearchKind.ARTIST)) == 3
        for query in ("Old title", "Old album", "Cleared album", "Unselected album"):
            assert service.query_search(owner, query, 100) == []
        with sessions() as session:
            stored = session.get(TrackMetadataRow, ref)
            assert stored is not None and stored.document == document
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", [MusicSearchKind.ALL, MusicSearchKind.ALBUM])
def test_literal_search_owner_tombstones_and_bounded_album_track_navigation(
    database_url: str,
    kind: MusicSearchKind,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = _owner(session), _owner(session)
            now = datetime.now(UTC)
            _, vault_entry = _track(
                session, owner, title="First", album="100%_\\ album", availability="VAULT", now=now
            )
            _, local_entry = _track(
                session,
                owner,
                title="Second",
                artist="Different artist",
                album="100%_\\ album",
                now=now + timedelta(seconds=1),
            )
            _track(session, owner, title="False wildcard match", album="100xyz album")
            _track(session, other, title="Other owner", album="100%_\\ album")
            removed_ref, removed_entry = _track(
                session, owner, title="Removed", album="100%_\\ album"
            )
            deleted_ref, _ = _track(session, owner, title="Deleted", album="100%_\\ album")
            repository = LibraryRepository(session)
            repository.remove_library_entry(owner, removed_entry, base_version=1, now=now)
            deleted = session.get(UserTrackRefRow, deleted_ref)
            assert deleted is not None
            deleted.deleted_at = now
            session.flush()
            assert removed_ref != deleted_ref
            rows = repository.search_library(owner, query="100%_\\", limit=100, kind=kind)
            assert [row.library_entry_id for row in rows] == [vault_entry, local_entry]
            assert [row.availability_status for row in rows] == ["VAULT", "LOCAL"]
            first = repository.search_library(owner, query="100%_\\", limit=1, kind=kind)
            assert [row.library_entry_id for row in first] == [vault_entry]
    finally:
        engine.dispose()
