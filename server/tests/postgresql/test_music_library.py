"""Real database proof for owner identity preservation and immutable Internet choices."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.internet_music import InternetMusicProvider
from autplay.adapters.postgresql.models import LibraryEntryRow, RecordingRow, UserTrackRefRow
from autplay.adapters.postgresql.models.internet_music import (
    InternetAcquisitionRow,
    InternetSearchRow,
)
from autplay.application.internet_music import InternetMusicService
from autplay.application.music_library import MusicError as ApiError
from autplay.application.music_library import MusicLibraryService
from autplay.domain.auth import AccountRole, Principal
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


def owner(sessions: sessionmaker[Session]) -> Principal:
    with sessions.begin() as session:
        user = session.scalar(
            text(
                """INSERT INTO account.user_account(display_name,role)
                VALUES ('Music test','USER') RETURNING user_id"""
            )
        )
        device = session.scalar(
            text(
                """INSERT INTO account.device(user_id,device_name,platform,app_version)
                VALUES (:user,'Music test','ANDROID','test') RETURNING device_id"""
            ),
            {"user": user},
        )
    return Principal(user, device, uuid4(), AccountRole.USER)


class Provider(InternetMusicProvider):
    calls = 0

    def search(self, query: str) -> list[dict[str, Any]]:
        self.calls += 1
        return [
            {
                "candidate_id": f"candidate0{i}",
                "provider": "YouTube",
                "title": f"Track {i}",
                "artist": "Artist",
                "duration_ms": 150000,
                "rank": i + 1,
            }
            for i in range(5)
        ]


def test_prepare_preserves_ref_and_library_and_replays(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    with sessions.begin() as session:
        ref = UserTrackRefRow(
            user_id=principal.user_id,
            raw_title="Phone song",
            raw_artist="Phone artist",
            resolution_status="UNRESOLVED",
        )
        session.add(ref)
        session.flush()
        entry = LibraryEntryRow(
            user_id=principal.user_id,
            user_track_ref_id=ref.user_track_ref_id,
            source="IMPORT",
            availability_status="LOCAL",
        )
        session.add(entry)
        session.flush()
        ref_id, entry_id = ref.user_track_ref_id, entry.library_entry_id
    service = MusicLibraryService(sessions, object())
    recording = service.prepare(principal, ref_id)
    assert service.prepare(principal, ref_id) == recording
    with sessions() as session:
        stored_ref = session.get(UserTrackRefRow, ref_id)
        assert stored_ref is not None and stored_ref.recording_id == recording
        stored_entry = session.get(LibraryEntryRow, entry_id)
        assert stored_entry is not None and stored_entry.availability_status == "LOCAL"
        assert session.scalar(select(func.count()).select_from(RecordingRow)) == 1
        assert session.scalar(select(func.count()).select_from(UserTrackRefRow)) == 1
    with pytest.raises(ApiError) as denied:
        service.prepare(owner(sessions), ref_id)
    assert denied.value.status_code == 404
    engine.dispose()


def test_search_snapshot_membership_replay_expiry_and_owner(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    provider = Provider()
    service = InternetMusicService(sessions, object(), provider)
    identity = uuid4()
    result = service.search(principal, "artist song", identity)
    assert len(result["candidates"]) == 5
    assert service.search(principal, "artist song", identity) == result
    assert provider.calls == 1
    with pytest.raises(ApiError):
        service.search(principal, "different song", identity)
    with pytest.raises(ApiError):
        service.select(principal, identity, "not_selected")
    selected = service.select(principal, identity, "candidate02")
    assert service.select(principal, identity, "candidate02") == selected
    other_search = service.search(principal, "another matching query", uuid4())
    assert service.select(principal, UUID(other_search["search_id"]), "candidate02") == selected
    with pytest.raises(ApiError):
        service.select(owner(sessions), identity, "candidate02")
    with sessions() as session:
        rows = session.scalars(select(InternetAcquisitionRow)).all()
        assert len(rows) == 1
        assert rows[0].selected_snapshot["title"] == "Track 2"
    with pytest.raises(DBAPIError), sessions.begin() as session:
        session.execute(
            text("UPDATE discovery.internet_search SET candidates='[]' WHERE search_id=:id"),
            {"id": identity},
        )
    with pytest.raises(DBAPIError), sessions.begin() as session:
        session.execute(text("UPDATE discovery.internet_acquisition SET candidate_id='changed'"))
    with sessions.begin() as session:
        expired = InternetSearchRow(
            search_id=uuid4(),
            user_id=principal.user_id,
            query="expired",
            candidates=result["candidates"],
            snapshot_sha256=b"0" * 32,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(expired)
    with pytest.raises(ApiError) as expiry:
        service.select(principal, expired.search_id, "candidate02")
    assert expiry.value.code == "music_search_expired"
    engine.dispose()


def test_worker_fence_and_revocation_prevent_side_effect(database_url: str) -> None:
    from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
    from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
    from autplay.application.internet_music import INTERNET_ACQUIRE_JOB, InternetMusicHandler
    from autplay.application.job_worker import JobExecutionContext, JobLeaseLost

    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    service = InternetMusicService(sessions, object(), Provider())
    search = service.search(principal, "fence proof", uuid4())
    selected = service.select(principal, UUID(search["search_id"]), "candidate00")
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="music-test",
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        fence=lease.fence,
        lease_interval=timedelta(seconds=60),
    )
    handler = InternetMusicHandler(service)
    calls = []
    identity = UUID(selected["acquisition_id"])
    handler._guarded(identity, context, lambda: calls.append("allowed"))
    with sessions.begin() as session:
        session.execute(
            text("UPDATE account.device SET revoked_at=now() WHERE device_id=:id"),
            {"id": principal.device_id},
        )
    with pytest.raises(ApiError):
        handler._guarded(identity, context, lambda: calls.append("revoked"))
    with sessions.begin() as session:
        session.execute(
            text("UPDATE jobs.job SET cancel_requested_at=now() WHERE job_id=:id"),
            {"id": lease.fence.job_id},
        )
    with pytest.raises(JobLeaseLost):
        handler._guarded(identity, context, lambda: calls.append("cancelled"))
    assert calls == ["allowed"]
    engine.dispose()
