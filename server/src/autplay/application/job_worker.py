"""CPU worker orchestration over short, fenced job transactions."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from autplay.domain.jobs import (
    JobCancellationRequested,
    JobError,
    JobKey,
    JobLease,
    JsonValue,
    LeaseFence,
    LeaseTransition,
    RetryableJobError,
    RetryPolicy,
    TerminalJobError,
)
from autplay.ports.transactions import JobUnitOfWorkFactory


class JobHandler(Protocol):
    """Execute one versioned job with cooperative safe points."""

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        """Execute the job or raise one explicitly classified error."""

        ...


class JobLeaseLost(RuntimeError):
    """The current attempt may no longer mutate durable job state."""

    def __init__(self) -> None:
        super().__init__("job.lease_lost")


class WorkerOutcome(StrEnum):
    """Observable result of one bounded worker iteration."""

    IDLE = "IDLE"
    COMPLETED = "COMPLETED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LEASE_LOST = "LEASE_LOST"


@dataclass(frozen=True, slots=True)
class JobWorkerSettings:
    """Bounded CPU-worker scheduling and lease settings."""

    lease_interval: timedelta = timedelta(seconds=60)
    heartbeat_interval: timedelta = timedelta(seconds=20)
    idle_poll_interval: timedelta = timedelta(seconds=1)
    recovery_batch_size: int = 20
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not timedelta(seconds=2) <= self.lease_interval <= timedelta(hours=1):
            raise ValueError("lease_interval must be between two seconds and one hour")
        if not timedelta(milliseconds=100) <= self.heartbeat_interval:
            raise ValueError("heartbeat_interval must be at least 100 milliseconds")
        if self.heartbeat_interval > self.lease_interval / 2:
            raise ValueError("heartbeat_interval must not exceed half the lease interval")
        if not timedelta(milliseconds=10) <= self.idle_poll_interval <= timedelta(minutes=1):
            raise ValueError("idle_poll_interval must be between 10 ms and one minute")
        if not 1 <= self.recovery_batch_size <= 100:
            raise ValueError("recovery_batch_size must be between one and one hundred")


@dataclass(frozen=True, slots=True)
class WorkerTick:
    """Small result suitable for metrics without exposing job payloads."""

    outcome: WorkerOutcome
    recovered_count: int
    job_id: str | None = None


class JobHandlerRegistry:
    """Immutable mapping of explicitly supported job schema versions."""

    def __init__(self, handlers: Mapping[JobKey, JobHandler] | None = None) -> None:
        supplied = dict(handlers or {})
        self._handlers: Mapping[JobKey, JobHandler] = MappingProxyType(supplied)

    @property
    def supported(self) -> tuple[JobKey, ...]:
        """Return stable handler keys used to constrain PostgreSQL claim SQL."""

        return tuple(sorted(self._handlers, key=lambda key: (key.job_type, key.schema_version)))

    def handler_for(self, key: JobKey) -> JobHandler:
        """Return the already-registered handler for a claimed key."""

        try:
            return self._handlers[key]
        except KeyError as error:
            raise RuntimeError("claimed job has no registered handler") from error


class JobExecutionContext:
    """Cooperative heartbeat, checkpoint, and cancellation safe points."""

    def __init__(
        self,
        *,
        uow_factory: JobUnitOfWorkFactory,
        fence: LeaseFence,
        lease_interval: timedelta,
    ) -> None:
        self._uow_factory = uow_factory
        self._fence = fence
        self._lease_interval = lease_interval
        self._cancel_requested = threading.Event()
        self._lease_lost = threading.Event()

    @property
    def fence(self) -> LeaseFence:
        """Return the immutable lease fence for handler diagnostics."""

        return self._fence

    def heartbeat(self) -> None:
        """Renew the lease and observe its durable cancellation request."""

        with self._uow_factory() as unit:
            heartbeat = unit.jobs.heartbeat(self._fence, self._lease_interval)
            unit.commit()
        if heartbeat is None:
            self._lease_lost.set()
            raise JobLeaseLost
        if heartbeat.cancel_requested:
            self._cancel_requested.set()

    def checkpoint(
        self,
        value: Mapping[str, JsonValue],
        *,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> None:
        """Persist a checkpoint before an irreversible handler boundary."""

        self.raise_if_cancelled()
        with self._uow_factory() as unit:
            saved = unit.jobs.save_checkpoint(
                self._fence,
                value,
                progress_current=progress_current,
                progress_total=progress_total,
            )
            unit.commit()
        if saved is None:
            self._lease_lost.set()
            raise JobLeaseLost
        if saved.cancel_requested:
            self._cancel_requested.set()
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        """Stop work when the lease is lost or cancellation reaches a safe point."""

        if self._lease_lost.is_set():
            raise JobLeaseLost
        if self._cancel_requested.is_set():
            raise JobCancellationRequested


class JobWorker:
    """Single-concurrency CPU worker with a separate lease heartbeat."""

    def __init__(
        self,
        *,
        uow_factory: JobUnitOfWorkFactory,
        worker_id: str,
        registry: JobHandlerRegistry | None = None,
        settings: JobWorkerSettings | None = None,
    ) -> None:
        if not 1 <= len(worker_id) <= 300:
            raise ValueError("worker_id length must be between one and 300")
        self._uow_factory = uow_factory
        self._worker_id = worker_id
        self._registry = registry or JobHandlerRegistry()
        self._settings = settings or JobWorkerSettings()

    def run_once(self) -> WorkerTick:
        """Recover expired leases, then execute at most one supported job."""

        supported = self._registry.supported
        if not supported:
            return WorkerTick(WorkerOutcome.IDLE, 0)
        with self._uow_factory() as unit:
            recovered = unit.jobs.recover_expired(
                supported=supported,
                limit=self._settings.recovery_batch_size,
                policy=self._settings.retry_policy,
            )
            unit.commit()

        with self._uow_factory() as unit:
            leases = unit.jobs.claim(
                worker_id=self._worker_id,
                supported=supported,
                lease_interval=self._settings.lease_interval,
                limit=1,
            )
            unit.commit()
        if not leases:
            return WorkerTick(WorkerOutcome.IDLE, len(recovered))

        lease = leases[0]
        outcome = self._execute(lease, self._registry.handler_for(lease.key))
        return WorkerTick(outcome, len(recovered), str(lease.fence.job_id))

    @property
    def idle_poll_interval(self) -> timedelta:
        """Expose the configured bounded idle delay to process maintenance hooks."""
        return self._settings.idle_poll_interval

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run bounded iterations until a cooperative process stop is requested."""

        while not stop_event.is_set():
            tick = self.run_once()
            if tick.outcome is WorkerOutcome.IDLE:
                stop_event.wait(self._settings.idle_poll_interval.total_seconds())

    def _execute(self, lease: JobLease, handler: JobHandler) -> WorkerOutcome:
        context = JobExecutionContext(
            uow_factory=self._uow_factory,
            fence=lease.fence,
            lease_interval=self._settings.lease_interval,
        )
        monitor = _HeartbeatMonitor(
            context=context,
            interval=self._settings.heartbeat_interval,
        )
        try:
            with monitor:
                context.raise_if_cancelled()
                handler(context, lease)
                context.raise_if_cancelled()
            if monitor.failure is not None:
                return WorkerOutcome.LEASE_LOST
        except JobCancellationRequested:
            transition = self._acknowledge_cancel(lease.fence)
            return _outcome_for_transition(transition, applied=WorkerOutcome.CANCELLED)
        except JobLeaseLost:
            return WorkerOutcome.LEASE_LOST
        except RetryableJobError as error:
            transition = self._fail_retryable(lease.fence, error.error)
            return _outcome_for_transition(
                transition,
                applied=(
                    WorkerOutcome.FAILED
                    if lease.fence.attempt_no >= self._settings.retry_policy.max_attempts
                    else WorkerOutcome.RETRY_SCHEDULED
                ),
            )
        except TerminalJobError as error:
            transition = self._fail_terminal(lease.fence, error.error)
            return _outcome_for_transition(transition, applied=WorkerOutcome.FAILED)
        except Exception as error:
            # This is the process boundary. Unknown failures are deliberately not
            # retried; only their type is persisted, never the exception message.
            safe_error = JobError(
                "job.unhandled_error",
                {"exception_type": type(error).__name__[:200]},
            )
            transition = self._fail_terminal(lease.fence, safe_error)
            return _outcome_for_transition(transition, applied=WorkerOutcome.FAILED)

        transition = self._complete(lease.fence)
        return _outcome_for_transition(transition, applied=WorkerOutcome.COMPLETED)

    def _complete(self, fence: LeaseFence) -> LeaseTransition:
        with self._uow_factory() as unit:
            transition = unit.jobs.complete(fence)
            unit.commit()
        return transition

    def _fail_retryable(self, fence: LeaseFence, error: JobError) -> LeaseTransition:
        with self._uow_factory() as unit:
            transition = unit.jobs.fail_retryable(
                fence,
                error,
                self._settings.retry_policy,
            )
            unit.commit()
        return transition

    def _fail_terminal(self, fence: LeaseFence, error: JobError) -> LeaseTransition:
        with self._uow_factory() as unit:
            transition = unit.jobs.fail_terminal(fence, error)
            unit.commit()
        return transition

    def _acknowledge_cancel(self, fence: LeaseFence) -> LeaseTransition:
        with self._uow_factory() as unit:
            transition = unit.jobs.acknowledge_cancel(fence)
            unit.commit()
        return transition


class _HeartbeatMonitor:
    def __init__(self, *, context: JobExecutionContext, interval: timedelta) -> None:
        self._context = context
        self._interval_seconds = interval.total_seconds()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: Exception | None = None

    def __enter__(self) -> _HeartbeatMonitor:
        self._thread = threading.Thread(
            target=self._run,
            name="autplay-job-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 2))
            if self._thread.is_alive() and self.failure is None:
                self.failure = RuntimeError("job heartbeat thread did not stop")

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._context.heartbeat()
            except Exception as error:
                self.failure = error
                return


def _outcome_for_transition(
    transition: LeaseTransition, *, applied: WorkerOutcome
) -> WorkerOutcome:
    if transition is LeaseTransition.LOST_LEASE:
        return WorkerOutcome.LEASE_LOST
    if transition is LeaseTransition.CANCELLED:
        return WorkerOutcome.CANCELLED
    return applied


__all__ = (
    "JobExecutionContext",
    "JobHandler",
    "JobHandlerRegistry",
    "JobLeaseLost",
    "JobWorker",
    "JobWorkerSettings",
    "WorkerOutcome",
    "WorkerTick",
)
