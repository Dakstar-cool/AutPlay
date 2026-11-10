"""Scheduled favorites, restart fairness and live authority on disposable SQL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.discovery_automation_runtime import (
    PostgresDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.discovery_runtime import JAMENDO_PROVIDER_ID
from autplay.adapters.postgresql.models import (
    DiscoveryRunRow,
    ExternalReferenceRow,
    JobRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.release_feed import ReleaseFeedRepository
from autplay.adapters.postgresql.release_watch import ReleaseWatchRepository
from autplay.adapters.postgresql.release_watch_dispatch import reconcile_watch_policies
from autplay.adapters.postgresql.watch_auto_download_authority import require_effective_watch
from autplay.application.discovery_automation import DiscoveryAutomationError
from autplay.application.release_watch import ReleaseWatchService
from autplay.application.release_watch_refresh import FavoriteSweep, ScheduledFavoriteRefresh
from autplay.domain.discovery import ProviderTrackPage
from autplay.domain.jobs import LeaseFence

from .conftest import DatabaseHarness
from .test_release_feed_runtime import _candidate, _run
from .test_release_watch_runtime import _artist, _owner, _plays


def test_scheduled_history_daily_refresh_preserves_continuing_favorite_and_drops_old_history(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = ReleaseWatchService(sessions, lambda: datetime.now(UTC))
    try:
        with sessions.begin() as session:
            now = datetime.now(UTC)
            owner, device = _owner(session, "scheduled favorite")
            sparse, _ = _owner(session, "sparse history")
            _artist_id, recordings = _artist(session, "Scheduled favorite")
            manual, _ = _artist(session, "Manual independent")
            _plays(session, owner, device, recordings, now)
            ReleaseWatchRepository(session).set_manual_follow(owner, manual, follow=True, now=now)
        assert service.refresh_due_from_history() == FavoriteSweep(2, 2, 0)
        assert service.refresh_due_from_history() == FavoriteSweep(0, 0, 0)
        with sessions.begin() as session:
            # Model valid continuous membership before the daily-expiry fixture.
            # Keep this first instant older than both forced stale refresh times.
            session.execute(
                text(
                    "UPDATE library.favorite_artist_projection SET "
                    "projected_at=clock_timestamp()-INTERVAL '48 hours' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
            projected_at = session.scalar(
                text(
                    "SELECT projected_at FROM "
                    "library.favorite_artist_projection WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
            assert ReleaseWatchRepository(session).list_effective(sparse) == []
            session.execute(
                text(
                    "UPDATE library.favorite_artist_refresh SET "
                    "refreshed_at=clock_timestamp()-INTERVAL '24 hours' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
        assert service.refresh_due_from_history() == FavoriteSweep(1, 1, 0)
        with sessions.begin() as session:
            assert (
                session.scalar(
                    text(
                        "SELECT generation FROM library.favorite_artist_refresh "
                        "WHERE user_id=:owner"
                    ),
                    {"owner": owner},
                )
                == 2
            )
            assert (
                session.scalar(
                    text(
                        "SELECT projected_at FROM library.favorite_artist_projection "
                        "WHERE user_id=:owner"
                    ),
                    {"owner": owner},
                )
                == projected_at
            )
            session.execute(
                text(
                    "UPDATE library.listening_event SET "
                    "started_at=clock_timestamp()-INTERVAL '91 days' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
            session.execute(
                text(
                    "UPDATE library.favorite_artist_refresh SET "
                    "refreshed_at=clock_timestamp()-INTERVAL '24 hours' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
        assert service.refresh_due_from_history() == FavoriteSweep(1, 1, 0)
        with sessions() as session:
            watches = ReleaseWatchRepository(session).list_effective(owner)
            assert [watch.artist_id for watch in watches] == [manual]
            assert (
                session.scalar(
                    text("SELECT count(*) FROM jobs.job WHERE user_id=:owner"), {"owner": owner}
                )
                == 0
            )
    finally:
        engine.dispose()


def test_restart_backoff_keeps_21_overflow_owners_from_starving_healthy_owner(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("autplay.adapters.postgresql.release_watch.MAX_EVIDENCE_EVENTS", 2)
    try:
        with sessions.begin() as session:
            _artist_id, recordings = _artist(session, "Overflow restart")
            for index in range(1, 23):
                # Fixed IDs order the 21 refusals ahead of the healthy owner.
                owner = UserAccountRow(
                    user_id=UUID(int=index), display_name="bounded", role="USER", status="ACTIVE"
                )
                session.add(owner)
                session.flush()
                if index <= 21:
                    from autplay.adapters.postgresql.models import DeviceRow

                    device = DeviceRow(
                        user_id=owner.user_id,
                        device_name="fixture",
                        platform="ANDROID",
                        app_version="m11",
                    )
                    session.add(device)
                    session.flush()
                    _plays(session, owner.user_id, device.device_id, recordings, datetime.now(UTC))
        assert ScheduledFavoriteRefresh(sessions).run() == FavoriteSweep(20, 0, 20)
        # A new instance models --once/process restart without an in-memory cursor.
        assert ScheduledFavoriteRefresh(sessions).run() == FavoriteSweep(2, 1, 1)
        assert ScheduledFavoriteRefresh(sessions).run() == FavoriteSweep(0, 0, 0)
        with sessions.begin() as session:
            assert (
                session.scalar(text("SELECT count(*) FROM library.favorite_artist_refresh_retry"))
                == 21
            )
            assert session.scalar(text("SELECT count(*) FROM library.favorite_artist_refresh")) == 1
            assert (
                session.scalar(text("SELECT count(*) FROM library.favorite_artist_projection")) == 0
            )
            session.execute(
                text(
                    "WITH instant AS (SELECT clock_timestamp() AS at) "
                    "UPDATE library.favorite_artist_refresh_retry SET "
                    "attempted_at=instant.at-INTERVAL '25 hours', "
                    "retry_after=instant.at-INTERVAL '1 hour' FROM instant WHERE user_id=:owner"
                ),
                {"owner": UUID(int=1)},
            )
        # The same 24h budget makes failures retryable, without a false refresh.
        assert ScheduledFavoriteRefresh(sessions).run(limit=1) == FavoriteSweep(1, 0, 1)
    finally:
        engine.dispose()


def test_skip_locked_owner_and_concurrent_workers_do_not_duplicate_generation(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions.begin() as session:
            locked, _ = _owner(session, "locked owner")
            healthy, _ = _owner(session, "healthy owner")
        with sessions.begin() as holder:
            holder.execute(
                text("SELECT user_id FROM account.user_account WHERE user_id=:owner FOR UPDATE"),
                {"owner": locked},
            )
            result = ScheduledFavoriteRefresh(sessions).run()
            assert result == FavoriteSweep(1, 1, 0)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(lambda _: ScheduledFavoriteRefresh(sessions).run(), range(2))
            )
        assert sum(result.refreshed for result in results) == 1
        with sessions() as session:
            assert (
                session.scalar(
                    text(
                        "SELECT generation FROM library.favorite_artist_refresh "
                        "WHERE user_id=:owner"
                    ),
                    {"owner": locked},
                )
                == 1
            )
            assert (
                session.scalar(
                    text(
                        "SELECT generation FROM library.favorite_artist_refresh "
                        "WHERE user_id=:owner"
                    ),
                    {"owner": healthy},
                )
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "fault", ["stale", "future_refresh", "future_projection", "wrong_algorithm", "wrong_generation"]
)
@pytest.mark.parametrize("manual", [False, True])
def test_invalid_inferred_authority_is_rejected_by_reads_feed_scan_and_acquisition(
    database_url: str,
    fault: str,
    manual: bool,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            now = datetime.now(UTC) - timedelta(minutes=1)
            owner, device = _owner(session, "freshness owner")
            artist, recordings = _artist(session, "Freshness artist")
            _plays(session, owner, device, recordings, now)
            session.add(
                ExternalReferenceRow(
                    provider_id=JAMENDO_PROVIDER_ID,
                    artist_id=artist,
                    external_entity_type="ARTIST",
                    external_id="20",
                    market_scope="GLOBAL",
                )
            )
            session.flush()
            watch = ReleaseWatchRepository(session)
            watch.refresh_favorites(owner, now=now)
            run = _run(session, owner, artist, now)
            _candidate(session, owner, artist, run, "711", now - timedelta(days=1))
            feed = ReleaseFeedRepository(session)
            assert len(feed.list_page(owner, as_of=datetime.now(UTC), limit=20)) == 1
            mutations = {
                "stale": "UPDATE library.favorite_artist_refresh SET "
                "refreshed_at=clock_timestamp()-INTERVAL '24 hours' WHERE user_id=:owner",
                "future_refresh": "UPDATE library.favorite_artist_refresh SET "
                "refreshed_at=clock_timestamp()+INTERVAL '1 hour' WHERE user_id=:owner",
                "future_projection": "UPDATE library.favorite_artist_projection SET "
                "projected_at=clock_timestamp()+INTERVAL '1 hour' WHERE user_id=:owner",
                "wrong_algorithm": "UPDATE library.favorite_artist_refresh SET "
                "algorithm_version='future_algorithm' WHERE user_id=:owner",
                "wrong_generation": "UPDATE library.favorite_artist_projection SET "
                "generation=generation+1 WHERE user_id=:owner",
            }
            session.execute(text(mutations[fault]), {"owner": owner})
            if manual:
                watch.set_manual_follow(owner, artist, follow=True, now=now)
            assert len(watch.list_effective(owner)) == int(manual)
            assert len(
                feed.list_page(
                    owner,
                    as_of=datetime.now(UTC),
                    authority_now=datetime.now(UTC) + timedelta(days=90),
                    limit=20,
                )
            ) == int(manual)
            assert watch.monitoring_status(owner, artist).state == (
                "AUTOMATIC_CHECK_DISABLED" if manual else "NOT_WATCHED"
            )
            if manual:
                require_effective_watch(
                    session, owner, artist, "20", provider_id=JAMENDO_PROVIDER_ID
                )
                assert watch.list_effective(owner)[0].favorite_generation is None
            else:
                with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                    require_effective_watch(
                        session, owner, artist, "20", provider_id=JAMENDO_PROVIDER_ID
                    )
    finally:
        engine.dispose()


def test_stale_inferred_watch_cannot_create_policy_but_manual_follow_can(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            now = datetime.now(UTC) - timedelta(days=2)
            owner, device = _owner(session, "stale bridge")
            artist, recordings = _artist(session, "Stale bridge")
            _plays(session, owner, device, recordings, now)
            session.add(
                ExternalReferenceRow(
                    provider_id=JAMENDO_PROVIDER_ID,
                    artist_id=artist,
                    external_entity_type="ARTIST",
                    external_id="333",
                    market_scope="GLOBAL",
                )
            )
            session.flush()
            watch = ReleaseWatchRepository(session)
            watch.refresh_favorites(owner, now=now)
            assert reconcile_watch_policies(session, now=now, limit=20, interval_hours=24) == 0
            watch.refresh_favorites(owner, now=datetime.now(UTC))
            assert (
                reconcile_watch_policies(
                    session, now=datetime.now(UTC), limit=20, interval_hours=24
                )
                == 1
            )
            require_effective_watch(session, owner, artist, "333", provider_id=JAMENDO_PROVIDER_ID)
            session.execute(
                text(
                    "UPDATE library.favorite_artist_refresh SET "
                    "refreshed_at=clock_timestamp()-INTERVAL '24 hours' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                require_effective_watch(
                    session, owner, artist, "333", provider_id=JAMENDO_PROVIDER_ID
                )
            watch.set_manual_follow(owner, artist, follow=True, now=now)
            require_effective_watch(session, owner, artist, "333", provider_id=JAMENDO_PROVIDER_ID)
            watch.set_manual_follow(owner, artist, follow=False, now=now)
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                require_effective_watch(
                    session, owner, artist, "333", provider_id=JAMENDO_PROVIDER_ID
                )
    finally:
        engine.dispose()


def test_retry_migration_refuses_retained_state_and_round_trips_empty(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    # env.py obtains its URL from the environment, not Config's URL field.
    # Canonical helpers scope and restore that environment for this generated DB.
    database_harness.upgrade(empty_database_name, "0067_release_watch_auto_download")
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as conn:
        owner = conn.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('retry owner','USER') RETURNING user_id"
        ).fetchone()
        assert owner is not None
        conn.execute(
            "INSERT INTO library.favorite_artist_refresh_retry "
            "VALUES(%s,now(),now()+INTERVAL '24 hours')",
            (owner[0],),
        )
        conn.commit()
    with pytest.raises(DBAPIError, match="favorite_refresh_retry_retained"):
        database_harness.downgrade(empty_database_name, "0067_release_watch_auto_download")
    with database_harness.connect(empty_database_name) as conn:
        conn.execute("DELETE FROM library.favorite_artist_refresh_retry")
        conn.commit()
    database_harness.downgrade(empty_database_name, "0067_release_watch_auto_download")
    database_harness.upgrade(empty_database_name)


def test_expired_inferred_watch_fences_queued_claim_provider_io_and_page_commit(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            now = datetime.now(UTC)
            owner, device = _owner(session, "expiry checkpoint owner")
            artist, recordings = _artist(session, "Expiry checkpoint")
            _plays(session, owner, device, recordings, now - timedelta(days=2))
            session.add(
                ExternalReferenceRow(
                    provider_id=JAMENDO_PROVIDER_ID,
                    artist_id=artist,
                    external_entity_type="ARTIST",
                    external_id="334",
                    market_scope="GLOBAL",
                )
            )
            session.flush()
            watch = ReleaseWatchRepository(session)
            watch.refresh_favorites(owner, now=now - timedelta(days=2))
            watch.refresh_favorites(owner, now=now)
            automation = PostgresDiscoveryAutomationRepository(session)
            assert automation.dispatch_due(now=now, limit=20) == 1
            run = session.scalar(select(DiscoveryRunRow).where(DiscoveryRunRow.user_id == owner))
            assert run is not None and run.job_id is not None
            job = session.get(JobRow, run.job_id)
            assert job is not None
            job.state, job.lease_owner = "RUNNING", "expiry-worker"
            job.lease_deadline, job.attempt_count = now + timedelta(hours=1), 1
            session.flush()
            fence = LeaseFence(job.job_id, "expiry-worker", 1)
            assert (
                automation.claim_scan(run_id=run.run_id, owner_user_id=owner, fence=fence, now=now)
                is not None
            )
            session.execute(
                text(
                    "UPDATE library.favorite_artist_refresh SET "
                    "refreshed_at=clock_timestamp()-INTERVAL '24 hours' WHERE user_id=:owner"
                ),
                {"owner": owner},
            )
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                automation.claim_scan(run_id=run.run_id, owner_user_id=owner, fence=fence, now=now)
            for boundary in ("BEFORE_PROVIDER_IO", "PAGE_COMMIT", "ACQUISITION_CLAIM"):
                with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                    automation.require_current(
                        run_id=run.run_id, owner_user_id=owner, fence=fence, boundary=boundary
                    )
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                automation.commit_page(
                    run_id=run.run_id,
                    owner_user_id=owner,
                    fence=fence,
                    page=ProviderTrackPage("334", 0, (), None, None),
                    now=now,
                )
            assert (
                session.scalar(
                    text("SELECT count(*) FROM discovery.run_page WHERE run_id=:run"),
                    {"run": run.run_id},
                )
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "fault", ["wrong_generation", "wrong_algorithm", "future_projection", "future_refresh"]
)
def test_invalid_projection_ancestry_starts_new_eligibility_instant(
    database_url: str,
    fault: str,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, session.begin():
            old = datetime.now(UTC) - timedelta(days=2)
            owner, device = _owner(session, "continuity owner")
            artist, recordings = _artist(session, "Continuity artist")
            _plays(session, owner, device, recordings, old)
            watch = ReleaseWatchRepository(session)
            watch.refresh_favorites(owner, now=old)
            mutations = {
                "wrong_generation": "UPDATE library.favorite_artist_projection SET "
                "generation=generation+1 WHERE user_id=:owner",
                "wrong_algorithm": "UPDATE library.favorite_artist_refresh SET "
                "algorithm_version='old_algorithm' WHERE user_id=:owner",
                "future_projection": "UPDATE library.favorite_artist_projection SET "
                "projected_at=clock_timestamp()+INTERVAL '1 hour' WHERE user_id=:owner",
                "future_refresh": "UPDATE library.favorite_artist_refresh SET "
                "refreshed_at=clock_timestamp()+INTERVAL '1 hour' WHERE user_id=:owner",
            }
            session.execute(text(mutations[fault]), {"owner": owner})
            current = datetime.now(UTC)
            assert watch.refresh_favorites(owner, now=current).generation == 2
            projected = session.scalar(
                text(
                    "SELECT projected_at FROM "
                    "library.favorite_artist_projection WHERE user_id=:owner AND artist_id=:artist"
                ),
                {"owner": owner, "artist": artist},
            )
            assert projected == current
    finally:
        engine.dispose()
