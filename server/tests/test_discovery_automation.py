"""Credential-free worker evidence for bounded A1C release scans."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.application.discovery_automation import (
    DISCOVERY_SCAN_JOB,
    DiscoveryAutomationError,
    DiscoveryAutomationRepository,
    DiscoveryScanHandler,
    ScanTarget,
    normalize_run_error_code,
)
from autplay.application.job_worker import (
    JobExecutionContext,
    JobWorker,
    WorkerOutcome,
    WorkerTick,
)
from autplay.domain.discovery import (
    DiscoveryCandidate,
    DiscoveryError,
    ProviderTrackObservation,
    ProviderTrackPage,
)
from autplay.domain.jobs import JobLease, LeaseFence, TerminalJobError
from autplay.entrypoints.worker_cpu import discovery_scan_handlers, run_cpu_worker
from autplay.ports.discovery import ReleaseDiscoveryProvider

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


class _Context:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[dict[str, object], int, int]] = []

    def raise_if_cancelled(self) -> None:
        return None

    def checkpoint(
        self,
        value: dict[str, object],
        *,
        progress_current: int,
        progress_total: int,
    ) -> None:
        self.checkpoints.append((value, progress_current, progress_total))


class _Provider:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def release_tracks(self, provider_artist_id: str, *, offset: int) -> ProviderTrackPage:
        self.offsets.append(offset)
        track_ids = range(125, 100, -1) if offset == 0 else (100,)
        return ProviderTrackPage(
            provider_artist_id,
            offset,
            tuple(
                ProviderTrackObservation(
                    DiscoveryCandidate(
                        provider_track_id=str(track_id),
                        provider_artist_id=provider_artist_id,
                        title=f"Release {track_id}",
                        artist="Artist",
                        album=None,
                        duration_seconds=180,
                        license_url="https://creativecommons.org/licenses/by/4.0/",
                        share_url=f"https://www.jamendo.com/track/{track_id}",
                        acquisition_allowed=False,
                    ),
                    date(2026, 8, 26),
                    "UTC",
                )
                for track_id in track_ids
            ),
            25 if offset == 0 else None,
            f"release:{offset}",
        )


class _Repository:
    def __init__(self, owner_id: UUID, run_id: UUID, *, revoked: bool = False) -> None:
        self.owner_id = owner_id
        self.run_id = run_id
        self.revoked = revoked
        self.committed_offsets: list[int] = []
        self.completed = False
        self.failures: list[tuple[str, bool]] = []

    def claim_scan(self, **_: object) -> ScanTarget:
        return ScanTarget(self.run_id, self.owner_id, "20", 0, 0)

    def require_current(self, **_: object) -> None:
        if self.revoked:
            raise DiscoveryAutomationError("policy_revision_stale")

    def commit_page(self, *, page: ProviderTrackPage, **_: object) -> ScanTarget | None:
        self.committed_offsets.append(page.offset)
        return (
            ScanTarget(self.run_id, self.owner_id, "20", 25, 1)
            if page.next_offset is not None
            else None
        )

    def complete_scan(self, **_: object) -> None:
        self.completed = True

    def fail_scan(self, *, error_code: str, terminal: bool, **_: object) -> None:
        self.failures.append((error_code, terminal))


class _OneTickWorker:
    idle_poll_interval = timedelta(milliseconds=10)

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event

    def run_once(self) -> WorkerTick:
        self.stop_event.set()
        return WorkerTick(WorkerOutcome.IDLE, 0)


def _lease(owner_id: UUID, run_id: UUID, *, attempt_no: int = 1) -> JobLease:
    job_id = uuid4()
    return JobLease(
        LeaseFence(job_id, "a1c-worker-test", attempt_no),
        DISCOVERY_SCAN_JOB,
        owner_id,
        3,
        {"run_id": str(run_id)},
        None,
        NOW + timedelta(minutes=5),
        None,
    )


def test_scan_handler_commits_exactly_two_pages_and_registers_only_scan_key() -> None:
    owner_id, run_id = uuid4(), uuid4()
    repository = _Repository(owner_id, run_id)
    provider = _Provider()
    context = _Context()
    handler = DiscoveryScanHandler(
        cast(DiscoveryAutomationRepository, repository),
        cast(ReleaseDiscoveryProvider, provider),
        now=lambda: NOW,
    )

    assert discovery_scan_handlers(handler) == {DISCOVERY_SCAN_JOB: handler}
    handler(cast(JobExecutionContext, context), _lease(owner_id, run_id))

    assert provider.offsets == [0, 25]
    assert repository.committed_offsets == [0, 25]
    assert repository.completed
    assert context.checkpoints == [
        ({"page_count": 1, "stage": "PAGE_COMMITTED"}, 1, 2),
        ({"page_count": 2, "stage": "PAGE_COMMITTED"}, 2, 2),
    ]


def test_revoked_policy_blocks_provider_io_and_records_terminal_failure() -> None:
    owner_id, run_id = uuid4(), uuid4()
    repository = _Repository(owner_id, run_id, revoked=True)
    provider = _Provider()
    handler = DiscoveryScanHandler(
        cast(DiscoveryAutomationRepository, repository),
        cast(ReleaseDiscoveryProvider, provider),
        now=lambda: NOW,
    )

    with pytest.raises(TerminalJobError, match="policy_revision_stale"):
        handler(cast(JobExecutionContext, _Context()), _lease(owner_id, run_id))

    assert provider.offsets == []
    assert repository.failures == [("policy_revision_stale", True)]
    assert not repository.completed


def test_fifth_retryable_provider_failure_terminalizes_run_projection() -> None:
    owner_id, run_id = uuid4(), uuid4()
    repository = _Repository(owner_id, run_id)

    class _RetryingProvider:
        def release_tracks(self, provider_artist_id: str, *, offset: int) -> ProviderTrackPage:
            del provider_artist_id, offset
            raise DiscoveryError("provider_unavailable")

    handler = DiscoveryScanHandler(
        cast(DiscoveryAutomationRepository, repository),
        cast(ReleaseDiscoveryProvider, _RetryingProvider()),
        now=lambda: NOW,
    )

    with pytest.raises(TerminalJobError, match="discovery_adapter_unavailable"):
        handler(
            cast(JobExecutionContext, _Context()),
            _lease(owner_id, run_id, attempt_no=5),
        )

    assert repository.failures == [("discovery_adapter_unavailable", True)]
    assert not repository.completed


def test_run_failures_are_normalized_to_the_frozen_contract_vocabulary() -> None:
    assert normalize_run_error_code("job.unhandled_error") == "discovery_adapter_unavailable"
    assert normalize_run_error_code("job_attempts_exhausted") == "discovery_adapter_unavailable"
    assert normalize_run_error_code("discovery_response_too_large") == "provider_schema_invalid"
    assert normalize_run_error_code("provider_timeout") == "provider_timeout"


def test_stale_scan_fence_never_calls_provider_or_mutates_failure_projection() -> None:
    owner_id, run_id = uuid4(), uuid4()
    provider = _Provider()

    class _StaleRepository(_Repository):
        def require_current(self, **_: object) -> None:
            raise DiscoveryAutomationError("lease_fence_lost")

        def fail_scan(self, **_: object) -> None:
            raise DiscoveryAutomationError("lease_fence_lost")

    repository = _StaleRepository(owner_id, run_id)
    handler = DiscoveryScanHandler(
        cast(DiscoveryAutomationRepository, repository),
        cast(ReleaseDiscoveryProvider, provider),
        now=lambda: NOW,
    )

    with pytest.raises(TerminalJobError, match="lease_fence_lost"):
        handler(cast(JobExecutionContext, _Context()), _lease(owner_id, run_id))

    assert provider.offsets == []
    assert repository.failures == []
    assert not repository.completed


def test_cpu_loop_dispatches_automation_on_the_fixed_five_minute_schedule() -> None:
    stop_event = threading.Event()
    dispatches: list[int] = []

    def dispatch() -> int:
        dispatches.append(1)
        return 1

    run_cpu_worker(
        cast(JobWorker, _OneTickWorker(stop_event)),
        stop_event,
        discovery_dispatch=dispatch,
    )

    assert dispatches == [1]
    with pytest.raises(ValueError, match="exactly five minutes"):
        run_cpu_worker(
            cast(JobWorker, _OneTickWorker(threading.Event())),
            threading.Event(),
            discovery_dispatch_interval=timedelta(minutes=4),
        )
