"""Concurrent PostgreSQL job-claim invariants owned by P02."""

from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from typing import Any

import pytest
from psycopg import Connection

from .conftest import DatabaseHarness

CLAIM_BATCH_SIZE = 3
LEASE_WORKER_COUNT = 8

CLAIM_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT job_id
    FROM jobs.job
    WHERE state IN ('QUEUED', 'RETRY_WAIT')
      AND scheduled_at <= now()
    ORDER BY priority ASC, scheduled_at ASC, created_at ASC, job_id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT %(batch_size)s
)
UPDATE jobs.job AS job
SET state = 'RUNNING',
    lease_owner = %(worker_id)s,
    lease_deadline = now() + interval '5 minutes',
    heartbeat_at = now(),
    started_at = COALESCE(job.started_at, now()),
    attempt_count = job.attempt_count + 1
FROM candidate
WHERE job.job_id = candidate.job_id
RETURNING job.job_id
"""

RECLAIM_EXPIRED_SQL = """
WITH expired AS MATERIALIZED (
    SELECT job_id
    FROM jobs.job
    WHERE state = 'RUNNING'
      AND lease_deadline < now()
    ORDER BY lease_deadline ASC, job_id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
),
closed_attempt AS (
    UPDATE jobs.job_attempt AS attempt
    SET finished_at = now(),
        outcome = 'LEASE_EXPIRED'
    FROM expired
    WHERE attempt.job_id = expired.job_id
      AND attempt.finished_at IS NULL
      AND attempt.outcome IS NULL
    RETURNING attempt.job_id
)
UPDATE jobs.job AS job
SET state = 'RETRY_WAIT',
    scheduled_at = now(),
    lease_owner = NULL,
    lease_deadline = NULL,
    heartbeat_at = NULL
FROM expired
WHERE job.job_id = expired.job_id
  AND EXISTS (
      SELECT 1
      FROM closed_attempt
      WHERE closed_attempt.job_id = job.job_id
  )
RETURNING job.job_id
"""


@dataclass(frozen=True)
class WorkerResult:
    """One independent PostgreSQL backend's claimed job identifiers."""

    backend_pid: int
    worker_id: str
    job_ids: tuple[uuid.UUID, ...]


def _backend_pid(connection: Connection[Any]) -> int:
    row = connection.execute("SELECT pg_backend_pid()").fetchone()
    if row is None or not isinstance(row[0], int):
        raise AssertionError("PostgreSQL did not return an integer backend PID")
    return row[0]


def _returned_job_ids(
    connection: Connection[Any], statement: str, params: dict[str, Any]
) -> tuple[uuid.UUID, ...]:
    rows = connection.execute(statement, params).fetchall()
    job_ids: list[uuid.UUID] = []
    for row in rows:
        if not isinstance(row[0], uuid.UUID):
            raise AssertionError("job claim did not return a UUID")
        job_ids.append(row[0])
    return tuple(job_ids)


def _claim_worker(
    database_harness: DatabaseHarness,
    database_name: str,
    start_barrier: Barrier,
    claimed_barrier: Barrier,
    worker_id: str,
) -> WorkerResult:
    with database_harness.connect(database_name) as connection:
        backend_pid = _backend_pid(connection)
        start_barrier.wait(timeout=15.0)
        job_ids = _returned_job_ids(
            connection,
            CLAIM_SQL,
            {"batch_size": CLAIM_BATCH_SIZE, "worker_id": worker_id},
        )
        # Keep row locks until every backend has completed its claim. This makes
        # the test exercise SKIP LOCKED instead of allowing sequential commits.
        claimed_barrier.wait(timeout=15.0)
    return WorkerResult(backend_pid, worker_id, job_ids)


def _reclaim_expired_worker(
    database_harness: DatabaseHarness,
    database_name: str,
    start_barrier: Barrier,
    reclaimed_barrier: Barrier,
    worker_id: str,
) -> WorkerResult:
    with database_harness.connect(database_name) as connection:
        backend_pid = _backend_pid(connection)
        start_barrier.wait(timeout=15.0)
        job_ids = _returned_job_ids(connection, RECLAIM_EXPIRED_SQL, {})
        # Hold the winning row lock until every competing backend has skipped it.
        reclaimed_barrier.wait(timeout=15.0)
    return WorkerResult(backend_pid, worker_id, job_ids)


def _collect_results(futures: list[Future[WorkerResult]]) -> list[WorkerResult]:
    return [future.result(timeout=30.0) for future in futures]


def _assert_independent_connections(results: list[WorkerResult]) -> None:
    assert len({result.backend_pid for result in results}) == len(results)
    assert len({result.worker_id for result in results}) == len(results)


