"""Fenced PostgreSQL runtime repository for durable jobs."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import INTERVAL, JSONB, TIMESTAMP
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from autplay.domain.jobs import (
    MAX_WORKER_ID_LENGTH,
    CancelRequestResult,
    CheckpointSaved,
    Heartbeat,
    JobAttemptOutcome,
    JobError,
    JobKey,
    JobLease,
    JobPersistenceInvariantError,
    JobState,
    JsonValue,
    LeaseFence,
    LeaseTransition,
    RetryPolicy,
    validate_job_document,
)
from autplay.ports.jobs import (
    EnqueueJob,
    EnqueueResult,
    JobIdempotencyConflict,
    RecoveredJob,
)

_MAX_CLAIM_BATCH: Final = 100
_MAX_RECOVERY_BATCH: Final = 100
_MAX_LEASE_INTERVAL: Final = timedelta(hours=1)
_MAX_IDEMPOTENCY_SCOPE_LENGTH: Final = 100
_MAX_IDEMPOTENCY_KEY_LENGTH: Final = 200

_ENQUEUE = text(
    """
    INSERT INTO jobs.job (
        job_type, schema_version, user_id, priority, scheduled_at,
        idempotency_scope, idempotency_key, payload
    ) VALUES (
        :job_type, :schema_version, :user_id, :priority,
        COALESCE(:scheduled_at, now()), :idempotency_scope, :idempotency_key,
        :payload
    )
    ON CONFLICT (idempotency_scope, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    DO NOTHING
    RETURNING job_id
    """
).bindparams(
    bindparam("scheduled_at", type_=TIMESTAMP(timezone=True)),
    bindparam("payload", type_=JSONB()),
)

_FIND_IDEMPOTENT = text(
    """
    SELECT job_id, job_type, schema_version, user_id, priority,
           scheduled_at, created_at, payload
    FROM jobs.job
    WHERE idempotency_scope = :idempotency_scope
      AND idempotency_key = :idempotency_key
    """
)

_HEARTBEAT = text(
    """
    UPDATE jobs.job
    SET heartbeat_at = now(),
        lease_deadline = now() + :lease_interval
    WHERE job_id = :job_id
      AND state = 'RUNNING'
      AND lease_owner = :worker_id
      AND attempt_count = :attempt_no
      AND lease_deadline > now()
    RETURNING lease_deadline, cancel_requested_at
    """
).bindparams(bindparam("lease_interval", type_=INTERVAL()))

_SAVE_CHECKPOINT = text(
    """
    UPDATE jobs.job
    SET checkpoint = :checkpoint,
        progress_current = :progress_current,
        progress_total = :progress_total
    WHERE job_id = :job_id
      AND state = 'RUNNING'
      AND lease_owner = :worker_id
      AND attempt_count = :attempt_no
      AND lease_deadline > now()
    RETURNING cancel_requested_at
    """
).bindparams(bindparam("checkpoint", type_=JSONB()))

_LOCK_ACTIVE_LEASE = text(
    """
    SELECT cancel_requested_at
    FROM jobs.job
    WHERE job_id = :job_id
      AND state = 'RUNNING'
      AND lease_owner = :worker_id
      AND attempt_count = :attempt_no
      AND lease_deadline > now()
    FOR UPDATE
    """
)

_CLOSE_ATTEMPT = text(
    """
    UPDATE jobs.job_attempt
    SET finished_at = now(),
        outcome = :outcome,
        error_code = :error_code,
        metrics = :metrics
    WHERE job_id = :job_id
      AND attempt_no = :attempt_no
      AND worker_id = :worker_id
      AND finished_at IS NULL
      AND outcome IS NULL
    RETURNING job_attempt_id
    """
).bindparams(bindparam("metrics", type_=JSONB()))

_SET_JOB_STATE = text(
    """
    UPDATE jobs.job
    SET state = :state,
        scheduled_at = CASE
            WHEN :state = 'RETRY_WAIT' THEN now() + :retry_delay
            ELSE scheduled_at
        END,
        lease_owner = NULL,
        lease_deadline = NULL,
        heartbeat_at = NULL,
        completed_at = CASE
            WHEN :state IN ('COMPLETED', 'FAILED', 'CANCELLED') THEN now()
            ELSE NULL
        END,
        error_code = :error_code,
        error_detail = :error_detail
    WHERE job_id = :job_id
      AND state = 'RUNNING'
      AND lease_owner = :worker_id
      AND attempt_count = :attempt_no
    RETURNING job_id
    """
).bindparams(
    bindparam("retry_delay", type_=INTERVAL()),
    bindparam("error_detail", type_=JSONB()),
)

_LOCK_OWNER_JOB = text(
    """
    SELECT state
    FROM jobs.job
    WHERE job_id = :job_id AND user_id = :owner_user_id
    FOR UPDATE
    """
)

_REQUEST_RUNNING_CANCEL = text(
    """
    UPDATE jobs.job
    SET cancel_requested_at = COALESCE(cancel_requested_at, now())
    WHERE job_id = :job_id
      AND user_id = :owner_user_id
      AND state = 'RUNNING'
    RETURNING job_id
    """
)

_CANCEL_PENDING_JOB = text(
    """
    UPDATE jobs.job
    SET state = 'CANCELLED',
        cancel_requested_at = COALESCE(cancel_requested_at, now()),
        completed_at = now(),
        error_code = NULL,
        error_detail = NULL
    WHERE job_id = :job_id
      AND user_id = :owner_user_id
      AND state IN ('QUEUED', 'RETRY_WAIT', 'PAUSED')
    RETURNING job_id
    """
)


class PostgresJobRepository:
    """Implement durable job operations inside a caller-owned ``Session``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, command: EnqueueJob) -> EnqueueResult:
        """Insert or safely replay one job without committing the transaction."""

        payload = validate_job_document(command.payload, field="payload")
        _validate_enqueue(command)
        params: dict[str, Any] = {
            "job_type": command.key.job_type,
            "schema_version": command.key.schema_version,
            "user_id": command.user_id,
            "priority": command.priority,
            "scheduled_at": command.scheduled_at,
            "idempotency_scope": command.idempotency_scope,
            "idempotency_key": command.idempotency_key,
            "payload": payload,
        }
        inserted = self._session.execute(_ENQUEUE, params).scalar_one_or_none()
        if inserted is not None:
            return EnqueueResult(_as_uuid(inserted, "job_id"), replayed=False)
        if command.idempotency_scope is None or command.idempotency_key is None:
            raise JobPersistenceInvariantError("non-idempotent enqueue returned no row")

        stored = (
            self._session.execute(
                _FIND_IDEMPOTENT,
                {
                    "idempotency_scope": command.idempotency_scope,
                    "idempotency_key": command.idempotency_key,
                },
            )
            .mappings()
            .one_or_none()
        )
        if stored is None:
            raise JobPersistenceInvariantError("idempotent enqueue conflict row is missing")
        if not _same_enqueue(stored, command, payload):
            raise JobIdempotencyConflict
        return EnqueueResult(_mapping_uuid(stored, "job_id"), replayed=True)

    def claim(
        self,
        *,
        worker_id: str,
        supported: Collection[JobKey],
        lease_interval: timedelta,
        limit: int,
    ) -> tuple[JobLease, ...]:
        """Claim jobs and append their attempt rows in the same transaction."""

        _validate_worker_id(worker_id)
        _validate_lease_interval(lease_interval)
        _validate_limit(limit, maximum=_MAX_CLAIM_BATCH, field="claim limit")
        supported_keys = tuple(
            sorted(set(supported), key=lambda key: (key.job_type, key.schema_version))
        )
        if not supported_keys:
            return ()

        predicates: list[str] = []
        params: dict[str, Any] = {
            "worker_id": worker_id,
            "lease_interval": lease_interval,
            "limit": limit,
        }
        for index, key in enumerate(supported_keys):
            predicates.append(
                f"(j.job_type = :job_type_{index} AND j.schema_version = :schema_version_{index})"
            )
            params[f"job_type_{index}"] = key.job_type
            params[f"schema_version_{index}"] = key.schema_version
        statement = text(_claim_sql(" OR ".join(predicates))).bindparams(
            bindparam("lease_interval", type_=INTERVAL())
        )
        rows = self._session.execute(statement, params).mappings().all()
        return tuple(_lease_from_row(row) for row in rows)

    def heartbeat(self, fence: LeaseFence, lease_interval: timedelta) -> Heartbeat | None:
        """Renew only the exact current, unexpired attempt."""

        _validate_lease_interval(lease_interval)
        row = (
            self._session.execute(
                _HEARTBEAT,
                {**_fence_params(fence), "lease_interval": lease_interval},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return Heartbeat(
            lease_deadline=_mapping_datetime(row, "lease_deadline"),
            cancel_requested_at=_mapping_optional_datetime(row, "cancel_requested_at"),
        )

    def save_checkpoint(
        self,
        fence: LeaseFence,
        checkpoint: Mapping[str, JsonValue],
        *,
        progress_current: int | None,
        progress_total: int | None,
    ) -> CheckpointSaved | None:
        """Persist a bounded checkpoint and observe cancellation in the same statement."""

        normalized = validate_job_document(checkpoint, field="checkpoint")
        _validate_progress(progress_current, progress_total)
        row = (
            self._session.execute(
                _SAVE_CHECKPOINT,
                {
                    **_fence_params(fence),
                    "checkpoint": normalized,
                    "progress_current": progress_current,
                    "progress_total": progress_total,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return CheckpointSaved(
            cancel_requested_at=_mapping_optional_datetime(row, "cancel_requested_at")
        )

    def complete(self, fence: LeaseFence) -> LeaseTransition:
        """Complete the current attempt, with cancellation winning at a safe point."""

        cancel_requested = self._lock_active_lease(fence)
        if cancel_requested is None:
            return LeaseTransition.LOST_LEASE
        if cancel_requested:
            self._finish(
                fence,
                outcome=JobAttemptOutcome.CANCELLED,
                state=JobState.CANCELLED,
                error=None,
            )
            return LeaseTransition.CANCELLED
        self._finish(
            fence,
            outcome=JobAttemptOutcome.SUCCESS,
            state=JobState.COMPLETED,
            error=None,
        )
        return LeaseTransition.APPLIED

    def fail_retryable(
        self,
        fence: LeaseFence,
        error: JobError,
        policy: RetryPolicy,
    ) -> LeaseTransition:
        """Close an attempt and either schedule retry or exhaust it safely."""

        cancel_requested = self._lock_active_lease(fence)
        if cancel_requested is None:
            return LeaseTransition.LOST_LEASE
        if cancel_requested:
            self._finish(
                fence,
                outcome=JobAttemptOutcome.CANCELLED,
                state=JobState.CANCELLED,
                error=None,
            )
            return LeaseTransition.CANCELLED
        if fence.attempt_no >= policy.max_attempts:
            exhausted = JobError(
                "job.retry_exhausted",
                {"last_error_code": error.code, "retry_exhausted": True},
            )
            self._finish(
                fence,
                outcome=JobAttemptOutcome.RETRYABLE_ERROR,
                state=JobState.FAILED,
                error=exhausted,
                attempt_error_code=error.code,
            )
        else:
            self._finish(
                fence,
                outcome=JobAttemptOutcome.RETRYABLE_ERROR,
                state=JobState.RETRY_WAIT,
                error=error,
                retry_delay=policy.delay_for(fence.job_id, fence.attempt_no),
            )
        return LeaseTransition.APPLIED

    def fail_terminal(self, fence: LeaseFence, error: JobError) -> LeaseTransition:
        """Close an attempt as terminal unless cancellation is already pending."""

        cancel_requested = self._lock_active_lease(fence)
        if cancel_requested is None:
            return LeaseTransition.LOST_LEASE
        if cancel_requested:
            self._finish(
                fence,
                outcome=JobAttemptOutcome.CANCELLED,
                state=JobState.CANCELLED,
                error=None,
            )
            return LeaseTransition.CANCELLED
        self._finish(
            fence,
            outcome=JobAttemptOutcome.TERMINAL_ERROR,
            state=JobState.FAILED,
            error=error,
        )
        return LeaseTransition.APPLIED

    def request_cancel_for_owner(self, *, job_id: UUID, owner_user_id: UUID) -> CancelRequestResult:
        """Request cancellation while hiding cross-owner job existence."""

        params = {"job_id": job_id, "owner_user_id": owner_user_id}
        row = self._session.execute(_LOCK_OWNER_JOB, params).mappings().one_or_none()
        if row is None:
            return CancelRequestResult.NOT_FOUND
        state = JobState(_mapping_str(row, "state"))
        if state.is_terminal:
            return CancelRequestResult.ALREADY_TERMINAL
        if state is JobState.RUNNING:
            updated = self._session.execute(_REQUEST_RUNNING_CANCEL, params).first()
            if updated is None:
                raise JobPersistenceInvariantError("locked running job was not cancellable")
            return CancelRequestResult.REQUESTED
        updated = self._session.execute(_CANCEL_PENDING_JOB, params).first()
        if updated is None:
            raise JobPersistenceInvariantError("locked pending job was not cancellable")
        return CancelRequestResult.CANCELLED

    def acknowledge_cancel(self, fence: LeaseFence) -> LeaseTransition:
        """Close the current attempt only when cancellation is durably requested."""

        cancel_requested = self._lock_active_lease(fence)
        if cancel_requested is None or not cancel_requested:
            return LeaseTransition.LOST_LEASE
        self._finish(
            fence,
            outcome=JobAttemptOutcome.CANCELLED,
            state=JobState.CANCELLED,
            error=None,
        )
        return LeaseTransition.CANCELLED

    def recover_expired(
        self,
        *,
        supported: Collection[JobKey],
        limit: int,
        policy: RetryPolicy,
    ) -> tuple[RecoveredJob, ...]:
        """Recover a bounded expired-lease batch exactly once under row locks."""

        _validate_limit(limit, maximum=_MAX_RECOVERY_BATCH, field="recovery limit")
        supported_keys = tuple(
            sorted(set(supported), key=lambda key: (key.job_type, key.schema_version))
        )
        if not supported_keys:
            return ()
        predicates: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        for index, key in enumerate(supported_keys):
            predicates.append(
                f"(job_type = :job_type_{index} AND schema_version = :schema_version_{index})"
            )
            params[f"job_type_{index}"] = key.job_type
            params[f"schema_version_{index}"] = key.schema_version
        rows = (
            self._session.execute(text(_lock_expired_sql(" OR ".join(predicates))), params)
            .mappings()
            .all()
        )
        recovered: list[RecoveredJob] = []
        for row in rows:
            job_id = _mapping_uuid(row, "job_id")
            attempt_no = _mapping_int(row, "attempt_count")
            worker_id = _mapping_str(row, "lease_owner")
            fence = LeaseFence(job_id, worker_id, attempt_no)
            cancel_requested = row["cancel_requested_at"] is not None
            if cancel_requested:
                state = JobState.CANCELLED
                closed = self._try_close_attempt(
                    fence,
                    outcome=JobAttemptOutcome.CANCELLED,
                    error_code=None,
                )
                error: JobError | None = None
            else:
                state = (
                    JobState.FAILED if attempt_no >= policy.max_attempts else JobState.RETRY_WAIT
                )
                closed = self._try_close_attempt(
                    fence,
                    outcome=JobAttemptOutcome.LEASE_EXPIRED,
                    error_code="job.lease_expired",
                )
                error = (
                    JobError(
                        "job.retry_exhausted",
                        {"last_error_code": "job.lease_expired", "retry_exhausted": True},
                    )
                    if state is JobState.FAILED
                    else JobError("job.lease_expired", {})
                )
            if not closed:
                state = JobState.FAILED
                error = JobError("job.persistence_invariant", {"missing_open_attempt": True})
            self._set_job_state(
                fence,
                state=state,
                error=error,
                retry_delay=(
                    policy.delay_for(job_id, attempt_no)
                    if state is JobState.RETRY_WAIT
                    else timedelta(0)
                ),
            )
            recovered.append(RecoveredJob(job_id, attempt_no, state))
        return tuple(recovered)

    def _lock_active_lease(self, fence: LeaseFence) -> bool | None:
        row = (
            self._session.execute(_LOCK_ACTIVE_LEASE, _fence_params(fence)).mappings().one_or_none()
        )
        if row is None:
            return None
        return row["cancel_requested_at"] is not None

    def _finish(
        self,
        fence: LeaseFence,
        *,
        outcome: JobAttemptOutcome,
        state: JobState,
        error: JobError | None,
        retry_delay: timedelta = timedelta(0),
        attempt_error_code: str | None = None,
    ) -> None:
        if not self._try_close_attempt(
            fence,
            outcome=outcome,
            error_code=attempt_error_code or (error.code if error is not None else None),
        ):
            raise JobPersistenceInvariantError("active job has no matching open attempt")
        self._set_job_state(
            fence,
            state=state,
            error=error,
            retry_delay=retry_delay,
        )

    def _try_close_attempt(
        self,
        fence: LeaseFence,
        *,
        outcome: JobAttemptOutcome,
        error_code: str | None,
    ) -> bool:
        row = self._session.execute(
            _CLOSE_ATTEMPT,
            {
                **_fence_params(fence),
                "outcome": outcome.value,
                "error_code": error_code,
                "metrics": {},
            },
        ).first()
        return row is not None

    def _set_job_state(
        self,
        fence: LeaseFence,
        *,
        state: JobState,
        error: JobError | None,
        retry_delay: timedelta,
    ) -> None:
        row = self._session.execute(
            _SET_JOB_STATE,
            {
                **_fence_params(fence),
                "state": state.value,
                "retry_delay": retry_delay,
                "error_code": error.code if error is not None else None,
                "error_detail": error.detail if error is not None else None,
            },
        ).first()
        if row is None:
            raise JobPersistenceInvariantError("locked job transition lost its lease fence")


def _claim_sql(supported_predicate: str) -> str:
    return f"""
    WITH candidate AS MATERIALIZED (
        SELECT j.job_id
        FROM jobs.job AS j
        WHERE j.state IN ('QUEUED', 'RETRY_WAIT')
          AND j.scheduled_at <= now()
          AND j.cancel_requested_at IS NULL
          AND ({supported_predicate})
          AND NOT EXISTS (
              SELECT 1
              FROM jobs.job_dependency AS dependency
              JOIN jobs.job AS prerequisite
                ON prerequisite.job_id = dependency.depends_on_job_id
              WHERE dependency.job_id = j.job_id
                AND (
                    (dependency.dependency_policy = 'REQUIRE_SUCCESS'
                     AND prerequisite.state <> 'COMPLETED')
                    OR
                    (dependency.dependency_policy = 'REQUIRE_TERMINAL'
                     AND prerequisite.state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED'))
                )
          )
        ORDER BY j.priority ASC, j.scheduled_at ASC, j.created_at ASC, j.job_id ASC
        FOR UPDATE OF j SKIP LOCKED
        LIMIT :limit
    ),
    claimed AS (
        UPDATE jobs.job AS j
        SET state = 'RUNNING',
            lease_owner = :worker_id,
            lease_deadline = now() + :lease_interval,
            heartbeat_at = now(),
            started_at = COALESCE(j.started_at, now()),
            attempt_count = j.attempt_count + 1,
            completed_at = NULL,
            error_code = NULL,
            error_detail = NULL
        FROM candidate
        WHERE j.job_id = candidate.job_id
        RETURNING j.job_id, j.job_type, j.schema_version, j.user_id, j.priority,
                  j.payload, j.checkpoint, j.attempt_count, j.lease_owner,
                  j.lease_deadline, j.cancel_requested_at, j.scheduled_at,
                  j.created_at
    ),
    attempts AS (
        INSERT INTO jobs.job_attempt (job_id, attempt_no, worker_id, started_at)
        SELECT job_id, attempt_count, lease_owner, now()
        FROM claimed
        RETURNING job_id, attempt_no
    )
    SELECT claimed.*
    FROM claimed
    JOIN attempts
      ON attempts.job_id = claimed.job_id
     AND attempts.attempt_no = claimed.attempt_count
    ORDER BY claimed.priority ASC, claimed.scheduled_at ASC,
             claimed.created_at ASC, claimed.job_id ASC
    """


def _lock_expired_sql(supported_predicate: str) -> str:
    return f"""
    SELECT job_id, attempt_count, lease_owner, cancel_requested_at
    FROM jobs.job
    WHERE state = 'RUNNING'
      AND lease_deadline <= now()
      AND ({supported_predicate})
    ORDER BY lease_deadline ASC, job_id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT :limit
    """


def _lease_from_row(row: RowMapping) -> JobLease:
    payload = _mapping_json_object(row, "payload")
    checkpoint_value = row["checkpoint"]
    checkpoint = (
        None if checkpoint_value is None else _as_json_object(checkpoint_value, "checkpoint")
    )
    return JobLease(
        fence=LeaseFence(
            _mapping_uuid(row, "job_id"),
            _mapping_str(row, "lease_owner"),
            _mapping_int(row, "attempt_count"),
        ),
        key=JobKey(
            _mapping_str(row, "job_type"),
            _mapping_int(row, "schema_version"),
        ),
        user_id=_mapping_optional_uuid(row, "user_id"),
        priority=_mapping_int(row, "priority"),
        payload=payload,
        checkpoint=checkpoint,
        lease_deadline=_mapping_datetime(row, "lease_deadline"),
        cancel_requested_at=_mapping_optional_datetime(row, "cancel_requested_at"),
    )


def _validate_enqueue(command: EnqueueJob) -> None:
    if not 0 <= command.priority <= 4:
        raise ValueError("priority must be between zero and four")
    if command.scheduled_at is not None and (
        command.scheduled_at.tzinfo is None or command.scheduled_at.utcoffset() is None
    ):
        raise ValueError("scheduled_at must be timezone-aware")
    if (command.idempotency_scope is None) != (command.idempotency_key is None):
        raise ValueError("idempotency scope and key must be supplied together")
    if command.idempotency_scope is not None:
        _validate_bounded_text(
            command.idempotency_scope,
            "idempotency_scope",
            _MAX_IDEMPOTENCY_SCOPE_LENGTH,
        )
        assert command.idempotency_key is not None
        _validate_bounded_text(
            command.idempotency_key,
            "idempotency_key",
            _MAX_IDEMPOTENCY_KEY_LENGTH,
        )


def _same_enqueue(stored: RowMapping, command: EnqueueJob, payload: dict[str, JsonValue]) -> bool:
    if (
        _mapping_str(stored, "job_type") != command.key.job_type
        or _mapping_int(stored, "schema_version") != command.key.schema_version
        or _mapping_optional_uuid(stored, "user_id") != command.user_id
        or _mapping_int(stored, "priority") != command.priority
        or not _same_json_document(_mapping_json_object(stored, "payload"), payload)
    ):
        return False
    stored_at = _mapping_datetime(stored, "scheduled_at")
    if command.scheduled_at is None:
        created_at = _mapping_datetime(stored, "created_at")
        return _same_instant(stored_at, created_at)
    return _same_instant(stored_at, command.scheduled_at)


def _same_instant(left: datetime, right: datetime) -> bool:
    return left.astimezone(UTC) == right.astimezone(UTC)


def _same_json_document(left: Mapping[str, JsonValue], right: Mapping[str, JsonValue]) -> bool:
    def encoded(value: Mapping[str, JsonValue]) -> str:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    return encoded(left) == encoded(right)


def _validate_worker_id(worker_id: str) -> None:
    _validate_bounded_text(worker_id, "worker_id", MAX_WORKER_ID_LENGTH)


def _validate_bounded_text(value: str, field: str, maximum: int) -> None:
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"{field} length must be between one and {maximum}")


def _validate_lease_interval(value: timedelta) -> None:
    if value < timedelta(seconds=1) or value > _MAX_LEASE_INTERVAL:
        raise ValueError("lease interval must be between one second and one hour")


def _validate_limit(value: int, *, maximum: int, field: str) -> None:
    if not 1 <= value <= maximum:
        raise ValueError(f"{field} must be between one and {maximum}")


def _validate_progress(current: int | None, total: int | None) -> None:
    if current is not None and current < 0:
        raise ValueError("progress_current must not be negative")
    if total is not None and total < 0:
        raise ValueError("progress_total must not be negative")
    if current is not None and total is not None and current > total:
        raise ValueError("progress_current must not exceed progress_total")


def _fence_params(fence: LeaseFence) -> dict[str, object]:
    return {
        "job_id": fence.job_id,
        "worker_id": fence.worker_id,
        "attempt_no": fence.attempt_no,
    }


def _as_json_object(value: object, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise JobPersistenceInvariantError(f"{field} is not a JSON object")
    return cast(dict[str, JsonValue], value)


def _mapping_json_object(row: RowMapping, field: str) -> dict[str, JsonValue]:
    return _as_json_object(row[field], field)


def _as_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, UUID):
        raise JobPersistenceInvariantError(f"{field} is not a UUID")
    return value


def _mapping_uuid(row: RowMapping, field: str) -> UUID:
    return _as_uuid(row[field], field)


def _mapping_optional_uuid(row: RowMapping, field: str) -> UUID | None:
    value = row[field]
    return None if value is None else _as_uuid(value, field)


def _mapping_str(row: RowMapping, field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise JobPersistenceInvariantError(f"{field} is not text")
    return value


def _mapping_int(row: RowMapping, field: str) -> int:
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobPersistenceInvariantError(f"{field} is not an integer")
    return value


def _mapping_datetime(row: RowMapping, field: str) -> datetime:
    value = row[field]
    if not isinstance(value, datetime):
        raise JobPersistenceInvariantError(f"{field} is not a timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise JobPersistenceInvariantError(f"{field} is not timezone-aware")
    return value


def _mapping_optional_datetime(row: RowMapping, field: str) -> datetime | None:
    value = row[field]
    if value is None:
        return None
    return _mapping_datetime(row, field)


__all__ = ("PostgresJobRepository",)
