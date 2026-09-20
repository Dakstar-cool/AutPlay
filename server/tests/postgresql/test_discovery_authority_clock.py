"""A1 lock waits cannot extend worker leases or source download authorization."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.discovery_automation_runtime import (
    PostgresDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.discovery_runtime import (
    DISCOVERY_ACQUIRE_JOB,
    JAMENDO_PROVIDER_ID,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    ArtistPolicyRow,
    ArtistRow,
    DiscoveryCandidateRow,
    DiscoveryRunRow,
    ExternalReferenceRow,
    JobRow,
    RecordingRow,
    SourceAuthorizationRow,
    SyncEventRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.domain.discovery import ProviderTrackObservation, ProviderTrackPage
from autplay.domain.jobs import LeaseFence
from autplay.domain.resource_admission import AcquisitionClaim
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits, VerifiedStagedFile
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .test_discovery_automation_runtime import _bind_provider_artist, _command
from .test_discovery_runtime import _seed_import, _track
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_worker_resource_wait import a1

__all__ = ["admission"]


def _blocked_by(observer: Session, pid: int) -> bool:
    # pg_stat_activity caches its backend list for a transaction. A new waiter
    # must be visible even if it connected after the first observation.
    observer.execute(select(func.pg_stat_clear_snapshot()))
    return bool(
        observer.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE :pid = ANY(pg_blocking_pids(pid)))"
            ),
            {"pid": pid},
        )
    )


def _wait_until_expired(
    harness: AdmissionHarness, blocker: Session, pending: Future[object], deadline: datetime
) -> None:
    pid = present(blocker.scalar(select(func.pg_backend_pid())))
    until = monotonic() + 5
    with harness.sessions() as observer:
        while not _blocked_by(observer, pid):
            assert not pending.done(), "The actual A1 boundary must wait for the held lock"
            assert monotonic() < until
            sleep(0.005)
        remaining = (
            deadline - present(observer.scalar(select(func.clock_timestamp())))
        ).total_seconds()
    sleep(max(0, remaining) + 0.05)


def test_lock_observer_refreshes_a_backend_connected_after_its_snapshot(
    admission: AdmissionHarness,
) -> None:
    claim = a1(admission)
    connected: Queue[int] = Queue(maxsize=1)
    fresh_engine = create_engine(admission.engine.url, poolclass=NullPool)

    def wait_for_lock() -> None:
        with fresh_engine.begin() as connection:
            connected.put(present(connection.scalar(select(func.pg_backend_pid()))))
            connection.execute(
                select(JobRow.job_id).where(JobRow.job_id == claim.fence.job_id).with_for_update()
            )

    try:
        with (
            admission.sessions() as blocker,
            admission.sessions() as observer,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            blocker.execute(
                select(JobRow).where(JobRow.job_id == claim.fence.job_id).with_for_update()
            )
            blocker_pid = present(blocker.scalar(select(func.pg_backend_pid())))
            cached = set(observer.scalars(text("SELECT pid FROM pg_stat_activity")))
            pending = pool.submit(wait_for_lock)
            try:
                new_pid = connected.get(timeout=5)
                assert new_pid not in cached
                assert new_pid not in set(
                    observer.scalars(text("SELECT pid FROM pg_stat_activity"))
                )
                until = monotonic() + 5
                while not _blocked_by(observer, blocker_pid):
                    assert not pending.done() and monotonic() < until
                    sleep(0.005)
            finally:
                blocker.rollback()
            pending.result(timeout=5)
    finally:
        fresh_engine.dispose()


def _snapshot(harness: AdmissionHarness, candidate_id: UUID) -> tuple[object, ...]:
    with harness.sessions() as session:
        candidate = present(session.get(DiscoveryCandidateRow, candidate_id))
        return (
            candidate.acquisition_state,
            candidate.recording_id,
            candidate.staging_key,
            *(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    RecordingRow,
                    UserTrackRefRow,
                    ExternalReferenceRow,
                    UploadSessionRow,
                    SyncEventRow,
                    JobRow,
                )
            ),
        )


@pytest.mark.parametrize(
    "phase,blocked,expiry",
    [
        (phase, blocked, "job")
        for phase in ("claim", "before", "prepare")
        for blocked in ("job", "candidate")
    ]
    + [(phase, "job", "cancel") for phase in ("claim", "before", "prepare")]
    + [("prepare", "identity", expiry) for expiry in ("job", "source")]
    + [("before", "source", "job")],
)
def test_a1_waiting_authority_rejects_expiry_and_cancel_atomically(
    admission: AdmissionHarness, phase: str, blocked: str, expiry: str
) -> None:
    claim = a1(admission)
    reference_id = uuid4()
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate_id, source_id = attempt.candidate_id, attempt.source_authorization_id
        candidate = present(session.get(DiscoveryCandidateRow, candidate_id))
        owner = candidate.user_id
        if blocked == "identity":
            session.add(
                ExternalReferenceRow(
                    external_reference_id=reference_id,
                    provider_id=JAMENDO_PROVIDER_ID,
                    external_entity_type="ARTIST",
                    external_id="20",
                    market_scope="GLOBAL",
                    artist_id=candidate.canonical_artist_id,
                )
            )
    if phase != "claim":
        with admission.sessions.begin() as session:
            PostgresBulkDiscoveryRepository(session).claim_acquisition(
                candidate_id=candidate_id, owner_user_id=owner, fence=claim.fence
            )
    initial = _snapshot(admission, candidate_id)
    with admission.sessions.begin() as session:
        deadline = present(session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=1)
        if expiry == "job":
            present(session.get(JobRow, claim.fence.job_id)).lease_deadline = deadline
        elif expiry == "source":
            present(session.get(SourceAuthorizationRow, source_id)).expires_at = deadline

    def invoke() -> object:
        with admission.sessions.begin() as session:
            # Keep an identity-map entry alive across the external cancellation.
            cached = present(session.get(JobRow, claim.fence.job_id))
            assert cached.cancel_requested_at is None
            repository = PostgresBulkDiscoveryRepository(session)
            if phase == "claim":
                return repository.claim_acquisition(
                    candidate_id=candidate_id, owner_user_id=owner, fence=claim.fence
                )
            if phase == "before":
                repository.require_before_acquire(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    acquisition_attempt_id=claim.acquisition_id,
                    fence=claim.fence,
                    automatic_enabled=False,
                )
                return None
            return repository.prepare_ingest(
                candidate_id=candidate_id,
                owner_user_id=owner,
                fence=claim.fence,
                evidence=_track(),
                staging_key=OpaqueStorageKey(f"disc-{claim.acquisition_id.hex}"),
                verified=VerifiedStagedFile(1024, Sha256Digest(b"x" * 32)),
                limits=VaultLimits(),
            )

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        if blocked == "identity":
            blocker.execute(
                select(ExternalReferenceRow)
                .where(ExternalReferenceRow.external_reference_id == reference_id)
                .with_for_update()
            )
        elif blocked == "candidate":
            blocker.execute(
                select(DiscoveryCandidateRow)
                .where(DiscoveryCandidateRow.candidate_id == candidate_id)
                .with_for_update()
            )
        elif blocked == "source":
            blocker.execute(
                select(SourceAuthorizationRow)
                .where(SourceAuthorizationRow.authorization_id == source_id)
                .with_for_update()
            )
        else:
            blocker.execute(
                select(JobRow).where(JobRow.job_id == claim.fence.job_id).with_for_update()
            )
        pending = pool.submit(invoke)
        try:
            _wait_until_expired(admission, blocker, pending, deadline)
            if expiry == "cancel":
                present(blocker.get(JobRow, claim.fence.job_id)).cancel_requested_at = present(
                    blocker.scalar(select(func.clock_timestamp()))
                )
                blocker.commit()
        finally:
            blocker.rollback()
        code = "source_authorization_unavailable" if expiry == "source" else "lease_fence_lost"
        with pytest.raises(BulkDiscoveryError, match=code):
            pending.result(timeout=5)
    assert _snapshot(admission, candidate_id) == initial


def _automatic(harness: AdmissionHarness) -> AcquisitionClaim:
    with harness.sessions.begin() as session:
        now = present(session.scalar(select(func.clock_timestamp())))
        owner, _ = _seed_import(session, "A1 policy expiry")
        artist = present(session.scalar(select(ArtistRow.artist_id)))
        _bind_provider_artist(session, owner, artist)
        repository = PostgresDiscoveryAutomationRepository(session)
        policy = repository.set_policy(
            owner_user_id=owner,
            command=_command(artist, uuid4(), import_mode="AUTO_IMPORT"),
            request_sha256=b"a" * 32,
            now=now,
        ).policy
        run = repository.run_now(
            owner_user_id=owner,
            policy_id=policy.policy_id,
            operation_id=uuid4(),
            request_sha256=b"b" * 32,
            now=now,
        )
        scan = present(
            session.get(JobRow, present(session.get(DiscoveryRunRow, run.run_id)).job_id)
        )
        scan.state, scan.lease_owner, scan.attempt_count = "RUNNING", "policy-clock", 1
        scan.lease_deadline = now + timedelta(minutes=2)
        fence = LeaseFence(scan.job_id, "policy-clock", 1)
        repository.claim_scan(run_id=run.run_id, owner_user_id=owner, fence=fence, now=now)
        repository.commit_page(
            run_id=run.run_id,
            owner_user_id=owner,
            fence=fence,
            page=ProviderTrackPage(
                "20",
                0,
                (ProviderTrackObservation(_track("972"), date(2026, 8, 4), "UTC"),),
                None,
                "release:authority-clock",
            ),
            now=now,
        )
        attempt = present(
            session.scalar(
                select(AcquisitionAttemptRow).where(AcquisitionAttemptRow.origin == "AUTOMATIC")
            )
        )
        identity = attempt.acquisition_attempt_id
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="a1-policy-clock",
            supported=(DISCOVERY_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    return AcquisitionClaim(lease.fence, "DISCOVERY_ACQUISITION", identity)


@pytest.mark.parametrize("phase", ["claim", "ingest", "persistent"])
def test_source_expiry_behind_automatic_policy_lock_is_checked_after_wait(
    admission: AdmissionHarness, phase: str
) -> None:
    claim = _automatic(admission)
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate_id, policy_id = attempt.candidate_id, attempt.policy_id
        owner = present(session.get(DiscoveryCandidateRow, candidate_id)).user_id
        deadline = present(session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=1)
        present(
            session.get(SourceAuthorizationRow, attempt.source_authorization_id)
        ).expires_at = deadline
    initial = _snapshot(admission, candidate_id)

    def invoke() -> object:
        with admission.sessions.begin() as session:
            repository = PostgresBulkDiscoveryRepository(session)
            if phase == "claim":
                return repository.claim_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    fence=claim.fence,
                    automatic_enabled=True,
                )
            if phase == "persistent":

                def clock() -> datetime:
                    now = session.scalar(select(func.clock_timestamp()))
                    assert isinstance(now, datetime)
                    return now

                repository.require_persistent_acquisition_authority(
                    candidate_id=candidate_id,
                    owner_user_id=owner,
                    acquisition_attempt_id=claim.acquisition_id,
                    authority_now=clock,
                )
                return None
            repository.require_ingest_boundary(
                candidate_id=candidate_id,
                owner_user_id=owner,
                acquisition_attempt_id=claim.acquisition_id,
                automatic_enabled=True,
            )
            return None

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute(
            select(ArtistPolicyRow).where(ArtistPolicyRow.policy_id == policy_id).with_for_update()
        )
        pending = pool.submit(invoke)
        try:
            _wait_until_expired(admission, blocker, pending, deadline)
        finally:
            blocker.rollback()
        with pytest.raises(BulkDiscoveryError, match="source_authorization_unavailable"):
            pending.result(timeout=5)
    assert _snapshot(admission, candidate_id) == initial


@pytest.mark.parametrize("authority", ["source", "policy", "attempt", "candidate"])
def test_cached_authority_cannot_survive_committed_revocation(
    admission: AdmissionHarness, authority: str
) -> None:
    claim = _automatic(admission)
    with admission.sessions() as reader:
        attempt = present(reader.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate = present(reader.get(DiscoveryCandidateRow, attempt.candidate_id))
        source = present(reader.get(SourceAuthorizationRow, attempt.source_authorization_id))
        policy = present(reader.get(ArtistPolicyRow, attempt.policy_id))
        with admission.sessions.begin() as writer:
            lock_resource_admission(writer)
            if authority == "source":
                present(
                    writer.get(SourceAuthorizationRow, source.authorization_id)
                ).revoked_at = present(writer.scalar(select(func.clock_timestamp())))
            elif authority == "policy":
                present(
                    writer.get(ArtistPolicyRow, policy.policy_id)
                ).import_mode = "REVIEW_REQUIRED"
            elif authority == "attempt":
                present(
                    writer.get(AcquisitionAttemptRow, attempt.acquisition_attempt_id)
                ).state = "CANCELLED"
            else:
                present(
                    writer.get(DiscoveryCandidateRow, candidate.candidate_id)
                ).current_acquisition_attempt_id = None
        assert source.revoked_at is None and policy.import_mode == "AUTO_IMPORT"
        assert attempt.state == "QUEUED" and candidate.current_acquisition_attempt_id is not None
        code = (
            "policy_revision_stale" if authority == "policy" else "source_authorization_unavailable"
        )
        with pytest.raises(BulkDiscoveryError, match=code):
            PostgresBulkDiscoveryRepository(reader).require_ingest_boundary(
                candidate_id=candidate.candidate_id,
                owner_user_id=candidate.user_id,
                acquisition_attempt_id=claim.acquisition_id,
                automatic_enabled=True,
            )
