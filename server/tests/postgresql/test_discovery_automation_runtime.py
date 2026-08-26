"""Real PostgreSQL evidence for A1C policy and bounded release-run persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
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
    ArtistCreditNameRow,
    ArtistPolicyRevisionRow,
    ArtistPolicyRow,
    ArtistRow,
    CandidateActionReceiptRow,
    DiscoveryCandidateRow,
    DiscoveryRunPageRow,
    DiscoveryRunRow,
    JobRow,
    LibraryEntryRow,
    RecordingRow,
    SourceAuthorizationRow,
    SourceProviderRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.discovery_automation import (
    DiscoveryAutomationError,
    PolicyMutation,
)
from autplay.domain.discovery import ProviderTrackObservation, ProviderTrackPage
from autplay.domain.jobs import LeaseFence
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .test_discovery_runtime import _seed_import, _seed_owned_artist, _sessions, _track


def _bind_provider_artist(
    session: Session, owner_id: UUID, artist_id: UUID, *, track_id: str = "10"
) -> None:
    candidate = _track(track_id)
    session.add(
        DiscoveryCandidateRow(
            candidate_id=uuid4(),
            user_id=owner_id,
            provider_id=JAMENDO_PROVIDER_ID,
            canonical_artist_id=artist_id,
            market_scope="GLOBAL",
            provider_track_id=candidate.provider_track_id,
            provider_artist_id=candidate.provider_artist_id,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
            duration_seconds=candidate.duration_seconds,
            license_url=candidate.license_url,
            share_url=candidate.share_url,
            disposition="SELECTABLE",
            acquisition_state="NOT_REQUESTED",
            source_authorization_revision=1,
        )
    )


def _command(
    artist_id: UUID,
    operation_id: UUID,
    *,
    expected_revision: int | None = None,
    import_mode: str = "REVIEW_REQUIRED",
    discovery_mode: str = "SCHEDULED",
) -> PolicyMutation:
    return PolicyMutation(
        canonical_artist_id=artist_id,
        provider_artist_id="20",
        discovery_mode=discovery_mode,
        import_mode=import_mode,
        automation_enabled=discovery_mode == "SCHEDULED",
        expected_revision=expected_revision,
        operation_id=operation_id,
        confirmation_code=(
            "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
            if import_mode == "AUTO_IMPORT"
            else None
        ),
    )


def test_policy_replay_cas_owner_and_provider_gate(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C policy owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            command = _command(artist_id, uuid4())
            first = PostgresDiscoveryAutomationRepository(session).set_policy(
                owner_user_id=owner_id, command=command, request_sha256=b"a" * 32, now=now
            )
        with sessions.begin() as session:
            replay = PostgresDiscoveryAutomationRepository(session).set_policy(
                owner_user_id=owner_id, command=command, request_sha256=b"a" * 32, now=now
            )
            assert replay.replayed and replay.policy.policy_id == first.policy.policy_id
        with (
            sessions() as session,
            pytest.raises(DiscoveryAutomationError, match="operation_conflict"),
        ):
            PostgresDiscoveryAutomationRepository(session).set_policy(
                owner_user_id=owner_id, command=command, request_sha256=b"b" * 32, now=now
            )
        with sessions.begin() as session:
            provider = session.get(SourceProviderRow, JAMENDO_PROVIDER_ID)
            assert provider is not None
            provider.enabled = False
        with sessions.begin() as session:
            assert (
                PostgresDiscoveryAutomationRepository(session).dispatch_due(now=now, limit=1) == 0
            )
    finally:
        engine.dispose()


def test_policy_operation_conflict_and_original_revision_replay_survive_later_change(
    database_url: str,
) -> None:
    """An idempotency receipt is immutable evidence, not current provider state."""

    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C immutable policy receipt owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            operation_id = uuid4()
            original = _command(artist_id, operation_id)
            first = repository.set_policy(
                owner_user_id=owner_id, command=original, request_sha256=b"u" * 32, now=now
            )
            with pytest.raises(DiscoveryAutomationError, match="operation_conflict"):
                repository.set_policy(
                    owner_user_id=owner_id,
                    command=_command(artist_id, operation_id, import_mode="AUTO_IMPORT"),
                    request_sha256=b"v" * 32,
                    now=now,
                )
            changed = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=first.policy.revision,
                    import_mode="AUTO_IMPORT",
                ),
                request_sha256=b"w" * 32,
                now=now + timedelta(minutes=1),
            )
            assert changed.policy.revision == 2
            provider = session.get(SourceProviderRow, JAMENDO_PROVIDER_ID)
            assert provider is not None
            provider.enabled = False
            replay = repository.set_policy(
                owner_user_id=owner_id,
                command=original,
                request_sha256=b"u" * 32,
                now=now + timedelta(minutes=2),
            )
            assert replay.replayed and replay.policy.revision == first.policy.revision == 1
    finally:
        engine.dispose()


def test_web_operation_namespace_is_shared_across_all_a1c_actions(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C global operation owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy_operation = uuid4()
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, policy_operation),
                request_sha256=b"p" * 32,
                now=now,
            ).policy
            with pytest.raises(DiscoveryAutomationError, match="operation_conflict"):
                repository.run_now(
                    owner_user_id=owner_id,
                    policy_id=policy.policy_id,
                    operation_id=policy_operation,
                    request_sha256=b"r" * 32,
                    now=now,
                )

            action_operation = uuid4()
            candidate_id = session.scalar(
                select(DiscoveryCandidateRow.candidate_id).where(
                    DiscoveryCandidateRow.user_id == owner_id
                )
            )
            assert candidate_id is not None
            repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=candidate_id,
                action="SELECT",
                operation_id=action_operation,
                request_sha256=b"a" * 32,
                now=now,
            )
            with pytest.raises(DiscoveryAutomationError, match="operation_conflict"):
                repository.set_policy(
                    owner_user_id=owner_id,
                    command=_command(
                        artist_id,
                        action_operation,
                        expected_revision=policy.revision,
                    ),
                    request_sha256=b"s" * 32,
                    now=now,
                )
    finally:
        engine.dispose()


def test_fail_safe_policy_reduction_works_when_provider_is_disabled(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C fail-safe reduction owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"e" * 32,
                now=now,
            ).policy
            provider = session.get(SourceProviderRow, JAMENDO_PROVIDER_ID)
            assert provider is not None
            provider.enabled = False

            disabled = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=policy.revision,
                    discovery_mode="DISABLED",
                ),
                request_sha256=b"d" * 32,
                now=now + timedelta(seconds=1),
            )
            assert disabled.policy.discovery_mode == "DISABLED"
            assert disabled.policy.revision == policy.revision + 1
    finally:
        engine.dispose()


def test_one_unreachable_owner_policy_does_not_block_due_dispatch(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            first_owner, _ = _seed_import(session, "A1C orphan policy owner")
            first_artist = session.scalar(
                select(ArtistRow.artist_id)
                .join(ArtistCreditNameRow, ArtistCreditNameRow.artist_id == ArtistRow.artist_id)
                .join(
                    RecordingRow,
                    RecordingRow.artist_credit_id == ArtistCreditNameRow.artist_credit_id,
                )
                .join(UserTrackRefRow, UserTrackRefRow.recording_id == RecordingRow.recording_id)
                .where(UserTrackRefRow.user_id == first_owner)
                .limit(1)
            )
            assert first_artist is not None
            _bind_provider_artist(session, first_owner, first_artist, track_id="901")
            first_policy = (
                PostgresDiscoveryAutomationRepository(session)
                .set_policy(
                    owner_user_id=first_owner,
                    command=_command(first_artist, uuid4()),
                    request_sha256=b"1" * 32,
                    now=now,
                )
                .policy
            )

            second_owner, _ = _seed_import(session, "A1C healthy policy owner")
            owned_artists = tuple(
                session.scalars(
                    select(ArtistRow.artist_id)
                    .join(ArtistCreditNameRow, ArtistCreditNameRow.artist_id == ArtistRow.artist_id)
                    .join(
                        RecordingRow,
                        RecordingRow.artist_credit_id == ArtistCreditNameRow.artist_credit_id,
                    )
                    .join(
                        UserTrackRefRow, UserTrackRefRow.recording_id == RecordingRow.recording_id
                    )
                    .where(UserTrackRefRow.user_id == second_owner)
                )
            )
            assert owned_artists
            second_artist = owned_artists[0]
            _bind_provider_artist(session, second_owner, second_artist, track_id="902")
            second_policy = (
                PostgresDiscoveryAutomationRepository(session)
                .set_policy(
                    owner_user_id=second_owner,
                    command=_command(second_artist, uuid4()),
                    request_sha256=b"2" * 32,
                    now=now,
                )
                .policy
            )
            entry = session.scalar(
                select(LibraryEntryRow)
                .join(
                    UserTrackRefRow,
                    UserTrackRefRow.user_track_ref_id == LibraryEntryRow.user_track_ref_id,
                )
                .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
                .join(
                    ArtistCreditNameRow,
                    ArtistCreditNameRow.artist_credit_id == RecordingRow.artist_credit_id,
                )
                .where(
                    LibraryEntryRow.user_id == first_owner,
                    ArtistCreditNameRow.artist_id == first_artist,
                )
            )
            assert entry is not None
            entry.removed_at = now

            dispatched = PostgresDiscoveryAutomationRepository(session).dispatch_due(
                now=now, limit=20
            )
            assert dispatched == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DiscoveryRunRow)
                    .where(DiscoveryRunRow.policy_id == first_policy.policy_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DiscoveryRunRow)
                    .where(DiscoveryRunRow.policy_id == second_policy.policy_id)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_policy_revision_is_inserted_by_set_policy_and_database_immutable(
    database_url: str,
) -> None:
    """The policy API appends history, while PostgreSQL rejects history rewrites."""

    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C immutable revision owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            policy = (
                PostgresDiscoveryAutomationRepository(session)
                .set_policy(
                    owner_user_id=owner_id,
                    command=_command(artist_id, uuid4()),
                    request_sha256=b"r" * 32,
                    now=now,
                )
                .policy
            )
            revision = session.get(ArtistPolicyRevisionRow, (policy.policy_id, policy.revision))
            assert revision is not None and revision.operation_id is not None

            with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
                session.execute(
                    text(
                        "UPDATE discovery.artist_policy_revision "
                        "SET import_mode = 'AUTO_IMPORT' "
                        "WHERE policy_id = :policy_id AND revision = :revision"
                    ),
                    {"policy_id": policy.policy_id, "revision": policy.revision},
                )
            with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
                session.execute(
                    text(
                        "DELETE FROM discovery.artist_policy_revision "
                        "WHERE policy_id = :policy_id AND revision = :revision"
                    ),
                    {"policy_id": policy.policy_id, "revision": policy.revision},
                )
            with pytest.raises(DBAPIError, match="binding is immutable"), session.begin_nested():
                session.execute(
                    text(
                        "UPDATE discovery.artist_policy SET provider_artist_id = '999' "
                        "WHERE policy_id = :policy_id"
                    ),
                    {"policy_id": policy.policy_id},
                )
            assert session.get(ArtistPolicyRevisionRow, (policy.policy_id, policy.revision))
            replay = PostgresDiscoveryAutomationRepository(session).set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, operation_id=revision.operation_id),
                request_sha256=b"r" * 32,
                now=now + timedelta(minutes=1),
            )
            assert replay.replayed
            assert replay.policy.provider_artist_id == "20"
    finally:
        engine.dispose()


def test_due_run_claim_page_is_fenced_and_review_does_not_enqueue(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C scan owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"c" * 32,
                now=now,
            ).policy
            assert repository.dispatch_due(now=now + timedelta(seconds=1), limit=20) == 1
            run = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.policy_id == policy.policy_id)
            )
            assert run is not None and run.job_id is not None
            job = session.get(JobRow, run.job_id)
            assert job is not None
            job.state = "RUNNING"
            job.lease_owner = "a1c-pg-test"
            job.lease_deadline = now + timedelta(minutes=5)
            job.attempt_count = 1
            fence = LeaseFence(job.job_id, "a1c-pg-test", 1)
            target = repository.claim_scan(
                run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
            )
            assert target is not None and target.next_offset == 0
            page = ProviderTrackPage(
                provider_artist_id="20",
                offset=0,
                observations=(ProviderTrackObservation(_track(), date(2026, 8, 1), "UTC"),),
                next_offset=None,
                checkpoint="release:2026-08-01:10",
            )
            assert (
                repository.commit_page(
                    run_id=run.run_id, owner_user_id=owner_id, fence=fence, page=page, now=now
                )
                is None
            )
            # A worker can crash after a terminal short page commits. Reclaiming that run must
            # not manufacture a second provider target or repeat provider I/O.
            job.lease_owner, job.attempt_count = "a1c-short-page-reclaim", 2
            reclaimed = repository.claim_scan(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=LeaseFence(job.job_id, "a1c-short-page-reclaim", 2),
                now=now + timedelta(minutes=1),
            )
            assert reclaimed is None
            repository.complete_scan(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=LeaseFence(job.job_id, "a1c-short-page-reclaim", 2),
                now=now,
            )
            session.flush()
            assert run.state == "COMPLETED" and run.observed_count == 1
            assert run.auto_selected_count == 0
            assert (
                session.scalar(select(JobRow).where(JobRow.job_type == "discovery.acquire")) is None
            )
            policy_row = session.get(ArtistPolicyRow, policy.policy_id)
            assert policy_row is not None and policy_row.next_eligible_at is not None
    finally:
        engine.dispose()


def test_stale_scan_lease_cannot_mutate_run_or_page(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C stale scan owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"y" * 32,
                now=now,
            ).policy
            assert repository.dispatch_due(now=now, limit=20) == 1
            run = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.policy_id == policy.policy_id)
            )
            assert run is not None and run.job_id is not None
            job = session.get(JobRow, run.job_id)
            assert job is not None
            job.state = "RUNNING"
            job.lease_owner = "current-worker"
            job.lease_deadline = now + timedelta(minutes=5)
            job.attempt_count = 1
            current_fence = LeaseFence(job.job_id, "current-worker", 1)
            assert (
                repository.claim_scan(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=current_fence,
                    now=now,
                )
                is not None
            )
            job.lease_owner = "replacement-worker"
            job.attempt_count = 2

            with pytest.raises(DiscoveryAutomationError, match="lease_fence_lost"):
                repository.require_current(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=current_fence,
                    boundary="BEFORE_PROVIDER_IO",
                )
            job.lease_owner = "current-worker"
            job.attempt_count = 1
            job.lease_deadline = now - timedelta(seconds=1)
            with pytest.raises(DiscoveryAutomationError, match="lease_fence_lost"):
                repository.require_current(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=current_fence,
                    boundary="BEFORE_PROVIDER_IO",
                )
            assert run.state == "RUNNING"
            assert run.page_count == 0
            assert run.started_at == now
    finally:
        engine.dispose()


def test_policy_change_after_provider_response_rejects_page_atomically(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C page revocation race owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"q" * 32,
                now=now,
            ).policy
            run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"r" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            job = session.get(JobRow, durable_run.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "a1c-page-race", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "a1c-page-race", 1)
            repository.claim_scan(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                now=now,
            )
            provider_response = ProviderTrackPage(
                "20",
                0,
                (ProviderTrackObservation(_track("971"), date(2026, 8, 3), "UTC"),),
                None,
                "release:revoked-after-response",
            )

            repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=policy.revision,
                    import_mode="REVIEW_REQUIRED",
                ),
                request_sha256=b"s" * 32,
                now=now + timedelta(seconds=1),
            )
            with pytest.raises(DiscoveryAutomationError, match="policy_revision_stale"):
                repository.commit_page(
                    run_id=run.run_id,
                    owner_user_id=owner_id,
                    fence=fence,
                    page=provider_response,
                    now=now + timedelta(seconds=2),
                )
            assert session.get(DiscoveryRunPageRow, (run.run_id, 0)) is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AcquisitionAttemptRow)
                    .where(AcquisitionAttemptRow.origin == "AUTOMATIC")
                )
                == 0
            )
            repository.fail_scan(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                error_code="policy_revision_stale",
                terminal=True,
                now=now + timedelta(seconds=3),
            )
            assert durable_run.state == "CANCELLED"
    finally:
        engine.dispose()


def test_real_auto_attempt_is_gated_at_claim_acquire_and_vault_boundaries(
    database_url: str,
) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C automatic boundary owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            automation = PostgresDiscoveryAutomationRepository(session)
            policy = automation.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"t" * 32,
                now=now,
            ).policy
            run = automation.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"u" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            scan_job = session.get(JobRow, durable_run.job_id)
            assert scan_job is not None
            scan_job.state, scan_job.lease_owner, scan_job.attempt_count = (
                "RUNNING",
                "a1c-auto-scan",
                1,
            )
            scan_job.lease_deadline = now + timedelta(minutes=5)
            scan_fence = LeaseFence(scan_job.job_id, "a1c-auto-scan", 1)
            automation.claim_scan(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=scan_fence,
                now=now,
            )
            automation.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=scan_fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("972"), date(2026, 8, 4), "UTC"),),
                    None,
                    "release:auto-boundary",
                ),
                now=now,
            )
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == "972",
                )
            )
            assert candidate is not None and candidate.job_id is not None
            attempt = session.get(AcquisitionAttemptRow, candidate.current_acquisition_attempt_id)
            acquire_job = session.get(JobRow, candidate.job_id)
            assert attempt is not None and acquire_job is not None
            acquire_job.state, acquire_job.lease_owner, acquire_job.attempt_count = (
                "RUNNING",
                "a1c-auto-acquire",
                1,
            )
            acquire_job.lease_deadline = now - timedelta(seconds=1)
            acquire_fence = LeaseFence(acquire_job.job_id, "a1c-auto-acquire", 1)
            acquisition = PostgresBulkDiscoveryRepository(session)

            with pytest.raises(BulkDiscoveryError, match="lease_fence_lost"):
                acquisition.claim_acquisition(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_id,
                    fence=acquire_fence,
                    automatic_enabled=True,
                )
            assert candidate.acquisition_state == "QUEUED" and attempt.state == "QUEUED"
            acquire_job.lease_deadline = now + timedelta(minutes=5)

            with pytest.raises(BulkDiscoveryError, match="automation_not_active"):
                acquisition.claim_acquisition(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_id,
                    fence=acquire_fence,
                    automatic_enabled=False,
                )
            assert candidate.acquisition_state == "QUEUED" and attempt.state == "QUEUED"

            target = acquisition.claim_acquisition(
                candidate_id=candidate.candidate_id,
                owner_user_id=owner_id,
                fence=acquire_fence,
                automatic_enabled=True,
            )
            assert target is not None
            acquire_job.lease_deadline = now - timedelta(seconds=1)
            with pytest.raises(BulkDiscoveryError, match="lease_fence_lost"):
                acquisition.require_before_acquire(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt.acquisition_attempt_id,
                    fence=acquire_fence,
                    automatic_enabled=True,
                )
            acquire_job.lease_deadline = now + timedelta(minutes=5)
            with pytest.raises(BulkDiscoveryError, match="automation_not_active"):
                acquisition.require_before_acquire(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_id,
                    acquisition_attempt_id=attempt.acquisition_attempt_id,
                    fence=acquire_fence,
                    automatic_enabled=False,
                )
            for _boundary in ("PRE_PUBLISH", "PRE_MATERIALIZE"):
                with pytest.raises(BulkDiscoveryError, match="automation_not_active"):
                    acquisition.require_ingest_boundary(
                        candidate_id=candidate.candidate_id,
                        owner_user_id=owner_id,
                        acquisition_attempt_id=attempt.acquisition_attempt_id,
                        automatic_enabled=False,
                    )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(UploadSessionRow)
                    .where(
                        UploadSessionRow.source_acquisition_attempt_id
                        == attempt.acquisition_attempt_id
                    )
                )
                == 0
            )
            assert candidate.acquisition_state == "ACQUIRING"
            assert attempt.state == "RUNNING"
    finally:
        engine.dispose()


def test_due_dispatch_dedupes_and_run_now_preserves_fixed_cadence(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C cadence owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"d" * 32,
                now=now,
            ).policy
            assert repository.dispatch_due(now=now + timedelta(seconds=1), limit=20) == 1
            assert repository.dispatch_due(now=now + timedelta(seconds=2), limit=20) == 0
            with pytest.raises(DiscoveryAutomationError, match="automation_not_active"):
                repository.run_now(
                    owner_user_id=owner_id,
                    policy_id=policy.policy_id,
                    operation_id=uuid4(),
                    request_sha256=b"e" * 32,
                    now=now + timedelta(minutes=1),
                )
            run_at = now + timedelta(days=1, seconds=2)
            run_now = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"e" * 32,
                now=run_at,
            )
            policy_row = session.get(ArtistPolicyRow, policy.policy_id)
            assert policy_row is not None
            assert policy_row.next_eligible_at == run_at + timedelta(days=1)
            assert run_now.state == "QUEUED"
            assert (
                len(
                    tuple(
                        session.scalars(
                            select(DiscoveryRunRow).where(
                                DiscoveryRunRow.policy_id == policy.policy_id
                            )
                        )
                    )
                )
                == 2
            )
    finally:
        engine.dispose()


def test_scheduler_reconciles_exhausted_job_and_allows_next_due_run(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C exhausted scan owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"x" * 32,
                now=now,
            ).policy
            assert repository.dispatch_due(now=now, limit=20) == 1
            exhausted = session.scalar(
                select(DiscoveryRunRow).where(DiscoveryRunRow.policy_id == policy.policy_id)
            )
            assert exhausted is not None and exhausted.job_id is not None
            exhausted.state = "RETRY_WAIT"
            job = session.get(JobRow, exhausted.job_id)
            assert job is not None
            job.state = "FAILED"
            job.error_code = "job.unhandled_error"
            job.completed_at = now + timedelta(minutes=1)

            assert repository.dispatch_due(now=now + timedelta(days=1, seconds=1), limit=20) == 1
            assert exhausted.state == "FAILED_TERMINAL"
            assert exhausted.error_code == "discovery_adapter_unavailable"
            assert exhausted.completed_at == now + timedelta(days=1, seconds=1)
            recovered_at = now + timedelta(days=1, seconds=2)
            job.state = "RUNNING"
            job.error_code = None
            job.completed_at = None
            job.lease_owner = "recovered-worker"
            job.lease_deadline = recovered_at + timedelta(minutes=5)
            job.attempt_count = 5
            recovered_fence = LeaseFence(job.job_id, "recovered-worker", 5)
            assert (
                repository.claim_scan(
                    run_id=exhausted.run_id,
                    owner_user_id=owner_id,
                    fence=recovered_fence,
                    now=recovered_at,
                )
                is None
            )
            repository.complete_scan(
                run_id=exhausted.run_id,
                owner_user_id=owner_id,
                fence=recovered_fence,
                now=recovered_at,
            )
            assert exhausted.state == "FAILED_TERMINAL"
            assert exhausted.error_code == "discovery_adapter_unavailable"
            assert (
                len(
                    tuple(
                        session.scalars(
                            select(DiscoveryRunRow).where(
                                DiscoveryRunRow.policy_id == policy.policy_id
                            )
                        )
                    )
                )
                == 2
            )
    finally:
        engine.dispose()


def test_page_replay_is_digest_idempotent_and_auto_is_bounded_to_ten(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C auto quota owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"f" * 32,
                now=now,
            ).policy
            run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"g" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            job = session.get(JobRow, durable_run.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "a1c-auto", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "a1c-auto", 1)
            assert (
                repository.claim_scan(
                    run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
                )
                is not None
            )
            observations = tuple(
                ProviderTrackObservation(_track(str(track_id)), date(2026, 8, 1), "UTC")
                for track_id in range(31, 20, -1)
            )
            page = ProviderTrackPage("20", 0, observations, None, "release:quota")
            repository.commit_page(
                run_id=run.run_id, owner_user_id=owner_id, fence=fence, page=page, now=now
            )
            repository.commit_page(
                run_id=run.run_id, owner_user_id=owner_id, fence=fence, page=page, now=now
            )
            assert durable_run.observed_count == 11 and durable_run.auto_selected_count == 10
            attempts = tuple(
                session.scalars(
                    select(AcquisitionAttemptRow).where(AcquisitionAttemptRow.origin == "AUTOMATIC")
                )
            )
            assert len(attempts) == 10
            assert (
                len(
                    tuple(
                        session.scalars(
                            select(JobRow).where(JobRow.job_type == "discovery.acquire")
                        )
                    )
                )
                == 10
            )
            downgraded = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=policy.revision,
                    import_mode="REVIEW_REQUIRED",
                ),
                request_sha256=b"h" * 32,
                now=now + timedelta(minutes=1),
            )
            assert downgraded.policy.revision == 2
            assert {
                attempt.state
                for attempt in session.scalars(
                    select(AcquisitionAttemptRow).where(AcquisitionAttemptRow.origin == "AUTOMATIC")
                )
            } == {"CANCELLED"}
    finally:
        engine.dispose()


def test_same_value_revision_revokes_old_auto_lineage_and_new_revision_can_run(
    database_url: str,
) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C same-value revision owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"j" * 32,
                now=now,
            ).policy
            first_run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"k" * 32,
                now=now,
            )
            first_durable = session.get(DiscoveryRunRow, first_run.run_id)
            assert first_durable is not None and first_durable.job_id is not None
            first_job = session.get(JobRow, first_durable.job_id)
            assert first_job is not None
            first_job.state, first_job.lease_owner, first_job.attempt_count = (
                "RUNNING",
                "a1c-revision-1",
                1,
            )
            first_job.lease_deadline = now + timedelta(minutes=5)
            first_fence = LeaseFence(first_job.job_id, "a1c-revision-1", 1)
            repository.claim_scan(
                run_id=first_run.run_id,
                owner_user_id=owner_id,
                fence=first_fence,
                now=now,
            )
            repository.commit_page(
                run_id=first_run.run_id,
                owner_user_id=owner_id,
                fence=first_fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("951"), date(2026, 8, 1), "UTC"),),
                    None,
                    "release:revision-1",
                ),
                now=now,
            )
            first_attempt = session.scalar(
                select(AcquisitionAttemptRow).where(
                    AcquisitionAttemptRow.policy_revision == policy.revision,
                    AcquisitionAttemptRow.origin == "AUTOMATIC",
                )
            )
            first_auth = session.scalar(
                select(SourceAuthorizationRow).where(
                    SourceAuthorizationRow.policy_revision == policy.revision,
                    SourceAuthorizationRow.purpose == "AUTO_IMPORT",
                )
            )
            assert first_attempt is not None and first_auth is not None

            revised = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=policy.revision,
                    import_mode="AUTO_IMPORT",
                ),
                request_sha256=b"l" * 32,
                now=now + timedelta(minutes=1),
            ).policy
            assert revised.revision == policy.revision + 1
            assert first_durable.state == "CANCELLED"
            assert first_attempt.state == "CANCELLED"
            assert first_attempt.row_version == 2
            assert first_durable.row_version >= 4
            assert first_auth.revoked_at == now + timedelta(minutes=1)

            second_now = now + timedelta(days=1)
            second_run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=revised.policy_id,
                operation_id=uuid4(),
                request_sha256=b"m" * 32,
                now=second_now,
            )
            second_durable = session.get(DiscoveryRunRow, second_run.run_id)
            assert second_durable is not None and second_durable.job_id is not None
            second_job = session.get(JobRow, second_durable.job_id)
            assert second_job is not None
            second_job.state, second_job.lease_owner, second_job.attempt_count = (
                "RUNNING",
                "a1c-revision-2",
                1,
            )
            second_job.lease_deadline = second_now + timedelta(minutes=5)
            second_fence = LeaseFence(second_job.job_id, "a1c-revision-2", 1)
            repository.claim_scan(
                run_id=second_run.run_id,
                owner_user_id=owner_id,
                fence=second_fence,
                now=second_now,
            )
            repository.commit_page(
                run_id=second_run.run_id,
                owner_user_id=owner_id,
                fence=second_fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("952"), date(2026, 8, 2), "UTC"),),
                    None,
                    "release:revision-2",
                ),
                now=second_now,
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SourceAuthorizationRow)
                    .where(
                        SourceAuthorizationRow.policy_revision == revised.revision,
                        SourceAuthorizationRow.purpose == "AUTO_IMPORT",
                        SourceAuthorizationRow.revoked_at.is_(None),
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


def test_ignored_candidate_is_not_auto_reselected_when_observed_again(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C ignored rescan owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"z" * 32,
                now=now,
            ).policy
            binding_candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == "10",
                )
            )
            assert binding_candidate is not None
            repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=binding_candidate.candidate_id,
                action="IGNORE",
                operation_id=uuid4(),
                request_sha256=b"y" * 32,
                now=now,
            )
            run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"x" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            job = session.get(JobRow, durable_run.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "a1c-ignored", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "a1c-ignored", 1)
            assert (
                repository.claim_scan(
                    run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
                )
                is not None
            )
            repository.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("10"), date(2026, 8, 3), "UTC"),),
                    None,
                    "release:ignored-rescan",
                ),
                now=now,
            )
            assert binding_candidate.disposition == "IGNORED"
            assert binding_candidate.acquisition_state == "NOT_REQUESTED"
            assert durable_run.auto_selected_count == 0
            assert (
                session.scalar(select(JobRow).where(JobRow.job_type == "discovery.acquire")) is None
            )
    finally:
        engine.dispose()


def test_concurrent_distinct_policy_pages_share_one_owner_rolling_auto_quota(
    database_url: str,
) -> None:
    """The owner advisory lock serializes two eligible pages racing for the last ten slots."""

    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        pending: list[tuple[UUID, LeaseFence, ProviderTrackPage]] = []
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C concurrent quota owner")
            first_artist = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert first_artist is not None
            artists = [first_artist] + [
                _seed_owned_artist(session, owner_id, f"A1C quota artist {index}")
                for index in range(1, 6)
            ]
            repository = PostgresDiscoveryAutomationRepository(session)
            for index, artist_id in enumerate(artists):
                base_track_id = 100 + index * 20
                _bind_provider_artist(session, owner_id, artist_id, track_id=str(base_track_id))
                policy = repository.set_policy(
                    owner_user_id=owner_id,
                    command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                    request_sha256=bytes([index + 1]) * 32,
                    now=now,
                ).policy
                run = repository.run_now(
                    owner_user_id=owner_id,
                    policy_id=policy.policy_id,
                    operation_id=uuid4(),
                    request_sha256=bytes([index + 11]) * 32,
                    now=now,
                )
                durable_run = session.get(DiscoveryRunRow, run.run_id)
                assert durable_run is not None and durable_run.job_id is not None
                job = session.get(JobRow, durable_run.job_id)
                assert job is not None
                worker_id = f"a1c-quota-{index}"
                job.state, job.lease_owner, job.attempt_count = "RUNNING", worker_id, 1
                job.lease_deadline = now + timedelta(minutes=5)
                fence = LeaseFence(job.job_id, worker_id, 1)
                assert (
                    repository.claim_scan(
                        run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
                    )
                    is not None
                )
                observations = tuple(
                    ProviderTrackObservation(
                        _track(str(base_track_id + track_offset)), date(2026, 8, 4), "UTC"
                    )
                    for track_offset in range(9, -1, -1)
                )
                pending.append(
                    (
                        run.run_id,
                        fence,
                        ProviderTrackPage("20", 0, observations, None, f"release:quota:{index}"),
                    )
                )
            for run_id, fence, page in pending[:4]:
                repository.commit_page(
                    run_id=run_id, owner_user_id=owner_id, fence=fence, page=page, now=now
                )

        def commit_last_page(item: tuple[UUID, LeaseFence, ProviderTrackPage]) -> int:
            run_id, fence, page = item
            with sessions.begin() as session:
                repository = PostgresDiscoveryAutomationRepository(session)
                repository.commit_page(
                    run_id=run_id, owner_user_id=owner_id, fence=fence, page=page, now=now
                )
                run = session.get(DiscoveryRunRow, run_id)
                assert run is not None
                return run.auto_selected_count

        with ThreadPoolExecutor(max_workers=2) as executor:
            selected = tuple(executor.map(commit_last_page, pending[4:]))

        with sessions() as session:
            automatic_total = session.scalar(
                select(func.count())
                .select_from(AcquisitionAttemptRow)
                .where(AcquisitionAttemptRow.origin == "AUTOMATIC")
            )
        assert sorted(selected) == [0, 10]
        assert automatic_total == 50
    finally:
        engine.dispose()


def test_owner_isolation_and_stale_policy_cas_are_fail_closed(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C isolated owner")
            other_id, _ = _seed_import(session, "A1C other owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"i" * 32,
                now=now,
            ).policy
            assert repository.list_policies(owner_user_id=other_id, limit=10) == ()
            with pytest.raises(DiscoveryAutomationError, match="policy_revision_stale"):
                repository.set_policy(
                    owner_user_id=owner_id,
                    command=_command(artist_id, uuid4(), expected_revision=99),
                    request_sha256=b"j" * 32,
                    now=now,
                )
            with pytest.raises(DiscoveryAutomationError, match="discovery_policy_not_found"):
                repository.run_now(
                    owner_user_id=other_id,
                    policy_id=policy.policy_id,
                    operation_id=uuid4(),
                    request_sha256=b"k" * 32,
                    now=now,
                )
    finally:
        engine.dispose()


def test_candidate_actions_are_replayable_and_preserve_manual_after_auto_lineage(
    database_url: str,
) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C action owner")
            other_id, _ = _seed_import(session, "A1C action other")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            repository = PostgresDiscoveryAutomationRepository(session)
            policy = repository.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4(), import_mode="AUTO_IMPORT"),
                request_sha256=b"l" * 32,
                now=now,
            ).policy
            run = repository.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"m" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            job = session.get(JobRow, durable_run.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "a1c-actions", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "a1c-actions", 1)
            repository.claim_scan(run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now)
            repository.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("44"), date(2026, 8, 2), "UTC"),),
                    None,
                    "release:action",
                ),
                now=now,
            )
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == "44",
                )
            )
            assert candidate is not None
            auto_attempt_id = candidate.current_acquisition_attempt_id
            assert auto_attempt_id is not None
            repository.set_policy(
                owner_user_id=owner_id,
                command=_command(
                    artist_id,
                    uuid4(),
                    expected_revision=policy.revision,
                    import_mode="REVIEW_REQUIRED",
                ),
                request_sha256=b"n" * 32,
                now=now + timedelta(minutes=1),
            )
            assert candidate.acquisition_state == "CANCELLED"

            select_operation = uuid4()
            selected = repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=candidate.candidate_id,
                action="SELECT",
                operation_id=select_operation,
                request_sha256=b"o" * 32,
                now=now + timedelta(minutes=2),
            )
            manual_attempt_id = candidate.current_acquisition_attempt_id
            assert selected.acquisition_state == "QUEUED" and not selected.replayed
            assert manual_attempt_id is not None and manual_attempt_id != auto_attempt_id
            replay = repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=candidate.candidate_id,
                action="SELECT",
                operation_id=select_operation,
                request_sha256=b"o" * 32,
                now=now + timedelta(minutes=2),
            )
            assert replay.replayed
            with pytest.raises(DiscoveryAutomationError, match="operation_conflict"):
                repository.act_on_candidate(
                    owner_user_id=owner_id,
                    candidate_id=candidate.candidate_id,
                    action="IGNORE",
                    operation_id=select_operation,
                    request_sha256=b"p" * 32,
                    now=now + timedelta(minutes=2),
                )
            with pytest.raises(DiscoveryAutomationError, match="discovery_candidate_not_found"):
                repository.act_on_candidate(
                    owner_user_id=other_id,
                    candidate_id=candidate.candidate_id,
                    action="SELECT",
                    operation_id=uuid4(),
                    request_sha256=b"q" * 32,
                    now=now + timedelta(minutes=2),
                )

            manual_attempt = session.get(AcquisitionAttemptRow, manual_attempt_id)
            assert manual_attempt is not None and manual_attempt.job_id is not None
            manual_attempt.state = "FAILED"
            manual_attempt.completed_at = now + timedelta(minutes=3)
            manual_attempt.updated_at = now + timedelta(minutes=3)
            manual_job = session.get(JobRow, manual_attempt.job_id)
            assert manual_job is not None
            manual_job.state = "FAILED"
            candidate.acquisition_state = "FAILED_TERMINAL"
            candidate.error_code = "provider_unavailable"
            # The action receipt must replay the result captured at SELECT time, even after the
            # candidate has later crossed a terminal attempt state.
            selected_replay_after_mutation = repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=candidate.candidate_id,
                action="SELECT",
                operation_id=select_operation,
                request_sha256=b"o" * 32,
                now=now + timedelta(minutes=4),
            )
            assert (
                selected_replay_after_mutation.replayed
                and selected_replay_after_mutation.disposition == selected.disposition
                and selected_replay_after_mutation.acquisition_state == selected.acquisition_state
            )
            retried = repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=candidate.candidate_id,
                action="RETRY",
                operation_id=uuid4(),
                request_sha256=b"r" * 32,
                now=now + timedelta(minutes=4),
            )
            assert retried.acquisition_state == "QUEUED"
            attempts = tuple(
                session.scalars(
                    select(AcquisitionAttemptRow).where(
                        AcquisitionAttemptRow.candidate_id == candidate.candidate_id
                    )
                )
            )
            assert [attempt.origin for attempt in attempts].count("AUTOMATIC") == 1
            assert [attempt.origin for attempt in attempts].count("MANUAL") == 2

            # A delayed worker from the cancelled automatic lineage cannot quarantine or fail
            # the new manual attempt. Its stale fence must leave both projections untouched.
            automatic_attempt = session.get(AcquisitionAttemptRow, auto_attempt_id)
            assert automatic_attempt is not None and automatic_attempt.job_id is not None
            current_attempt_id = candidate.current_acquisition_attempt_id
            assert current_attempt_id is not None
            current_state = candidate.acquisition_state
            # Simulate a delayed provider upload reaching the Vault quarantine/failure helper.
            # The stale automatic attempt must not mutate the replacement manual projection.
            PostgresVaultRuntime(session)._fail_discovery_candidate(
                cast(
                    UploadSessionRow,
                    SimpleNamespace(
                        source_candidate_id=candidate.candidate_id,
                        source_acquisition_attempt_id=auto_attempt_id,
                        user_id=owner_id,
                    ),
                ),
                "vault.delayed_auto_upload",
            )
            assert candidate.current_acquisition_attempt_id == current_attempt_id
            assert candidate.acquisition_state == current_state == "QUEUED"
            assert automatic_attempt.state == "CANCELLED"
            with pytest.raises(BulkDiscoveryError, match="lease_fence_lost"):
                PostgresBulkDiscoveryRepository(session).fail_acquisition(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_id,
                    fence=LeaseFence(automatic_attempt.job_id, "delayed-auto-worker", 1),
                    error_code="source_authorization_unavailable",
                    terminal=True,
                )
            assert candidate.current_acquisition_attempt_id == current_attempt_id
            assert candidate.acquisition_state == current_state == "QUEUED"
            assert automatic_attempt.state == "CANCELLED"

            binding_candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == "10",
                )
            )
            assert binding_candidate is not None
            ignored = repository.act_on_candidate(
                owner_user_id=owner_id,
                candidate_id=binding_candidate.candidate_id,
                action="IGNORE",
                operation_id=uuid4(),
                request_sha256=b"s" * 32,
                now=now + timedelta(minutes=5),
            )
            assert ignored.disposition == "IGNORED" and ignored.acquisition_state == "NOT_REQUESTED"
            receipts = tuple(session.scalars(select(CandidateActionReceiptRow)))
            assert len(receipts) == 3
            expired_at = now - timedelta(days=31)
            for receipt in receipts:
                receipt.created_at = expired_at
            durable_run.state = "COMPLETED"
            durable_run.completed_at = expired_at
            durable_run.updated_at = expired_at
            assert repository.cleanup_expired(now=now, limit=10) == 4
            assert session.get(DiscoveryRunRow, durable_run.run_id) is None
            candidate.updated_at = expired_at
            assert PostgresBulkDiscoveryRepository(session).cleanup_expired(now=now, limit=10) == 0
            assert session.get(DiscoveryCandidateRow, candidate.candidate_id) is candidate
            assert len(tuple(session.scalars(select(AcquisitionAttemptRow)))) == 3
    finally:
        engine.dispose()


def test_combined_cleanup_orders_a1c_links_before_unattempted_candidate(
    database_url: str,
) -> None:
    engine, sessions = _sessions(database_url)
    now = datetime.now(UTC)
    expired_at = now - timedelta(days=31)
    try:
        with sessions.begin() as session:
            owner_id, _ = _seed_import(session, "A1C cleanup dependency owner")
            artist_id = session.scalar(select(ArtistRow.artist_id).limit(1))
            assert artist_id is not None
            _bind_provider_artist(session, owner_id, artist_id)
            automation = PostgresDiscoveryAutomationRepository(session)
            policy = automation.set_policy(
                owner_user_id=owner_id,
                command=_command(artist_id, uuid4()),
                request_sha256=b"c" * 32,
                now=now,
            ).policy
            run = automation.run_now(
                owner_user_id=owner_id,
                policy_id=policy.policy_id,
                operation_id=uuid4(),
                request_sha256=b"d" * 32,
                now=now,
            )
            durable_run = session.get(DiscoveryRunRow, run.run_id)
            assert durable_run is not None and durable_run.job_id is not None
            job = session.get(JobRow, durable_run.job_id)
            assert job is not None
            job.state, job.lease_owner, job.attempt_count = "RUNNING", "cleanup-worker", 1
            job.lease_deadline = now + timedelta(minutes=5)
            fence = LeaseFence(job.job_id, "cleanup-worker", 1)
            automation.claim_scan(run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now)
            automation.commit_page(
                run_id=run.run_id,
                owner_user_id=owner_id,
                fence=fence,
                page=ProviderTrackPage(
                    "20",
                    0,
                    (ProviderTrackObservation(_track("981"), date(2026, 8, 5), "UTC"),),
                    None,
                    "release:cleanup",
                ),
                now=now,
            )
            automation.complete_scan(
                run_id=run.run_id, owner_user_id=owner_id, fence=fence, now=now
            )
            candidate = session.scalar(
                select(DiscoveryCandidateRow).where(
                    DiscoveryCandidateRow.user_id == owner_id,
                    DiscoveryCandidateRow.provider_track_id == "981",
                )
            )
            assert candidate is not None and candidate.acquisition_state == "NOT_REQUESTED"
            candidate.updated_at = expired_at
            durable_run.updated_at = expired_at
            durable_run.completed_at = expired_at

            bulk = PostgresBulkDiscoveryRepository(session)
            assert bulk.cleanup_expired(now=now, limit=10) == 0
            assert automation.cleanup_expired(now=now, limit=10) == 1
            assert bulk.cleanup_expired(now=now, limit=10) == 1
            assert session.get(DiscoveryCandidateRow, candidate.candidate_id) is None
    finally:
        engine.dispose()
