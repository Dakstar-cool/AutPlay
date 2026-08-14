"""Fast tests for framework-independent durable-job contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from autplay.adapters.system import SystemClock, Uuid7Generator
from autplay.application.job_worker import (
    JobExecutionContext,
    JobHandlerRegistry,
    JobWorkerSettings,
)
from autplay.domain.jobs import (
    CheckpointSaved,
    JobCancellationRequested,
    JobDocumentError,
    JobKey,
    JobState,
    JsonValue,
    LeaseFence,
    RetryPolicy,
    validate_job_document,
)
from autplay.ports.jobs import JobRepository


def test_retry_backoff_is_deterministic_bounded_and_jittered() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay=timedelta(seconds=4),
        max_delay=timedelta(seconds=10),
        jitter_ratio=0.25,
    )
    job_id = UUID("018f3d34-7c00-7000-8000-000000000001")

    first = policy.delay_for(job_id, 1)
    assert first == policy.delay_for(job_id, 1)
    assert timedelta(seconds=3) <= first <= timedelta(seconds=4)
    assert timedelta(seconds=7.5) <= policy.delay_for(job_id, 20) <= timedelta(seconds=10)


@pytest.mark.parametrize(
    "policy",
    (
        RetryPolicy(max_attempts=1),
        RetryPolicy(base_delay=timedelta(seconds=1), max_delay=timedelta(seconds=1)),
    ),
)
def test_retry_policy_valid_boundaries(policy: RetryPolicy) -> None:
    assert policy.delay_for(UUID(int=1), 1) >= timedelta(0)


def test_job_documents_reject_secrets_non_finite_numbers_and_size_overflow() -> None:
    with pytest.raises(JobDocumentError, match="sensitive"):
        validate_job_document({"nested": {"refreshToken": "secret"}}, field="payload")
    with pytest.raises(JobDocumentError, match="non-finite"):
        validate_job_document({"score": float("nan")}, field="payload")
    with pytest.raises(JobDocumentError, match="exceeds"):
        validate_job_document({"value": "x" * 30}, field="payload", maximum_bytes=20)


def test_job_state_terminal_classification_is_closed() -> None:
    assert {state for state in JobState if state.is_terminal} == {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
    }


def test_system_clock_and_ids_satisfy_runtime_ports() -> None:
    now = SystemClock().now()
    generated = Uuid7Generator().new()

    assert isinstance(now, datetime)
    assert now.tzinfo is UTC
    assert generated.version == 7


def test_empty_registry_claims_no_feature_job_and_worker_bounds_are_checked() -> None:
    assert JobHandlerRegistry().supported == ()
    assert JobHandlerRegistry({}).supported == ()
    assert JobKey("test.noop", 1) == JobKey("test.noop", 1)
    with pytest.raises(ValueError, match="half"):
        JobWorkerSettings(
            lease_interval=timedelta(seconds=10),
            heartbeat_interval=timedelta(seconds=6),
        )


class _CheckpointRepository:
    def __init__(self, result: CheckpointSaved | None, events: list[str]) -> None:
        self.result = result
        self.events = events
        self.saved: tuple[LeaseFence, Mapping[str, JsonValue], int | None, int | None] | None = None

    def save_checkpoint(
        self,
        fence: LeaseFence,
        checkpoint: Mapping[str, JsonValue],
        *,
        progress_current: int | None,
        progress_total: int | None,
    ) -> CheckpointSaved | None:
        self.events.append("saved")
        self.saved = (fence, checkpoint, progress_current, progress_total)
        return self.result


class _CheckpointUnitOfWork:
    def __init__(self, repository: _CheckpointRepository, events: list[str]) -> None:
        self._repository = repository
        self._events = events

    @property
    def jobs(self) -> JobRepository:
        return cast(JobRepository, self._repository)

    def __enter__(self) -> _CheckpointUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self._events.append("committed")

    def rollback(self) -> None:
        self._events.append("rolled_back")


class _CheckpointUnitOfWorkFactory:
    def __init__(self, unit: _CheckpointUnitOfWork) -> None:
        self._unit = unit

    def __call__(self) -> _CheckpointUnitOfWork:
        return self._unit


def test_checkpoint_commits_then_raises_when_atomic_result_reports_cancel() -> None:
    events: list[str] = []
    cancel_requested_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    repository = _CheckpointRepository(CheckpointSaved(cancel_requested_at), events)
    unit = _CheckpointUnitOfWork(repository, events)
    factory = _CheckpointUnitOfWorkFactory(unit)
    fence = LeaseFence(UUID(int=1), "p03-unit-worker", 1)
    context = JobExecutionContext(
        uow_factory=factory,
        fence=fence,
        lease_interval=timedelta(minutes=1),
    )

    with pytest.raises(JobCancellationRequested, match=r"job\.cancel_requested"):
        context.checkpoint(
            {"cursor": 7},
            progress_current=7,
            progress_total=10,
        )

    assert events == ["saved", "committed"]
    assert repository.saved == (fence, {"cursor": 7}, 7, 10)
    with pytest.raises(JobCancellationRequested, match=r"job\.cancel_requested"):
        context.raise_if_cancelled()
