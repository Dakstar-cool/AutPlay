"""Worker identity preparation joins its caller's transaction and rolls back as one unit."""

from __future__ import annotations

from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import (
    ArtistRow,
    LibraryEntryRow,
    MatchDecisionRow,
    RecordingRow,
    SyncEventRow,
    UserAccountRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.music_library import MusicLibraryService
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .test_resource_admission_runtime import present


def test_preparing_identity_joins_single_connection_and_rolls_back_with_caller(
    database_url: str,
) -> None:
    engine = create_engine(database_url, pool_size=1, max_overflow=0, pool_timeout=0.25)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    owner, ref_id = uuid4(), uuid4()
    try:
        with sessions.begin() as session:
            session.add(UserAccountRow(user_id=owner, display_name="Worker identity", role="USER"))
            session.flush()
            session.add(
                UserTrackRefRow(
                    user_track_ref_id=ref_id,
                    user_id=owner,
                    raw_title="Test",
                    raw_artist="Synthetic",
                )
            )
            session.flush()
            session.add(
                LibraryEntryRow(
                    user_id=owner,
                    user_track_ref_id=ref_id,
                    source="SEARCH",
                    availability_status="PENDING",
                )
            )
        service = MusicLibraryService(sessions, None)
        with pytest.raises(RuntimeError, match="abort_handoff"), sessions.begin() as session:
            lock_resource_admission(session)
            recording = service.prepare_in_transaction(session, owner, ref_id)
            assert present(session.get(UserTrackRefRow, ref_id)).recording_id == recording
            raise RuntimeError("abort_handoff")
        with sessions() as session:
            ref = present(session.get(UserTrackRefRow, ref_id))
            assert ref.recording_id is None and ref.current_match_decision_id is None
            for model in (ArtistRow, RecordingRow, MatchDecisionRow, SyncEventRow):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        with sessions.begin() as session:
            lock_resource_admission(session)
            recording = service.prepare_in_transaction(session, owner, ref_id)
            assert service.prepare_in_transaction(session, owner, ref_id) == recording
        with sessions() as session:
            assert present(session.get(UserTrackRefRow, ref_id)).recording_id == recording
            assert session.scalar(select(func.count()).select_from(RecordingRow)) == 1
    finally:
        engine.dispose()