@pytest.mark.parametrize("worker_count", (2, 4, 8))
def test_concurrent_claim_batches_never_duplicate_job_ids(
    database_harness: DatabaseHarness,
    database_name: str,
    worker_count: int,
) -> None:
    """2/4/8 independent backends claim disjoint batches while locks overlap."""

    job_count = worker_count * CLAIM_BATCH_SIZE
    with database_harness.connect(database_name) as connection:
        inserted_rows = connection.execute(
            """
            INSERT INTO jobs.job (
                job_type, schema_version, priority, scheduled_at
            )
            SELECT 'p02-concurrent-claim', 1, 3, now() - interval '1 minute'
            FROM generate_series(1, %s)
            RETURNING job_id
            """,
            (job_count,),
        ).fetchall()
        assert len(inserted_rows) == job_count

    start_barrier = Barrier(worker_count)
    claimed_barrier = Barrier(worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _claim_worker,
                database_harness,
                database_name,
                start_barrier,
                claimed_barrier,
                f"p02-worker-{index}",
            )
            for index in range(worker_count)
        ]
        results = _collect_results(futures)

    _assert_independent_connections(results)
    assert all(len(result.job_ids) == CLAIM_BATCH_SIZE for result in results)
    claimed_job_ids = [job_id for result in results for job_id in result.job_ids]
    assert len(claimed_job_ids) == job_count
    assert len(set(claimed_job_ids)) == job_count

    with database_harness.connect(database_name) as connection:
        state_rows = connection.execute(
            """
            SELECT state, count(*), count(DISTINCT lease_owner), min(attempt_count),
                   max(attempt_count), min(row_version), max(row_version)
            FROM jobs.job
            GROUP BY state
            """
        ).fetchall()
    assert state_rows == [("RUNNING", job_count, worker_count, 1, 1, 2, 2)]


def test_expired_lease_is_requeued_and_attempt_closed_exactly_once(
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    """Only one competing backend atomically expires an open attempt and lease."""

    with database_harness.connect(database_name) as connection:
        row = connection.execute(
            """
            INSERT INTO jobs.job (
                job_type, schema_version, state, attempt_count, lease_owner,
                lease_deadline, heartbeat_at, started_at, scheduled_at
            ) VALUES (
                'p02-expired-lease', 1, 'RUNNING', 1, 'stale-worker',
                now() - interval '1 minute', now() - interval '2 minutes',
                now() - interval '3 minutes', now() - interval '4 minutes'
            )
            RETURNING job_id, started_at
            """
        ).fetchone()
        if row is None or not isinstance(row[0], uuid.UUID):
            raise AssertionError("expired lease fixture did not return a job UUID")
        expired_job_id = row[0]
        connection.execute(
            """
            INSERT INTO jobs.job_attempt (job_id, attempt_no, worker_id, started_at)
            VALUES (%s, 1, 'stale-worker', %s)
            """,
            (expired_job_id, row[1]),
        )

    start_barrier = Barrier(LEASE_WORKER_COUNT)
    reclaimed_barrier = Barrier(LEASE_WORKER_COUNT)
    with ThreadPoolExecutor(max_workers=LEASE_WORKER_COUNT) as executor:
        futures = [
            executor.submit(
                _reclaim_expired_worker,
                database_harness,
                database_name,
                start_barrier,
                reclaimed_barrier,
                f"p02-reclaimer-{index}",
            )
            for index in range(LEASE_WORKER_COUNT)
        ]
        results = _collect_results(futures)

    _assert_independent_connections(results)
    winners = [result for result in results if result.job_ids]
    assert len(winners) == 1
    assert winners[0].job_ids == (expired_job_id,)
    assert sum(len(result.job_ids) for result in results) == 1

    with database_harness.connect(database_name) as connection:
        job_row = connection.execute(
            """
            SELECT state, lease_owner, lease_deadline, heartbeat_at, attempt_count,
                   row_version
            FROM jobs.job
            WHERE job_id = %s
            """,
            (expired_job_id,),
        ).fetchone()
        attempt_rows = connection.execute(
            """
            SELECT attempt_no, worker_id, outcome, finished_at IS NOT NULL,
                   finished_at >= started_at
            FROM jobs.job_attempt
            WHERE job_id = %s
            """,
            (expired_job_id,),
        ).fetchall()

    assert job_row == ("RETRY_WAIT", None, None, None, 1, 2)
    assert attempt_rows == [(1, "stale-worker", "LEASE_EXPIRED", True, True)]
