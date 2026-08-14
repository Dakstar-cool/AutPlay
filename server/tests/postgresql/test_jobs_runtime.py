"""Real-PostgreSQL evidence for the P03 fenced jobs repository and worker."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier
from typing import Any

import pytest
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.application.job_worker import (
    JobExecutionContext,
    JobHandlerRegistry,
    JobWorker,
    WorkerOutcome,
)
from autplay.domain.jobs import (
    CancelRequestResult,
    JobCancellationRequested,
    JobError,
    JobKey,
    JobLease,
    JobState,
    LeaseTransition,
    RetryableJobError,
    RetryPolicy,
)
from autplay.ports.jobs import EnqueueJob, JobIdempotencyConflict
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

TEST_KEY = JobKey("p03.test", 1)
LEASE = timedelta(minutes=5)


def test_worker_cli_readiness_and_once_use_the_migrated_database(database_url: str) -> None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        {
            "AUTPLAY_DATABASE_URL": database_url,
            "AUTPLAY_PROFILE": "test",
        }
    )
    readiness = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.worker_cpu", "--check-readiness"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    once = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.worker_cpu", "--once"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert readiness.returncode == 0, readiness.stderr
    assert readiness.stdout == '{"status":"ready","service":"autplay-worker-cpu"}\n'
    assert once.returncode == 0, once.stderr
    assert once.stdout == ""


@pytest.fixture
def job_uow_factory(database_url: str) -> Iterator[SqlAlchemyJobUnitOfWorkFactory]:
    """Create independent SQLAlchemy sessions for one disposable test database."""

    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    try:
        yield SqlAlchemyJobUnitOfWorkFactory(sessions)
    finally:
        engine.dispose()


def _enqueue(
    factory: SqlAlchemyJobUnitOfWorkFactory,
    *,
    user_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    scheduled_at: datetime | None = None,
    idempotency_scope: str | None = None,
    idempotency_key: str | None = None,
) -> uuid.UUID:
    with factory() as unit:
        result = unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=user_id,
                payload=payload or {},
                scheduled_at=scheduled_at,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
            )
        )
        unit.commit()
    return result.job_id


def _claim_one(factory: SqlAlchemyJobUnitOfWorkFactory, worker_id: str = "p03-worker") -> JobLease:
    with factory() as unit:
        leases = unit.jobs.claim(
            worker_id=worker_id,
            supported=(TEST_KEY,),
            lease_interval=LEASE,
            limit=1,
        )
        unit.commit()
    assert len(leases) == 1
    return leases[0]


def _insert_user(connection: Connection[Any], label: str) -> uuid.UUID:
    row = connection.execute(
        """
        INSERT INTO account.user_account (display_name)
        VALUES (%s)
        RETURNING user_id
        """,
        (f"p03-{label}-{uuid.uuid4().hex}",),
    ).fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("user fixture did not return a UUID")
    return row[0]


def test_enqueue_replays_only_identical_input(
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    job_id = _enqueue(
        job_uow_factory,
        payload={"item": 1},
        idempotency_scope="p03-test",
        idempotency_key="same",
    )
    with job_uow_factory() as unit:
        replay = unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={"item": 1},
                idempotency_scope="p03-test",
                idempotency_key="same",
            )
        )
        unit.commit()
    assert replay.job_id == job_id
    assert replay.replayed

    with pytest.raises(JobIdempotencyConflict), job_uow_factory() as unit:
        unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={"item": 2},
                idempotency_scope="p03-test",
                idempotency_key="same",
            )
        )


def test_enqueue_replays_same_scheduled_instant_after_timestamptz_normalizes_offset(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    scheduled_plus_three = datetime(
        2026,
        8,
        15,
        15,
        30,
        45,
        123456,
        tzinfo=timezone(timedelta(hours=3)),
    )
    scheduled_utc = datetime(2026, 8, 15, 12, 30, 45, 123456, tzinfo=UTC)
    job_id = _enqueue(
        job_uow_factory,
        scheduled_at=scheduled_plus_three,
        idempotency_scope="p03-scheduled-offset",
        idempotency_key="same-instant",
    )

    with job_uow_factory() as unit:
        replay = unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={},
                scheduled_at=scheduled_utc,
                idempotency_scope="p03-scheduled-offset",
                idempotency_key="same-instant",
            )
        )
        unit.commit()

    assert replay.job_id == job_id
    assert replay.replayed
    stored = database_connection.execute(
        """
        SELECT scheduled_at = %s, scheduled_at AT TIME ZONE 'UTC'
        FROM jobs.job
        WHERE job_id = %s
        """,
        (scheduled_utc, job_id),
    ).fetchone()
    assert stored == (True, scheduled_utc.replace(tzinfo=None))


def test_omitted_schedule_replay_distinguishes_database_default_from_explicit_future(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    omitted_id = _enqueue(
        job_uow_factory,
        idempotency_scope="p03-omitted-schedule",
        idempotency_key="omitted-original",
    )
    with job_uow_factory() as unit:
        omitted_replay = unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={},
                scheduled_at=None,
                idempotency_scope="p03-omitted-schedule",
                idempotency_key="omitted-original",
            )
        )
        unit.commit()
    assert omitted_replay.job_id == omitted_id
    assert omitted_replay.replayed

    explicit_id = _enqueue(
        job_uow_factory,
        scheduled_at=datetime(2099, 1, 1, tzinfo=UTC),
        idempotency_scope="p03-omitted-schedule",
        idempotency_key="explicit-original",
    )
    with pytest.raises(JobIdempotencyConflict), job_uow_factory() as unit:
        unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={},
                scheduled_at=None,
                idempotency_scope="p03-omitted-schedule",
                idempotency_key="explicit-original",
            )
        )

    schedule_origins = database_connection.execute(
        """
        SELECT job_id, scheduled_at = created_at
        FROM jobs.job
        WHERE job_id IN (%s, %s)
        ORDER BY job_id
        """,
        (omitted_id, explicit_id),
    ).fetchall()
    assert dict(schedule_origins) == {omitted_id: True, explicit_id: False}


def test_enqueue_payload_replay_is_type_aware_for_nested_bool_and_integer(
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    _enqueue(
        job_uow_factory,
        payload={"nested": {"value": True}},
        idempotency_scope="p03-json-types",
        idempotency_key="bool-not-int",
    )

    with pytest.raises(JobIdempotencyConflict), job_uow_factory() as unit:
        unit.jobs.enqueue(
            EnqueueJob(
                key=TEST_KEY,
                user_id=None,
                payload={"nested": {"value": 1}},
                idempotency_scope="p03-json-types",
                idempotency_key="bool-not-int",
            )
        )


def _claim_concurrently(
    factory: SqlAlchemyJobUnitOfWorkFactory,
    start: Barrier,
    claimed: Barrier,
    worker_id: str,
) -> tuple[uuid.UUID, ...]:
    with factory() as unit:
        start.wait(timeout=15)
        leases = unit.jobs.claim(
            worker_id=worker_id,
            supported=(TEST_KEY,),
            lease_interval=LEASE,
            limit=2,
        )
        claimed.wait(timeout=15)
        unit.commit()
    return tuple(lease.fence.job_id for lease in leases)


def _future_results(futures: list[Future[tuple[uuid.UUID, ...]]]) -> list[tuple[uuid.UUID, ...]]:
    return [future.result(timeout=30) for future in futures]


def test_concurrent_claim_is_disjoint_and_creates_one_open_attempt_per_job(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    worker_count = 4
    for _ in range(worker_count * 2):
        _enqueue(job_uow_factory)
    start = Barrier(worker_count)
    claimed = Barrier(worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _claim_concurrently,
                job_uow_factory,
                start,
                claimed,
                f"p03-concurrent-{index}",
            )
            for index in range(worker_count)
        ]
        results = _future_results(futures)

    all_ids = [job_id for result in results for job_id in result]
    assert all(len(result) == 2 for result in results)
    assert len(all_ids) == len(set(all_ids)) == worker_count * 2
    rows = database_connection.execute(
        """
        SELECT count(*), count(*) FILTER (
            WHERE finished_at IS NULL AND outcome IS NULL
        ), count(DISTINCT (job_id, attempt_no))
        FROM jobs.job_attempt
        """
    ).fetchone()
    assert rows == (worker_count * 2, worker_count * 2, worker_count * 2)


def test_claim_rollback_leaves_no_attempt_and_can_be_reclaimed(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    job_id = _enqueue(job_uow_factory)
    with job_uow_factory() as unit:
        leases = unit.jobs.claim(
            worker_id="p03-rollback",
            supported=(TEST_KEY,),
            lease_interval=LEASE,
            limit=1,
        )
        assert leases[0].fence.attempt_no == 1

    lease = _claim_one(job_uow_factory, "p03-after-rollback")
    assert lease.fence.job_id == job_id
    assert lease.fence.attempt_no == 1
    row = database_connection.execute(
        "SELECT count(*) FROM jobs.job_attempt WHERE job_id = %s", (job_id,)
    ).fetchone()
    assert row == (1,)


def test_stale_attempt_cannot_heartbeat_checkpoint_or_complete_after_recovery(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    _enqueue(job_uow_factory)
    first = _claim_one(job_uow_factory, "p03-reused-worker")
    with job_uow_factory() as unit:
        heartbeat = unit.jobs.heartbeat(first.fence, LEASE)
        assert heartbeat is not None
        saved = unit.jobs.save_checkpoint(
            first.fence,
            {"cursor": 4},
            progress_current=4,
            progress_total=10,
        )
        assert saved is not None and not saved.cancel_requested
        unit.commit()
    database_connection.execute(
        "UPDATE jobs.job SET lease_deadline = now() - interval '1 second' WHERE job_id = %s",
        (first.fence.job_id,),
    )
    database_connection.commit()
    with job_uow_factory() as unit:
        recovered = unit.jobs.recover_expired(supported=(TEST_KEY,), limit=10, policy=RetryPolicy())
        unit.commit()
    assert recovered[0].state is JobState.RETRY_WAIT
    database_connection.execute(
        "UPDATE jobs.job SET scheduled_at = now() - interval '1 second' WHERE job_id = %s",
        (first.fence.job_id,),
    )
    database_connection.commit()
    second = _claim_one(job_uow_factory, "p03-reused-worker")
    assert second.fence.attempt_no == 2
    assert second.checkpoint == {"cursor": 4}

    with job_uow_factory() as unit:
        assert unit.jobs.heartbeat(first.fence, LEASE) is None
        assert (
            unit.jobs.save_checkpoint(
                first.fence,
                {"cursor": 99},
                progress_current=9,
                progress_total=10,
            )
            is None
        )
        assert unit.jobs.complete(first.fence) is LeaseTransition.LOST_LEASE
        unit.commit()
    with job_uow_factory() as unit:
        assert unit.jobs.complete(second.fence) is LeaseTransition.APPLIED
        unit.commit()


def test_retry_limit_closes_attempt_history_and_terminal_job_is_not_reclaimed(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    policy = RetryPolicy(
        max_attempts=2,
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=1),
        jitter_ratio=0,
    )
    job_id = _enqueue(job_uow_factory)
    first = _claim_one(job_uow_factory)
    with job_uow_factory() as unit:
        assert (
            unit.jobs.fail_retryable(first.fence, JobError("job.transient", {}), policy)
            is LeaseTransition.APPLIED
        )
        unit.commit()
    database_connection.execute(
        "UPDATE jobs.job SET scheduled_at = now() - interval '1 second' WHERE job_id = %s",
        (job_id,),
    )
    database_connection.commit()
    second = _claim_one(job_uow_factory)
    with job_uow_factory() as unit:
        assert (
            unit.jobs.fail_retryable(second.fence, JobError("job.transient", {}), policy)
            is LeaseTransition.APPLIED
        )
        unit.commit()

    row = database_connection.execute(
        """
        SELECT state, attempt_count, lease_owner, completed_at IS NOT NULL, error_code
        FROM jobs.job WHERE job_id = %s
        """,
        (job_id,),
    ).fetchone()
    attempts = database_connection.execute(
        """
        SELECT attempt_no, outcome, finished_at IS NOT NULL
        FROM jobs.job_attempt WHERE job_id = %s ORDER BY attempt_no
        """,
        (job_id,),
    ).fetchall()
    assert row == ("FAILED", 2, None, True, "job.retry_exhausted")
    assert attempts == [(1, "RETRYABLE_ERROR", True), (2, "RETRYABLE_ERROR", True)]
    with job_uow_factory() as unit:
        assert not unit.jobs.claim(
            worker_id="p03-no-terminal-reclaim",
            supported=(TEST_KEY,),
            lease_interval=LEASE,
            limit=1,
        )
        unit.commit()


def test_owner_scoped_cancel_is_safe_for_pending_and_running_jobs(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    owner = _insert_user(database_connection, "owner")
    other = _insert_user(database_connection, "other")
    database_connection.commit()
    pending_id = _enqueue(job_uow_factory, user_id=owner)
    with job_uow_factory() as unit:
        assert (
            unit.jobs.request_cancel_for_owner(job_id=pending_id, owner_user_id=other)
            is CancelRequestResult.NOT_FOUND
        )
        unit.commit()
    with job_uow_factory() as unit:
        assert (
            unit.jobs.request_cancel_for_owner(job_id=pending_id, owner_user_id=owner)
            is CancelRequestResult.CANCELLED
        )
        unit.commit()

    running_id = _enqueue(job_uow_factory, user_id=owner)
    running = _claim_one(job_uow_factory)
    assert running.fence.job_id == running_id
    with job_uow_factory() as unit:
        assert (
            unit.jobs.request_cancel_for_owner(job_id=running_id, owner_user_id=owner)
            is CancelRequestResult.REQUESTED
        )
        unit.commit()
    with job_uow_factory() as unit:
        heartbeat = unit.jobs.heartbeat(running.fence, LEASE)
        assert heartbeat is not None and heartbeat.cancel_requested
        assert unit.jobs.complete(running.fence) is LeaseTransition.CANCELLED
        unit.commit()
    with job_uow_factory() as unit:
        assert (
            unit.jobs.request_cancel_for_owner(job_id=running_id, owner_user_id=owner)
            is CancelRequestResult.ALREADY_TERMINAL
        )
        unit.commit()


def test_checkpoint_atomically_observes_cancel_and_stops_after_durable_save(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    owner = _insert_user(database_connection, "checkpoint-cancel")
    database_connection.commit()
    job_id = _enqueue(job_uow_factory, user_id=owner)
    lease = _claim_one(job_uow_factory, "p03-checkpoint-cancel")
    assert lease.fence.job_id == job_id

    with job_uow_factory() as unit:
        assert (
            unit.jobs.request_cancel_for_owner(job_id=job_id, owner_user_id=owner)
            is CancelRequestResult.REQUESTED
        )
        unit.commit()

    with job_uow_factory() as unit:
        saved = unit.jobs.save_checkpoint(
            lease.fence,
            {"cursor": 4},
            progress_current=4,
            progress_total=10,
        )
        assert saved is not None and saved.cancel_requested
        observed_cancel_at = saved.cancel_requested_at
        unit.commit()

    context = JobExecutionContext(
        uow_factory=job_uow_factory,
        fence=lease.fence,
        lease_interval=LEASE,
    )
    with pytest.raises(JobCancellationRequested, match=r"job\.cancel_requested"):
        context.checkpoint(
            {"cursor": 5},
            progress_current=5,
            progress_total=10,
        )

    row = database_connection.execute(
        """
        SELECT checkpoint, progress_current, progress_total, cancel_requested_at, state
        FROM jobs.job
        WHERE job_id = %s
        """,
        (job_id,),
    ).fetchone()
    assert row == ({"cursor": 5}, 5, 10, observed_cancel_at, "RUNNING")

    with job_uow_factory() as unit:
        assert unit.jobs.acknowledge_cancel(lease.fence) is LeaseTransition.CANCELLED
        unit.commit()
    terminal = database_connection.execute(
        """
        SELECT state, lease_owner, completed_at IS NOT NULL
        FROM jobs.job
        WHERE job_id = %s
        """,
        (job_id,),
    ).fetchone()
    assert terminal == ("CANCELLED", None, True)


def _recover_concurrently(
    factory: SqlAlchemyJobUnitOfWorkFactory,
    start: Barrier,
    locked: Barrier,
) -> tuple[uuid.UUID, ...]:
    with factory() as unit:
        start.wait(timeout=15)
        recovered = unit.jobs.recover_expired(supported=(TEST_KEY,), limit=1, policy=RetryPolicy())
        locked.wait(timeout=15)
        unit.commit()
    return tuple(item.job_id for item in recovered)


def test_concurrent_expired_lease_recovery_has_one_winner(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    _enqueue(job_uow_factory)
    lease = _claim_one(job_uow_factory)
    database_connection.execute(
        "UPDATE jobs.job SET lease_deadline = now() - interval '1 second' WHERE job_id = %s",
        (lease.fence.job_id,),
    )
    database_connection.commit()
    worker_count = 4
    start = Barrier(worker_count)
    locked = Barrier(worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_recover_concurrently, job_uow_factory, start, locked)
            for _ in range(worker_count)
        ]
        results = _future_results(futures)
    assert sum(len(result) for result in results) == 1
    assert [job_id for result in results for job_id in result] == [lease.fence.job_id]


def _complete_handler(context: JobExecutionContext, lease: JobLease) -> None:
    context.checkpoint({"handled": True}, progress_current=1, progress_total=1)
    assert lease.key == TEST_KEY


def _retry_handler(context: JobExecutionContext, lease: JobLease) -> None:
    del context, lease
    raise RetryableJobError("job.try_again", {})


def test_worker_executes_registered_handler_and_never_claims_without_one(
    database_connection: Connection[Any],
    job_uow_factory: SqlAlchemyJobUnitOfWorkFactory,
) -> None:
    unknown_id = _enqueue(job_uow_factory)
    empty = JobWorker(uow_factory=job_uow_factory, worker_id="p03-empty")
    assert empty.run_once().outcome is WorkerOutcome.IDLE
    state = database_connection.execute(
        "SELECT state FROM jobs.job WHERE job_id = %s", (unknown_id,)
    ).fetchone()
    assert state == ("QUEUED",)

    complete_worker = JobWorker(
        uow_factory=job_uow_factory,
        worker_id="p03-complete",
        registry=JobHandlerRegistry({TEST_KEY: _complete_handler}),
    )
    assert complete_worker.run_once().outcome is WorkerOutcome.COMPLETED

    retry_id = _enqueue(job_uow_factory)
    retry_worker = JobWorker(
        uow_factory=job_uow_factory,
        worker_id="p03-retry",
        registry=JobHandlerRegistry({TEST_KEY: _retry_handler}),
    )
    assert retry_worker.run_once().outcome is WorkerOutcome.RETRY_SCHEDULED
    retry_state = database_connection.execute(
        "SELECT state FROM jobs.job WHERE job_id = %s", (retry_id,)
    ).fetchone()
    assert retry_state == ("RETRY_WAIT",)
