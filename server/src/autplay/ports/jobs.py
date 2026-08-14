"""Application-facing repository boundary for durable jobs."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from autplay.domain.jobs import (
    CancelRequestResult,
    CheckpointSaved,
    Heartbeat,
    JobError,
    JobKey,
    JobLease,
    JobState,
    JsonValue,
    LeaseFence,
    LeaseTransition,
    RetryPolicy,
)


@dataclass(frozen=True, slots=True)
class EnqueueJob:
    """Validated input for one durable job enqueue operation."""

    key: JobKey
    user_id: UUID | None
    payload: Mapping[str, JsonValue]
    priority: int = 3
    scheduled_at: datetime | None = None
    idempotency_scope: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Identity and replay status of an enqueue command."""

    job_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class RecoveredJob:
    """One expired lease moved to a safe non-running state."""

    job_id: UUID
    attempt_no: int
    state: JobState


class JobIdempotencyConflict(RuntimeError):
    """An enqueue idempotency key was reused for different input."""

    def __init__(self) -> None:
        super().__init__("job.idempotency_conflict")


class JobRepository(Protocol):
    """Short-transaction persistence operations for durable jobs."""

    def enqueue(self, command: EnqueueJob) -> EnqueueResult:
        """Insert or safely replay one idempotent enqueue command."""

        ...

    def claim(
        self,
        *,
        worker_id: str,
        supported: Collection[JobKey],
        lease_interval: timedelta,
        limit: int,
    ) -> tuple[JobLease, ...]:
        """Claim due supported jobs and create matching attempts atomically."""

        ...

    def heartbeat(self, fence: LeaseFence, lease_interval: timedelta) -> Heartbeat | None:
        """Renew an unexpired current lease, or return ``None`` if it was lost."""

        ...

    def save_checkpoint(
        self,
        fence: LeaseFence,
        checkpoint: Mapping[str, JsonValue],
        *,
        progress_current: int | None,
        progress_total: int | None,
    ) -> CheckpointSaved | None:
        """Persist progress and return cancellation state, or ``None`` if lease was lost."""

        ...

    def complete(self, fence: LeaseFence) -> LeaseTransition:
        """Finish the current lease successfully or honor pending cancellation."""

        ...

    def fail_retryable(
        self,
        fence: LeaseFence,
        error: JobError,
        policy: RetryPolicy,
    ) -> LeaseTransition:
        """Schedule bounded retry or fail after the final permitted attempt."""

        ...

    def fail_terminal(self, fence: LeaseFence, error: JobError) -> LeaseTransition:
        """Finish the current lease with a non-retryable failure."""

        ...

    def request_cancel_for_owner(self, *, job_id: UUID, owner_user_id: UUID) -> CancelRequestResult:
        """Request cancellation without revealing another owner's job."""

        ...

    def acknowledge_cancel(self, fence: LeaseFence) -> LeaseTransition:
        """Finish a running attempt at a safe cancellation point."""

        ...

    def recover_expired(
        self,
        *,
        supported: Collection[JobKey],
        limit: int,
        policy: RetryPolicy,
    ) -> tuple[RecoveredJob, ...]:
        """Recover expired supported leases under queue-style row locking."""

        ...


__all__ = (
    "EnqueueJob",
    "EnqueueResult",
    "JobIdempotencyConflict",
    "JobRepository",
    "RecoveredJob",
)
