"""Durable quota waits free the CPU worker without spending job retries or queue age."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.discovery_runtime import PostgresBulkDiscoveryRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    JobAttemptRow,
    JobRow,
    SourceAuthorizationRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.internet_music import INTERNET_ACQUIRE_JOB
from autplay.application.job_worker import (
    JobExecutionContext,
    JobHandlerRegistry,
    JobResourceWait,
    JobWorker,
    JobWorkerSettings,
    WorkerOutcome,
)
from autplay.domain.jobs import (
    JobError,
    JobKey,
    JobLease,
    JobState,
    LeaseTransition,
    ResourceWaitTransition,
    RetryableJobError,
    RetryPolicy,
)
from autplay.domain.resource_admission import AcquisitionClaim, AdmissionState
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.ports.jobs import EnqueueJob
from sqlalchemy import select

from .test_discovery_runtime import _seed_import, _track
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import internet

__all__ = ["admission"]


def context(harness: AdmissionHarness, claim: AcquisitionClaim) -> JobExecutionContext:
    return JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(harness.sessions),
        fence=claim.fence,
        lease_interval=timedelta(minutes=1),
    )


def due(harness: AdmissionHarness, job_id: UUID) -> None:
    with harness.sessions.begin() as session:
        present(session.get(JobRow, job_id)).scheduled_at = datetime.now(UTC) - timedelta(seconds=1)


def waiting(harness: AdmissionHarness) -> AcquisitionClaim:
    _, claim = internet(harness, worker="resource-wait")
    assert harness.service.acquire_worker(claim).operation.state == AdmissionState.WAITING
    return claim


def a1(harness: AdmissionHarness) -> AcquisitionClaim:
    with harness.sessions.begin() as session:
        owner, _ = _seed_import(session, "Durable A1 wait")
    with harness.sessions.begin() as session:
        PostgresBulkDiscoveryRepository(session).start_search_acquisition(
            owner_user_id=owner, operation_id=uuid4(), evidence=_track()
        )
        target = present(session.scalar(select(AcquisitionAttemptRow.acquisition_attempt_id)))
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="a1-wait",
            supported=(JobKey("discovery.acquire", 1),),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
        present(session.get(JobRow, lease.fence.job_id)).priority = 4
    return AcquisitionClaim(lease.fence, "DISCOVERY_ACQUISITION", target)


def test_repeated_waits_preserve_retries_and_yield_to_ordinary_cpu_work(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admitted = admission.service.acquire_worker(holder)
    claim = waiting(admission)
    task_context = context(admission, claim)
    task_context.checkpoint({"stage": "RESOURCE_PENDING"})
    with pytest.raises(JobResourceWait):
        task_context.defer_for_resource(claim.request.operation_id)
    with admission.sessions() as session:
        original_age = present(
            session.get(ResourceAdmissionRow, claim.request.operation_id)
        ).enqueued_at

    ordinals: list[int] = []

    def acquisition_handler(context: JobExecutionContext, lease: JobLease) -> None:
        current = replace(claim, fence=lease.fence)
        status = admission.service.acquire_worker(current)
        ordinals.append(lease.retry_attempt_no)
        if status.operation.state == AdmissionState.WAITING:
            context.defer_for_resource(current.request.operation_id)
        raise RetryableJobError("test.provider_busy")

    regular_key = JobKey("test.regular", 1)
    handled: list[UUID] = []

    def regular_handler(context: JobExecutionContext, lease: JobLease) -> None:
        context.checkpoint({"stage": "NORMAL_WORK"})
        handled.append(lease.fence.job_id)

    worker = JobWorker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
        worker_id="resource-recheck",
        registry=JobHandlerRegistry(
            {INTERNET_ACQUIRE_JOB: acquisition_handler, regular_key: regular_handler}
        ),
        settings=JobWorkerSettings(retry_policy=RetryPolicy(max_attempts=2)),
    )
    with admission.sessions.begin() as session:
        ordinary = PostgresJobRepository(session).enqueue(
            EnqueueJob(regular_key, None, {}, priority=4)
        )
    due(admission, claim.fence.job_id)
    assert worker.run_once().outcome is WorkerOutcome.COMPLETED
    assert handled == [ordinary.job_id]
    for _ in range(6):
        due(admission, claim.fence.job_id)
        assert worker.run_once().outcome is WorkerOutcome.RESOURCE_WAIT
    assert ordinals == [1] * 6
    with admission.sessions() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        operation = present(session.get(ResourceAdmissionRow, claim.request.operation_id))
        assert job.attempt_count == job.resource_wait_count == 7
        assert job.checkpoint == {"stage": "RESOURCE_PENDING"}
        assert job.resource_waiting and job.state == "RETRY_WAIT"
        assert job.lease_owner is None and job.lease_deadline is None
        assert operation.enqueued_at == original_age and operation.waiting_until is None
        outcomes = list(
            session.scalars(select(JobAttemptRow.outcome).where(JobAttemptRow.job_id == job.job_id))
        )
        assert outcomes == ["RESOURCE_WAIT"] * 7
    admission.service.release(holder, fence(admitted))
    due(admission, claim.fence.job_id)
    assert worker.run_once().outcome is WorkerOutcome.RETRY_SCHEDULED
    due(admission, claim.fence.job_id)
    assert worker.run_once().outcome is WorkerOutcome.FAILED
    assert ordinals[-2:] == [1, 2]


def test_concurrent_grant_is_not_discarded_by_defer(admission: AdmissionHarness) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admitted = admission.service.acquire_worker(holder)
    claim = waiting(admission)
    admission.service.release(holder, fence(admitted))
    with admission.sessions.begin() as session:
        result = PostgresJobRepository(session).defer_for_resource(
            claim.fence, claim.request.operation_id, timedelta(seconds=2)
        )
        assert result is ResourceWaitTransition.GRANTED
    with admission.sessions() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        assert job.state == "RUNNING" and job.resource_wait_count == 0
        assert (
            session.scalar(select(JobAttemptRow.outcome).where(JobAttemptRow.job_id == job.job_id))
            is None
        )
    assert (
        admission.service.poll(claim, claim.request.operation_id).operation.state
        == AdmissionState.ACTIVE
    )


@pytest.mark.parametrize("deferred", [False, True])
def test_cancel_wins_before_or_after_defer(admission: AdmissionHarness, deferred: bool) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admission.service.acquire_worker(holder)
    claim = waiting(admission)
    if deferred:
        with pytest.raises(JobResourceWait):
            context(admission, claim).defer_for_resource(claim.request.operation_id)
    with admission.sessions.begin() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        assert job.user_id is not None
        PostgresJobRepository(session).request_cancel_for_owner(
            job_id=job.job_id, owner_user_id=job.user_id
        )
    if not deferred:
        with pytest.raises(JobResourceWait) as caught:
            context(admission, claim).defer_for_resource(claim.request.operation_id)
        assert caught.value.cancelled
    admission.service.sweep()
    with admission.sessions() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        assert job.state == "CANCELLED" and not job.resource_waiting
        assert (
            present(session.get(ResourceAdmissionRow, claim.request.operation_id)).state
            == "EXPIRED"
        )
    with admission.sessions.begin() as session:
        assert (
            PostgresJobRepository(session).defer_for_resource(
                claim.fence, claim.request.operation_id, timedelta(seconds=2)
            )
            is ResourceWaitTransition.LOST_LEASE
        )


def test_durable_wait_survives_old_age_and_cleanup_advances_past_valid_prefix(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admission.service.acquire_worker(holder)
    claims = [waiting(admission) for _ in range(3)]
    for index, claim in enumerate(claims):
        with pytest.raises(JobResourceWait):
            context(admission, claim).defer_for_resource(claim.request.operation_id)
        with admission.sessions.begin() as session:
            row = present(session.get(ResourceAdmissionRow, claim.request.operation_id))
            row.created_at = row.updated_at = row.enqueued_at = datetime.now(UTC) - timedelta(
                minutes=3 - index
            )
    with admission.sessions.begin() as session:
        lock_resource_admission(session)
        last = present(session.get(ResourceAdmissionRow, claims[-1].request.operation_id))
        present(session.get(UserAccountRow, last.user_id)).authority_generation += 1
    for _ in claims:
        admission.service.sweep(maximum=1)
    with admission.sessions() as session:
        rows = [
            present(session.get(ResourceAdmissionRow, claim.request.operation_id))
            for claim in claims
        ]
        assert [row.state for row in rows] == ["WAITING", "WAITING", "EXPIRED"]
        assert all(row.waiting_until is None for row in rows)
        age = rows[0].enqueued_at
    due(admission, claims[0].fence.job_id)
    with admission.sessions.begin() as session:
        for other in claims[1:]:
            present(session.get(JobRow, other.fence.job_id)).scheduled_at = datetime.now(
                UTC
            ) + timedelta(hours=1)
        session.flush()
        lease = PostgresJobRepository(session).claim(
            worker_id="reclaimed",
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    # Cleanup between claim and rebind must not treat the previous fence as lost intent.
    admission.service.sweep()
    rebound = admission.service.acquire_worker(replace(claims[0], fence=lease.fence))
    assert rebound.operation.enqueued_at == age


def test_expired_real_attempt_after_many_waits_uses_effective_retry_budget(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admission.service.acquire_worker(holder)
    claim = waiting(admission)
    for _ in range(3):
        with pytest.raises(JobResourceWait):
            context(admission, claim).defer_for_resource(claim.request.operation_id)
        due(admission, claim.fence.job_id)
        with admission.sessions.begin() as session:
            lease = PostgresJobRepository(session).claim(
                worker_id="reclaim",
                supported=(INTERNET_ACQUIRE_JOB,),
                lease_interval=timedelta(minutes=1),
                limit=1,
            )[0]
        claim = replace(claim, fence=lease.fence)
        admission.service.acquire_worker(claim)
    with admission.sessions.begin() as session:
        present(session.get(JobRow, claim.fence.job_id)).lease_deadline = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    with admission.sessions.begin() as session:
        recovered = PostgresJobRepository(session).recover_expired(
            supported=(INTERNET_ACQUIRE_JOB,),
            limit=1,
            policy=RetryPolicy(max_attempts=2),
        )
        assert recovered[0].state is JobState.RETRY_WAIT
        assert (
            PostgresJobRepository(session).fail_retryable(
                claim.fence, JobError("test.stale", {}), RetryPolicy(max_attempts=2)
            )
            is LeaseTransition.LOST_LEASE
        )


@pytest.mark.parametrize("swept", [False, True])
def test_expired_grant_requests_recheck_without_failing_job(
    admission: AdmissionHarness,
    swept: bool,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admitted = admission.service.acquire_worker(holder)
    claim = waiting(admission)
    admission.service.release(holder, fence(admitted))
    with admission.sessions.begin() as session:
        row = present(session.get(ResourceAdmissionRow, claim.request.operation_id))
        row.claim_until = datetime.now(UTC) - timedelta(seconds=1)
    if swept:
        admission.service.sweep()
    with admission.sessions.begin() as session:
        result = PostgresJobRepository(session).defer_for_resource(
            claim.fence, claim.request.operation_id, timedelta(seconds=2)
        )
        assert result is ResourceWaitTransition.RECHECK
    with admission.sessions() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        assert job.state == "RUNNING" and job.resource_wait_count == 0
    assert admission.service.acquire_worker(claim).operation.state is AdmissionState.ACTIVE


@pytest.mark.parametrize("claimed_before_newcomer", [False, True])
def test_fair_dormant_a1_winner_precedes_new_internet_and_survives_claim_gap(
    admission: AdmissionHarness,
    claimed_before_newcomer: bool,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admitted = admission.service.acquire_worker(holder)
    original = a1(admission)
    waiting_status = admission.service.acquire_worker(original)
    assert waiting_status.operation.state is AdmissionState.WAITING
    with pytest.raises(JobResourceWait):
        context(admission, original).defer_for_resource(original.request.operation_id)
    admission.service.release(holder, fence(admitted))
    extra = JobKey("test.new_ordinary", 1)
    with admission.sessions.begin() as session:
        PostgresJobRepository(session).enqueue(EnqueueJob(extra, None, {}, priority=0))

    def claim_winner() -> JobLease:
        with admission.sessions.begin() as session:
            lease = PostgresJobRepository(session).claim(
                worker_id="fair-winner",
                supported=(extra, JobKey("discovery.acquire", 1)),
                lease_interval=timedelta(minutes=1),
                limit=1,
            )[0]
            assert lease.fence.job_id == original.fence.job_id
            return lease

    lease = claim_winner() if claimed_before_newcomer else None
    _, newcomer = internet(admission, worker="new-internet")
    younger = admission.service.acquire_worker(newcomer)
    assert younger.operation.state is AdmissionState.WAITING
    lease = lease or claim_winner()
    winner = admission.service.acquire_worker(replace(original, fence=lease.fence))
    assert winner.operation.state is AdmissionState.ACTIVE
    assert winner.operation.enqueued_at == waiting_status.operation.enqueued_at
    assert (
        admission.service.poll(newcomer, newcomer.request.operation_id).operation.state
        is AdmissionState.WAITING
    )


def test_absent_worker_wake_grace_is_fixed_and_does_not_block_capacity_forever(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admitted = admission.service.acquire_worker(holder)
    original = a1(admission)
    waiting_status = admission.service.acquire_worker(original)
    with pytest.raises(JobResourceWait):
        context(admission, original).defer_for_resource(original.request.operation_id)
    admission.service.release(holder, fence(admitted))
    with admission.sessions() as session:
        deadline = present(session.get(JobRow, original.fence.job_id)).resource_wake_until
        assert deadline is not None
    admission.service.sweep()
    with admission.sessions.begin() as session:
        job = present(session.get(JobRow, original.fence.job_id))
        assert job.resource_wake_until == deadline
        job.resource_wake_until = datetime.now(UTC) - timedelta(seconds=1)
    _, newcomer = internet(admission, worker="new-internet")
    younger = admission.service.acquire_worker(newcomer)
    assert younger.operation.state is AdmissionState.ACTIVE
    with admission.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="returned-worker",
            supported=(JobKey("discovery.acquire", 1),),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    resumed = replace(original, fence=lease.fence)
    status = admission.service.acquire_worker(resumed)
    assert status.operation.state is AdmissionState.WAITING
    assert status.operation.enqueued_at == waiting_status.operation.enqueued_at
    admission.service.release(newcomer, fence(younger))
    assert (
        admission.service.poll(resumed, resumed.request.operation_id).operation.state
        is AdmissionState.ACTIVE
    )


def test_a1_deferred_intent_expires_on_source_revoke(admission: AdmissionHarness) -> None:
    admission.budget(transfers=1)
    _, holder = internet(admission)
    admission.service.acquire_worker(holder)
    claim = a1(admission)
    assert admission.service.acquire_worker(claim).operation.state is AdmissionState.WAITING
    with pytest.raises(JobResourceWait):
        context(admission, claim).defer_for_resource(claim.request.operation_id)
    admission.service.sweep()
    with admission.sessions.begin() as session:
        lock_resource_admission(session)
        assert (
            present(session.get(ResourceAdmissionRow, claim.request.operation_id)).state
            == "WAITING"
        )
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        present(
            session.get(SourceAuthorizationRow, attempt.source_authorization_id)
        ).revoked_at = datetime.now(UTC)
    admission.service.sweep()
    with admission.sessions() as session:
        assert (
            present(session.get(ResourceAdmissionRow, claim.request.operation_id)).state
            == "EXPIRED"
        )


def test_successor_yields_while_exact_old_execution_remains_charged(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    _, original = internet(admission)
    status = admission.service.acquire_worker(original)
    permit = admission.service.open_io(original, fence(status), original.acquisition_id)
    ticket = ExecutionTicket(
        uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, original.acquisition_id
    )
    child = ProcessIdentity(24680, b"p" * 32)
    admission.service.prepare_execution(original, ticket)
    admission.service.start_execution(original, ticket, child)
    with admission.sessions.begin() as session:
        present(session.get(JobRow, original.fence.job_id)).lease_deadline = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    with admission.sessions.begin() as session:
        PostgresJobRepository(session).recover_expired(
            supported=(INTERNET_ACQUIRE_JOB,),
            limit=1,
            policy=RetryPolicy(),
        )
    due(admission, original.fence.job_id)
    with admission.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="successor",
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    successor = replace(original, fence=lease.fence)
    blocked = admission.service.acquire_worker(successor)
    assert blocked.operation.state is AdmissionState.WAITING and blocked.usage.server == 1
    with pytest.raises(JobResourceWait):
        context(admission, successor).defer_for_resource(successor.request.operation_id)
    admission.service.sweep()
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    )
    admission.service.close_io(permit)
    due(admission, original.fence.job_id)
    with admission.sessions.begin() as session:
        final_lease = PostgresJobRepository(session).claim(
            worker_id="final-claim",
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    final = admission.service.acquire_worker(replace(original, fence=final_lease.fence))
    assert final.operation.state is AdmissionState.ACTIVE and final.usage.server == 1
    assert final_lease.fence.attempt_no == 3 and final_lease.retry_attempt_no == 2
