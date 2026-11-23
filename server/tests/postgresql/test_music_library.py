"""Real database proof for owner identity preservation and immutable Internet choices."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.internet_music import InternetMusicProvider
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    JobRow,
    LibraryEntryRow,
    RecordingRow,
    UserSessionRow,
    UserTrackRefRow,
)
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
    session_id = uuid4()
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
        assert isinstance(user, UUID)
        assert isinstance(device, UUID)
        session.add(
            UserSessionRow(
                session_id=session_id,
                user_id=user,
                device_id=device,
                refresh_token_hash=hashlib.sha256(session_id.bytes).digest(),
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=90),
                session_mode="V2",
                family_id=session_id,
                generation=0,
            )
        )
    return Principal(user, device, session_id, AccountRole.USER)


class Provider(InternetMusicProvider):
    calls = 0

    def search(self, query: str) -> list[dict[str, Any]]:
        del query
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
        ref_row = session.get(UserTrackRefRow, ref_id)
        entry_row = session.get(LibraryEntryRow, entry_id)
        assert ref_row is not None and ref_row.recording_id == recording
        assert entry_row is not None and entry_row.availability_status == "LOCAL"
        assert session.scalar(select(func.count()).select_from(RecordingRow)) == 1
        assert session.scalar(select(func.count()).select_from(UserTrackRefRow)) == 1
    with pytest.raises(ApiError) as denied:
        service.prepare(owner(sessions), ref_id)
    assert denied.value.status_code == 404
    engine.dispose()


def test_verified_musicbrainz_disc_position_keeps_joint_and_release_credits_separate(
    database_url: str,
) -> None:
    from autplay.adapters.postgresql.confirmed_edition import project_edition
    from autplay.application.sync import _edition_tracks
    from sqlalchemy import text

    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    service = MusicLibraryService(sessions, object())
    with sessions.begin() as session:
        ref = UserTrackRefRow(
            user_id=principal.user_id,
            raw_title="BASS PESADÃO",
            raw_artist="SVRGN",
            resolution_status="UNRESOLVED",
        )
        session.add(ref)
        session.flush()
        session.add(
            LibraryEntryRow(
                user_id=principal.user_id,
                user_track_ref_id=ref.user_track_ref_id,
                source="IMPORT",
                availability_status="LOCAL",
            )
        )
    service.prepare(principal, ref.user_track_ref_id)
    musicians = [
        {
            "name": name,
            "mbid": str(uuid4()),
            "credited_name": name,
            "join_phrase": join,
            "role": "PRIMARY",
        }
        for name, join in (("SVRGN", " & "), ("RAGETRAIN", ""))
    ]
    release = str(uuid4())
    snapshot = {
        "fields": {
            "mb_recording_id": str(uuid4()),
            "mb_release_group_id": str(uuid4()),
            "mb_release_id": release,
            "album": "Joint edition",
            "title": "BASS PESADÃO",
            "disc_number": 1,
            "track_number": 2,
            "release_date": "2026-05",
        },
        "release_track": str(uuid4()),
        "recording_members": musicians,
        "release_members": musicians[:1],
        "track_members": musicians,
        "type": "Single",
    }
    with sessions.begin() as session:
        stored = session.get(UserTrackRefRow, ref.user_track_ref_id)
        assert stored is not None
        assert project_edition(session, stored, snapshot)
        assert not project_edition(session, stored, snapshot)
        (row,) = _edition_tracks(session, principal.user_id)
        assert row.payload["release_mbid"] == release
        assert row.payload["release_date"] == "2026-05" and row.payload["sequence_no"] == 2
        assert row.payload["track_artist"] == "SVRGN & RAGETRAIN"
        assert session.scalar(text("SELECT count(*) FROM catalog.release_track")) == 1
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
    with sessions.begin() as session:
        old_search = InternetSearchRow(
            search_id=uuid4(),
            user_id=principal.user_id,
            query="old broad search",
            candidates=result["candidates"],
            snapshot_sha256=b"0" * 32,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(old_search)
    assert service.search(principal, "old broad search", old_search.search_id)["candidates"] == []
    with pytest.raises(ApiError) as denied:
        service.select(principal, old_search.search_id, "candidate02")
    assert denied.value.code == "music_search_expired"
    engine.dispose()


@pytest.mark.parametrize("source", ["import", "legacy_search"])
def test_two_searches_reuse_playable_prior_source(database_url: str, source: str) -> None:
    from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow

    from .test_track_metadata import setup
    from .test_track_metadata_acquisition import publish_audio

    engine, sessions, principal, ref_id, _ = setup(database_url)
    variant_id = publish_audio(sessions, principal, ref_id)
    service = InternetMusicService(sessions, object(), Provider())
    candidate = Provider().search("one")[0]
    with sessions.begin() as session:
        session.get_one(AudioVariantRow, variant_id).duration_ms = candidate["duration_ms"]
        if source == "import":
            session.add(
                TrackMetadataRow(
                    user_track_ref_id=ref_id,
                    document={
                        "audio_variant_id": str(variant_id),
                        "acquisition_evidence": {"provider": "YOUTUBE", "source_id": "candidate00"},
                    },
                )
            )
        else:
            old_search = InternetSearchRow(
                search_id=uuid4(),
                user_id=principal.user_id,
                query="older search",
                candidates=[candidate],
                snapshot_sha256=b"x" * 32,
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
            old_job = JobRow(
                job_type="music.internet.acquire",
                schema_version=1,
                user_id=principal.user_id,
                state="COMPLETED",
                completed_at=datetime.now(UTC),
            )
            session.add_all([old_search, old_job])
            session.flush()
            session.add(
                InternetAcquisitionRow(
                    acquisition_id=uuid4(),
                    user_id=principal.user_id,
                    device_id=principal.device_id,
                    search_id=old_search.search_id,
                    candidate_id="candidate00",
                    selected_snapshot=candidate,
                    job_id=old_job.job_id,
                    state="READY",
                    user_track_ref_id=ref_id,
                    audio_variant_id=variant_id,
                )
            )
    searches = [
        UUID(service.search(principal, query, uuid4())["search_id"]) for query in ("one", "two")
    ]
    start = Barrier(2)

    def select_track(search: UUID) -> dict[str, Any]:
        start.wait(timeout=10)
        return service.select(principal, search, "candidate00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(select_track, searches)
    assert first == second == service.status(principal, UUID(first["acquisition_id"]))
    assert (first["state"], first["user_track_ref_id"], first["audio_variant_id"]) == (
        "READY",
        str(ref_id),
        str(variant_id),
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetAcquisitionRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.job_type == "music.internet.acquire", JobRow.state != "COMPLETED")
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(UserTrackRefRow)) == 1
    other = owner(sessions)
    other_search = UUID(service.search(other, "one", uuid4())["search_id"])
    assert service.select(other, other_search, "candidate00")["state"] == "QUEUED"
    assert service.select(principal, searches[0], "candidate01")["state"] == "QUEUED"
    engine.dispose()


def test_parallel_new_searches_share_one_download_even_after_pause(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    service = InternetMusicService(sessions, object(), Provider())
    searches = [
        UUID(service.search(principal, query, uuid4())["search_id"]) for query in ("a", "b")
    ]
    start = Barrier(2)

    def select_track(search: UUID) -> dict[str, Any]:
        start.wait(timeout=10)
        return service.select(principal, search, "candidate00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(select_track, searches)
    assert first == second and first["state"] == "QUEUED"
    with sessions.begin() as session:
        job = session.scalars(
            select(JobRow).where(JobRow.job_type == "music.internet.acquire")
        ).one()
        job.state = "PAUSED"
    third = UUID(service.search(principal, "c", uuid4())["search_id"])
    assert service.select(principal, third, "candidate00") == first
    engine.dispose()


class AlternateRecordings(Provider):
    def search(self, query: str) -> list[dict[str, Any]]:
        result = super().search(query)[0]
        result.update(candidate_id=query, title=" Original ", artist="ARTIST", duration_ms=182000)
        if query == "long-version":
            result["duration_ms"] = 182001
        if query == "other-artist":
            result["artist"] = "Guest"
        if query == "other-title":
            result["title"] = "Original (Remix)"
        return [result]


def test_metadata_dedup_owned_and_published_vault(database_url: str) -> None:
    from autplay.adapters.postgresql.shared_catalog import enable_bridge_catalog

    from .test_track_metadata import setup
    from .test_track_metadata_acquisition import publish_audio

    engine, sessions, principal, ref, _ = setup(database_url)
    variant = publish_audio(sessions, principal, ref)  # 180000 ms
    service = InternetMusicService(sessions, object(), AlternateRecordings())

    def choose(actor: Principal, query: str) -> dict[str, Any]:
        search = service.search(actor, query, uuid4())
        return service.select(actor, UUID(search["search_id"]), query)

    saved = choose(principal, "one-id")
    assert saved["state"] == "READY" and saved["user_track_ref_id"] == str(ref)
    assert choose(principal, "different-id")["audio_variant_id"] == str(variant)
    # Only a published variant is reachable from a different listener.
    assert choose(owner(sessions), "private")["state"] == "QUEUED"
    with sessions.begin() as session:
        session.execute(
            text(
                "UPDATE vault.recording_canonical_variant "
                "SET policy_version='acquisition-verified-upload-v1'"
            )
        )
        assert enable_bridge_catalog(session) == 1
    listener = owner(sessions)
    shared = choose(listener, "public-id")
    again = choose(listener, "another-id")
    assert shared["state"] == again["state"] == "READY"
    assert shared["audio_variant_id"] == again["audio_variant_id"] == str(variant)
    assert shared["user_track_ref_id"] == again["user_track_ref_id"] != str(ref)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LibraryEntryRow)
                .where(LibraryEntryRow.user_id == listener.user_id)
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(AudioVariantRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.user_id == listener.user_id,
                    JobRow.job_type == "music.internet.acquire",
                    JobRow.state != "COMPLETED",
                )
            )
            == 0
        )
    for different in ("other-artist", "other-title", "long-version"):
        assert choose(listener, different)["state"] == "QUEUED"
    with sessions.begin() as session:
        session.execute(
            text(
                "UPDATE identity.source_provider SET enabled=false "
                "WHERE provider_key='autplay.server-catalog'"
            )
        )
    assert service.status(listener, UUID(shared["acquisition_id"]))["state"] == "FAILED"
    engine.dispose()


def test_different_ids_selected_concurrently_use_one_acquisition(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    service = InternetMusicService(sessions, object(), AlternateRecordings())
    requests = [
        (UUID(service.search(principal, key, uuid4())["search_id"]), key)
        for key in ("first-id", "second-id")
    ]
    start = Barrier(2)

    def choose(request: tuple[UUID, str]) -> dict[str, Any]:
        start.wait(timeout=10)
        return service.select(principal, *request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(choose, requests)
    assert first == second and first["state"] == "QUEUED"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetAcquisitionRow)) == 1
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
    calls: list[str] = []
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
