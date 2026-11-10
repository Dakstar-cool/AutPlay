"""Scheduled M11 watch bridge and revocation fences on disposable PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.discovery_automation_runtime import (
    PostgresDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.discovery_runtime import JAMENDO_PROVIDER_ID
from autplay.adapters.postgresql.models import (
    ArtistPolicyRevisionRow,
    ArtistPolicyRow,
    ArtistRow,
    DiscoveryRunRow,
    ExternalReferenceRow,
    JobRow,
)
from autplay.adapters.postgresql.release_watch import ReleaseWatchRepository
from autplay.adapters.postgresql.release_watch_dispatch import is_watch_policy
from autplay.application.discovery_automation import (
    AUTO_IMPORT_CONFIRMATION,
    DiscoveryAutomationError,
    PolicyMutation,
)
from autplay.domain.jobs import LeaseFence

from .test_discovery_automation_runtime import _bind_provider_artist
from .test_discovery_runtime import _seed_import
from .test_release_watch_runtime import _artist, _owner, _plays


def test_zero_history_follows_wait_for_global_interval_and_fence_revocation(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    due = now + timedelta(hours=25)
    try:
        with Session(engine) as session, session.begin():
            owner_a, _ = _owner(session, "watch-dispatch-a")
            owner_b, _ = _owner(session, "watch-dispatch-b")
            owner_c, device_c = _owner(session, "watch-dispatch-inferred")
            artist_a, _ = _artist(session, "Watch dispatch A")
            artist_b, _ = _artist(session, "Watch dispatch B")
            artist_c, recordings_c = _artist(session, "Watch dispatch inferred")
            artist_unmapped, _ = _artist(session, "Watch dispatch unmapped")
            artist_explicit, _ = _artist(session, "Watch dispatch explicit policy")
            session.add_all(
                (
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91001",
                        market_scope="GLOBAL",
                        artist_id=artist_a,
                    ),
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91002",
                        market_scope="GLOBAL",
                        artist_id=artist_b,
                    ),
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91003",
                        market_scope="GLOBAL",
                        artist_id=artist_c,
                    ),
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91006",
                        market_scope="GLOBAL",
                        artist_id=artist_explicit,
                    ),
                )
            )
            watch = ReleaseWatchRepository(session)
            watch.set_manual_follow(owner_a, artist_a, follow=True, now=now)
            watch.set_manual_follow(owner_b, artist_b, follow=True, now=now)
            watch.set_manual_follow(owner_a, artist_unmapped, follow=True, now=now)
            watch.set_manual_follow(owner_a, artist_explicit, follow=True, now=now)
            explicit_policy = ArtistPolicyRow(
                policy_id=uuid4(),
                user_id=owner_a,
                canonical_artist_id=artist_explicit,
                provider_id=JAMENDO_PROVIDER_ID,
                provider_artist_id="91006",
                discovery_mode="MANUAL_ONLY",
                import_mode="REVIEW_REQUIRED",
                automation_enabled=False,
                current_revision=1,
                next_eligible_at=None,
            )
            session.add(explicit_policy)
            session.flush([explicit_policy])
            session.add(
                ArtistPolicyRevisionRow(
                    policy_id=explicit_policy.policy_id,
                    owner_user_id=owner_a,
                    revision=1,
                    discovery_mode="MANUAL_ONLY",
                    import_mode="REVIEW_REQUIRED",
                    automation_enabled=False,
                    change_kind="OWNER_CONFIRMED",
                    operation_id=uuid4(),
                    request_sha256=b"e" * 32,
                )
            )
            _plays(session, owner_c, device_c, recordings_c, now)
            assert watch.refresh_favorites(owner_c, now=now).favorite_count == 1
            assert watch.monitoring_status(owner_a, artist_unmapped).state == (
                "PROVIDER_MAPPING_UNAVAILABLE"
            )
            session.add_all(
                (
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91004",
                        market_scope="GLOBAL",
                        artist_id=artist_unmapped,
                    ),
                    ExternalReferenceRow(
                        provider_id=JAMENDO_PROVIDER_ID,
                        external_entity_type="ARTIST",
                        external_id="91005",
                        market_scope="GLOBAL",
                        artist_id=artist_unmapped,
                    ),
                )
            )
            session.flush()
            assert watch.monitoring_status(owner_a, artist_unmapped).state == (
                "IDENTITY_REVIEW_REQUIRED"
            )
            assert session.scalar(text("SELECT count(*) FROM jobs.job")) == 0

        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            assert automation.dispatch_due(now=now, limit=20) == 0
            policies = session.scalars(select(ArtistPolicyRow)).all()
            assert {(p.user_id, p.canonical_artist_id) for p in policies} == {
                (owner_a, artist_a),
                (owner_b, artist_b),
                (owner_c, artist_c),
                (owner_a, artist_explicit),
            }
            explicit = next(p for p in policies if p.canonical_artist_id == artist_explicit)
            assert explicit.discovery_mode == "MANUAL_ONLY"
            assert explicit.current_revision == 1
            watch = ReleaseWatchRepository(session)
            assert watch.monitoring_status(owner_a, artist_a).state == "SCHEDULED"
            assert watch.monitoring_status(owner_a, artist_explicit).state == (
                "AUTOMATIC_CHECK_DISABLED"
            )
            assert all(p.import_mode == "REVIEW_REQUIRED" for p in policies)
            assert all(
                p.next_eligible_at is not None and p.next_eligible_at >= now + timedelta(hours=24)
                for p in policies
                if p.canonical_artist_id != artist_explicit
            )
            assert session.scalar(text("SELECT count(*) FROM jobs.job")) == 0

        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            assert automation.dispatch_due(now=due, limit=20) == 3
            runs = session.scalars(select(DiscoveryRunRow)).all()
            assert len(runs) == 3
            assert {run.user_id for run in runs} == {owner_a, owner_b, owner_c}
            assert (
                session.scalar(
                    text("SELECT count(*) FROM jobs.job WHERE job_type='discovery.acquire'")
                )
                == 0
            )
            assert all(session.get(JobRow, run.job_id) is not None for run in runs)

        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            run_a = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.user_id == owner_a)
            )
            assert run_a is not None and run_a.job_id is not None
            job_a = session.get(JobRow, run_a.job_id)
            assert job_a is not None
            job_a.state = "RUNNING"
            job_a.lease_owner = "m11-test"
            job_a.lease_deadline = due + timedelta(hours=48)
            job_a.attempt_count = 1
            assert (
                automation.claim_scan(
                    run_id=run_a.run_id,
                    owner_user_id=owner_a,
                    fence=LeaseFence(job_a.job_id, "m11-test", 1),
                    now=due,
                )
                is not None
            )

        with Session(engine) as session, session.begin():
            watch = ReleaseWatchRepository(session)
            watch.set_manual_follow(owner_a, artist_a, follow=False, now=due)
            assert watch.monitoring_status(owner_a, artist_a).state == "EXCLUDED"

        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            run_a = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.user_id == owner_a)
            )
            run_b = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.user_id == owner_b)
            )
            assert run_a is not None and run_b is not None
            assert run_a.job_id is not None and run_b.job_id is not None
            job_a = session.get(JobRow, run_a.job_id)
            job_b = session.get(JobRow, run_b.job_id)
            assert job_a is not None and job_b is not None
            job_b.state = "RUNNING"
            job_b.lease_owner = "m11-test"
            job_b.lease_deadline = due + timedelta(hours=1)
            job_b.attempt_count = 1
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                automation.require_current(
                    run_id=run_a.run_id,
                    owner_user_id=owner_a,
                    fence=LeaseFence(job_a.job_id, "m11-test", 1),
                    boundary="PAGE_COMMIT",
                )
            assert (
                automation.claim_scan(
                    run_id=run_b.run_id,
                    owner_user_id=owner_b,
                    fence=LeaseFence(job_b.job_id, "m11-test", 1),
                    now=due,
                )
                is not None
            )

        with Session(engine) as session, session.begin():
            ReleaseWatchRepository(session).set_manual_follow(
                owner_a, artist_a, follow=True, now=due + timedelta(minutes=1)
            )
        with Session(engine) as session, session.begin():
            run_a = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.user_id == owner_a)
            )
            assert run_a is not None and run_a.job_id is not None
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                PostgresDiscoveryAutomationRepository(session).claim_scan(
                    run_id=run_a.run_id,
                    owner_user_id=owner_a,
                    fence=LeaseFence(run_a.job_id, "m11-test", 1),
                    now=due + timedelta(hours=26),
                )
    finally:
        engine.dispose()


def test_confirmed_revision_keeps_watch_revocation_fence(database_url: str) -> None:
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session, session.begin():
            owner_id, _ = _seed_import(session, "watch-confirmed-revision")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
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

        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            assert automation.dispatch_due(now=now, limit=20) == 1
            policy = session.scalar(
                select(ArtistPolicyRow).where(ArtistPolicyRow.user_id == owner_id)
            )
            assert policy is not None and is_watch_policy(session, policy)
            changed = automation.set_policy(
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
                request_sha256=b"r" * 32,
                now=now + timedelta(minutes=1),
            )
            assert changed.policy.revision == 2
            assert policy.import_mode == "AUTO_IMPORT"
            assert is_watch_policy(session, policy)

        due = now + timedelta(hours=25)
        with Session(engine) as session, session.begin():
            automation = PostgresDiscoveryAutomationRepository(session)
            assert automation.dispatch_due(now=due, limit=20) == 1
            run = session.scalar(
                select(DiscoveryRunRow).where(
                    DiscoveryRunRow.user_id == owner_id,
                    DiscoveryRunRow.policy_revision == 2,
                )
            )
            assert run is not None and run.job_id is not None
            job = session.get(JobRow, run.job_id)
            assert job is not None
            job.state = "RUNNING"
            job.lease_owner = "m11-revision-test"
            job.lease_deadline = due + timedelta(hours=1)
            job.attempt_count = 1
            assert (
                automation.claim_scan(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=LeaseFence(job.job_id, "m11-revision-test", 1),
                    now=due,
                )
                is not None
            )

        with Session(engine) as session, session.begin():
            ReleaseWatchRepository(session).set_manual_follow(
                owner_id, artist_id, follow=False, now=due + timedelta(minutes=1)
            )

        with Session(engine) as session, session.begin():
            run = session.scalar(
                select(DiscoveryRunRow).where(
                    DiscoveryRunRow.user_id == owner_id,
                    DiscoveryRunRow.policy_revision == 2,
                )
            )
            assert run is not None and run.job_id is not None
            with pytest.raises(DiscoveryAutomationError, match="discovery_not_eligible"):
                PostgresDiscoveryAutomationRepository(session).require_current(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=LeaseFence(run.job_id, "m11-revision-test", 1),
                    boundary="PAGE_COMMIT",
                )
    finally:
        engine.dispose()
