"""Staged watch consent, zero-history policy, and acquisition revocation fences."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.discovery_automation_runtime import (
    PostgresDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.discovery_runtime import (
    JAMENDO_PROVIDER_ID,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    ArtistPolicyRow,
    DiscoveryCandidateRow,
    DiscoveryRunRow,
    ExternalReferenceRow,
    JobRow,
)
from autplay.adapters.postgresql.release_watch import ReleaseWatchRepository
from autplay.adapters.postgresql.release_watch_dispatch import reconcile_watch_policies
from autplay.adapters.postgresql.watch_auto_download_consent import (
    WatchAutoDownloadError,
    WatchAutoDownloadRepository,
)
from autplay.application.discovery_automation import AUTO_IMPORT_CONFIRMATION, PolicyMutation
from autplay.domain.discovery import ProviderTrackObservation, ProviderTrackPage
from autplay.domain.jobs import LeaseFence

from .test_discovery_runtime import _track
from .test_release_watch_runtime import _artist, _owner


def _watch_policy(
    session: Session, owner_id: UUID, artist_id: UUID, *, now: datetime
) -> ArtistPolicyRow:
    session.add(
        ExternalReferenceRow(
            provider_id=JAMENDO_PROVIDER_ID,
            external_entity_type="ARTIST",
            external_id="20",
            market_scope="GLOBAL",
            artist_id=artist_id,
        )
    )
    ReleaseWatchRepository(session).set_manual_follow(
        owner_id, artist_id, follow=True, now=now - timedelta(hours=25)
    )
    session.flush()
    assert reconcile_watch_policies(session, now=now, limit=20, interval_hours=24) == 1
    policy = session.scalar(
        select(ArtistPolicyRow).where(
            ArtistPolicyRow.user_id == owner_id,
            ArtistPolicyRow.canonical_artist_id == artist_id,
        )
    )
    assert policy is not None and policy.import_mode == "REVIEW_REQUIRED"
    return policy


def _enable_gate(session: Session) -> None:
    session.execute(text("UPDATE discovery.watch_auto_download_gate SET enabled=true"))


def _wait_for_revocation_lock(engine: Engine, checker_pid: int, pending: Future[None]) -> None:
    until = monotonic() + 5
    with Session(engine) as observer:
        while True:
            observer.execute(text("SELECT pg_stat_clear_snapshot()"))
            blocked = observer.scalar(
                text("""
                    SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE :checker = ANY(pg_blocking_pids(pid)))
                """),
                {"checker": checker_pid},
            )
            if blocked:
                return
            assert not pending.done()
            assert monotonic() < until, "revocation did not wait on the authority lock"
            sleep(0.005)


def _confirm_policy(
    session: Session, owner_id: UUID, artist_id: UUID, *, now: datetime
) -> ArtistPolicyRow:
    result = PostgresDiscoveryAutomationRepository(session).set_policy(
        owner_user_id=owner_id,
        command=PolicyMutation(
            canonical_artist_id=artist_id,
            provider_artist_id="20",
            discovery_mode="SCHEDULED",
            import_mode="AUTO_IMPORT",
            automation_enabled=True,
            expected_revision=1,
            operation_id=uuid4(),
            confirmation_code=AUTO_IMPORT_CONFIRMATION,
        ),
        request_sha256=b"m" * 32,
        now=now,
    )
    policy = session.get(ArtistPolicyRow, result.policy.policy_id)
    assert policy is not None and policy.current_revision == 2
    return policy


def test_owner_consent_is_default_off_gate_fenced_and_artist_effective_state(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session, session.begin():
            owner_id, _ = _owner(session, "watch-consent-owner")
            other_id, _ = _owner(session, "watch-consent-other")
            artist_id, _ = _artist(session, "Watch consent artist")
            consent = WatchAutoDownloadRepository(session)
            assert consent.get(owner_id).enabled is False
            assert consent.get(owner_id).revision == 0
            assert consent.get(owner_id).execution_enabled is False
            with pytest.raises(WatchAutoDownloadError, match="unavailable"):
                consent.set(
                    owner_id,
                    enabled=True,
                    expected_revision=0,
                    confirmation_code=AUTO_IMPORT_CONFIRMATION,
                    now=now,
                )
            assert consent.get(owner_id).revision == 0
            assert (
                consent.set(
                    owner_id,
                    enabled=False,
                    expected_revision=0,
                    confirmation_code=None,
                    now=now,
                ).revision
                == 0
            )
            _watch_policy(session, owner_id, artist_id, now=now)
            _confirm_policy(session, owner_id, artist_id, now=now)
            staged = ReleaseWatchRepository(session).list_effective(owner_id)[0].auto_download
            assert staged.policy_import_mode == "AUTO_IMPORT"
            assert staged.policy_revision == 2
            assert staged.provider_artist_id == "20"
            assert staged.effective_opt_in is False
            assert staged.reason == "STAGED_GATE_OFF"
            assert session.scalar(text("SELECT count(*) FROM jobs.job")) == 0
            policy = session.scalar(
                select(ArtistPolicyRow).where(ArtistPolicyRow.user_id == owner_id)
            )
            assert policy is not None
            automation = PostgresDiscoveryAutomationRepository(session)
            run = automation.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"g" * 32,
                now=now,
            )
            durable = session.get(DiscoveryRunRow, run.run_id)
            assert durable is not None and durable.job_id is not None
            scan_job = session.get(JobRow, durable.job_id)
            assert scan_job is not None
            scan_job.state, scan_job.lease_owner, scan_job.attempt_count = (
                "RUNNING",
                "staged-watch",
                1,
            )
            scan_job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(scan_job.job_id, "staged-watch", 1)
            assert (
                automation.claim_scan(
                    run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
                )
                is not None
            )
            automation.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("980"), date(2026, 8, 5), "UTC"),),
                    None,
                    "watch:staged",
                ),
                now=now,
            )
            assert (
                session.scalar(
                    text("""
                SELECT count(*) FROM jobs.job WHERE job_type='discovery.acquire'
            """)
                )
                == 0
            )
            _enable_gate(session)
            with pytest.raises(WatchAutoDownloadError, match="confirmation_invalid"):
                consent.set(
                    owner_id,
                    enabled=True,
                    expected_revision=0,
                    confirmation_code=None,
                    now=now,
                )
            owner_off = ReleaseWatchRepository(session).list_effective(owner_id)[0].auto_download
            assert owner_off.reason == "OWNER_CONSENT_OFF"
            granted = consent.set(
                owner_id,
                enabled=True,
                expected_revision=0,
                confirmation_code=AUTO_IMPORT_CONFIRMATION,
                now=now,
            )
            assert (granted.enabled, granted.revision, granted.execution_enabled) == (True, 1, True)
            ready = ReleaseWatchRepository(session).list_effective(owner_id)[0].auto_download
            assert ready.effective_opt_in and ready.execution_enabled and ready.reason == "READY"
            assert consent.get(other_id).enabled is False
            assert (
                session.scalar(
                    text("""
                SELECT count(*) FROM jobs.job WHERE job_type='discovery.acquire'
            """)
                )
                == 0
            )
            session.execute(text("UPDATE discovery.watch_auto_download_gate SET enabled=false"))
            session.execute(text("UPDATE discovery.watch_auto_download_gate SET enabled=true"))
            assert consent.get(owner_id).enabled is False
            assert consent.get(owner_id).revision == 1
            renewed = consent.set(
                owner_id,
                enabled=True,
                expected_revision=1,
                confirmation_code=AUTO_IMPORT_CONFIRMATION,
                now=now,
            )
            assert renewed.revision == 2 and renewed.enabled
            disabled = consent.set(
                owner_id,
                enabled=False,
                expected_revision=2,
                confirmation_code=None,
                now=now,
            )
            assert disabled.revision == 3 and not disabled.enabled
            with pytest.raises(WatchAutoDownloadError, match="revision_stale"):
                consent.set(
                    owner_id,
                    enabled=False,
                    expected_revision=2,
                    confirmation_code=None,
                    now=now,
                )
            assert (
                session.scalar(
                    text("""
                SELECT count(*) FROM discovery.watch_auto_download_consent_revision
                WHERE user_id=:owner
            """),
                    {"owner": owner_id},
                )
                == 3
            )
            with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
                session.execute(
                    text("""
                    UPDATE discovery.watch_auto_download_consent_revision
                    SET enabled=true WHERE user_id=:owner AND revision=1
                """),
                    {"owner": owner_id},
                )
            with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
                session.execute(
                    text("""
                    DELETE FROM discovery.watch_auto_download_consent_revision
                    WHERE user_id=:owner AND revision=1
                """),
                    {"owner": owner_id},
                )
    finally:
        engine.dispose()


def test_queued_watch_acquisition_is_fenced_after_consent_and_follow_revocation(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _owner(session, "watch-acquisition-owner")
            artist_id, _ = _artist(session, "Watch acquisition artist")
            _watch_policy(session, owner_id, artist_id, now=now)
            _confirm_policy(session, owner_id, artist_id, now=now)
            _enable_gate(session)
            WatchAutoDownloadRepository(session).set(
                owner_id,
                enabled=True,
                expected_revision=0,
                confirmation_code=AUTO_IMPORT_CONFIRMATION,
                now=now,
            )
            automation = PostgresDiscoveryAutomationRepository(session)
            policy = session.scalar(
                select(ArtistPolicyRow).where(ArtistPolicyRow.user_id == owner_id)
            )
            assert policy is not None
            run = automation.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"r" * 32,
                now=now,
            )
            durable = session.get(DiscoveryRunRow, run.run_id)
            assert durable is not None and durable.job_id is not None
            job = session.get(JobRow, durable.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "watch-test", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "watch-test", 1)
            assert (
                automation.claim_scan(
                    run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
                )
                is not None
            )
            automation.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("981"), date(2026, 8, 5), "UTC"),),
                    None,
                    "watch:auto",
                ),
                now=now,
            )
            attempt = session.scalar(
                select(AcquisitionAttemptRow).where(AcquisitionAttemptRow.origin == "AUTOMATIC")
            )
            assert attempt is not None and attempt.job_id is not None
            candidate = session.get(DiscoveryCandidateRow, attempt.candidate_id)
            assert candidate is not None
            candidate_id = candidate.candidate_id
            attempt_id = attempt.acquisition_attempt_id
            job_id = attempt.job_id
            acquisition_job = session.get(JobRow, job_id)
            assert acquisition_job is not None
            acquisition_job.state = "RUNNING"
            acquisition_job.lease_owner = "watch-acquire"
            acquisition_job.attempt_count = 1
            acquisition_job.lease_deadline = now + timedelta(minutes=5)
        with sessions.begin() as session:
            PostgresBulkDiscoveryRepository(session).require_persistent_acquisition_authority(
                candidate_id=candidate_id,
                owner_user_id=owner_id,
                acquisition_attempt_id=attempt_id,
                authority_now=lambda: datetime.now(UTC),
            )

        entered, release, revoke_started = Event(), Event(), Event()
        checker_pids: list[int] = []

        def checked_boundary() -> None:
            with sessions.begin() as session:
                pid = session.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                checker_pids.append(pid)

                def wait_at_boundary() -> datetime:
                    entered.set()
                    assert release.wait(timeout=10)
                    return datetime.now(UTC)

                PostgresBulkDiscoveryRepository(session).require_persistent_acquisition_authority(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt_id,
                    authority_now=wait_at_boundary,
                )

        def revoke_consent() -> None:
            revoke_started.set()
            with sessions.begin() as session:
                WatchAutoDownloadRepository(session).set(
                    owner_id,
                    enabled=False,
                    expected_revision=1,
                    confirmation_code=None,
                    now=datetime.now(UTC),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            checking = pool.submit(checked_boundary)
            assert entered.wait(timeout=10)
            revoking = pool.submit(revoke_consent)
            assert revoke_started.wait(timeout=10)
            _wait_for_revocation_lock(engine, checker_pids[-1], revoking)
            release.set()
            checking.result(timeout=15)
            revoking.result(timeout=15)

        with sessions.begin() as session:
            with pytest.raises(BulkDiscoveryError, match="policy_revision_stale"):
                PostgresBulkDiscoveryRepository(session).require_persistent_acquisition_authority(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt_id,
                    authority_now=lambda: datetime.now(UTC),
                )
            with pytest.raises(BulkDiscoveryError, match="policy_revision_stale"):
                PostgresBulkDiscoveryRepository(session).claim_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=LeaseFence(job_id, "watch-acquire", 1),
                    automatic_enabled=True,
                )
            with pytest.raises(BulkDiscoveryError, match="policy_revision_stale"):
                PostgresBulkDiscoveryRepository(session).require_ingest_boundary(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt_id,
                    automatic_enabled=True,
                )
        with sessions.begin() as session:
            WatchAutoDownloadRepository(session).set(
                owner_id,
                enabled=True,
                expected_revision=2,
                confirmation_code=AUTO_IMPORT_CONFIRMATION,
                now=datetime.now(UTC),
            )
        entered, release, revoke_started = Event(), Event(), Event()

        def revoke_follow() -> None:
            revoke_started.set()
            with sessions.begin() as session:
                ReleaseWatchRepository(session).set_manual_follow(
                    owner_id, artist_id, follow=False, now=datetime.now(UTC)
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            checking = pool.submit(checked_boundary)
            assert entered.wait(timeout=10)
            revoking = pool.submit(revoke_follow)
            assert revoke_started.wait(timeout=10)
            _wait_for_revocation_lock(engine, checker_pids[-1], revoking)
            release.set()
            checking.result(timeout=15)
            revoking.result(timeout=15)
        with sessions.begin() as session:
            with pytest.raises(BulkDiscoveryError, match="policy_revision_stale"):
                PostgresBulkDiscoveryRepository(session).require_persistent_acquisition_authority(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt_id,
                    authority_now=lambda: datetime.now(UTC),
                )
            job = session.get(JobRow, job_id)
            assert job is not None and job.job_type == "discovery.acquire"
    finally:
        engine.dispose()
