"""A1C owner-scoped policy commands, scheduler dispatch, and release scans."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import rfc8785
from sqlalchemy.exc import SQLAlchemyError

from autplay.application.job_worker import JobExecutionContext
from autplay.domain.discovery import DiscoveryError, ProviderTrackPage
from autplay.domain.jobs import (
    JobKey,
    JobLease,
    JsonValue,
    LeaseFence,
    RetryableJobError,
    TerminalJobError,
)
from autplay.ports.discovery import ReleaseDiscoveryProvider

AUTO_IMPORT_CONFIRMATION = "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
DISCOVERY_SCAN_JOB = JobKey("discovery.scan", 1)
DISCOVERY_CONTRACT_VERSION = "release-discovery-v1"
_AUTOMATION_MAX_ATTEMPTS = 5
_STABLE_RUN_ERROR_CODES = frozenset(
    {
        "automation_not_active",
        "candidate_not_selectable",
        "discovery_adapter_unavailable",
        "discovery_not_eligible",
        "discovery_target_not_found",
        "identity_review_required",
        "lease_fence_lost",
        "operation_conflict",
        "policy_revision_stale",
        "provider_rate_limited",
        "provider_schema_invalid",
        "provider_timeout",
        "source_authorization_unavailable",
    }
)


class DiscoveryAutomationError(RuntimeError):
    """Stable owner-safe A1C failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DiscoveryAutomationActor(Protocol):
    """Minimal owner identity accepted from either Web or bearer authentication."""

    @property
    def user_id(self) -> UUID: ...


@dataclass(frozen=True, slots=True)
class PolicyMutation:
    """One idempotent compare-and-set policy command."""

    canonical_artist_id: UUID
    provider_artist_id: str
    discovery_mode: str
    import_mode: str
    automation_enabled: bool
    expected_revision: int | None
    operation_id: UUID
    confirmation_code: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.provider_artist_id) <= 20 or any(
            character < "0" or character > "9" for character in self.provider_artist_id
        ):
            raise ValueError("provider artist id is invalid")
        if self.discovery_mode not in {"DISABLED", "MANUAL_ONLY", "SCHEDULED"}:
            raise ValueError("discovery mode is invalid")
        if self.import_mode not in {"REVIEW_REQUIRED", "AUTO_IMPORT"}:
            raise ValueError("import mode is invalid")
        if self.expected_revision is not None and self.expected_revision < 1:
            raise ValueError("expected revision is invalid")
        if self.import_mode == "AUTO_IMPORT":
            if self.confirmation_code != AUTO_IMPORT_CONFIRMATION:
                raise ValueError("automatic import confirmation is invalid")
        elif self.confirmation_code is not None:
            raise ValueError("confirmation is not accepted for review-required mode")
        if self.automation_enabled != (self.discovery_mode == "SCHEDULED"):
            raise ValueError("automation flag and discovery mode are inconsistent")


@dataclass(frozen=True, slots=True)
class PolicyView:
    policy_id: UUID
    canonical_artist_id: UUID
    provider_artist_id: str
    discovery_mode: str
    import_mode: str
    automation_enabled: bool
    revision: int
    last_checked_at: datetime | None
    next_eligible_at: datetime | None


@dataclass(frozen=True, slots=True)
class PolicyMutationResult:
    policy: PolicyView
    replayed: bool


@dataclass(frozen=True, slots=True)
class DiscoveryRunView:
    run_id: UUID
    policy_id: UUID
    policy_revision: int
    state: str
    observed_count: int
    selected_count: int
    page_count: int
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ReleaseCandidateView:
    candidate_id: UUID
    run_id: UUID
    title: str
    artist: str
    album: str | None
    released_at: datetime | None
    disposition: str
    acquisition_state: str
    selected_automatically: bool


@dataclass(frozen=True, slots=True)
class CandidateActionResult:
    candidate_id: UUID
    disposition: str
    acquisition_state: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ScanTarget:
    run_id: UUID
    owner_user_id: UUID
    provider_artist_id: str
    next_offset: int
    page_count: int


class DiscoveryAutomationRepository(Protocol):
    """Short-transaction storage boundary for A1C."""

    def set_policy(
        self, *, owner_user_id: UUID, command: PolicyMutation, request_sha256: bytes, now: datetime
    ) -> PolicyMutationResult: ...

    def list_policies(self, *, owner_user_id: UUID, limit: int) -> tuple[PolicyView, ...]: ...

    def list_runs(self, *, owner_user_id: UUID, limit: int) -> tuple[DiscoveryRunView, ...]: ...

    def list_candidates(
        self, *, owner_user_id: UUID, run_id: UUID, limit: int
    ) -> tuple[ReleaseCandidateView, ...]: ...

    def run_now(
        self,
        *,
        owner_user_id: UUID,
        policy_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> DiscoveryRunView: ...

    def act_on_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> CandidateActionResult: ...

    def dispatch_due(self, *, now: datetime, limit: int) -> int: ...

    def claim_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> ScanTarget | None: ...

    def require_current(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        boundary: str,
    ) -> None: ...

    def commit_page(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        page: ProviderTrackPage,
        now: datetime,
    ) -> ScanTarget | None: ...

    def complete_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> None: ...

    def fail_scan(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        error_code: str,
        terminal: bool,
        now: datetime,
    ) -> None: ...

    def cleanup_expired(self, *, now: datetime, limit: int) -> int: ...


class DiscoveryAutomationService:
    """Owner-bound commands and bounded scheduler dispatch."""

    def __init__(self, repository: DiscoveryAutomationRepository) -> None:
        self._repository = repository

    def set_policy(
        self, actor: DiscoveryAutomationActor, command: PolicyMutation, *, now: datetime
    ) -> PolicyMutationResult:
        return self._repository.set_policy(
            owner_user_id=actor.user_id,
            command=command,
            request_sha256=_policy_hash(command),
            now=now,
        )

    def policies(
        self, actor: DiscoveryAutomationActor, *, limit: int = 100
    ) -> tuple[PolicyView, ...]:
        _require_limit(limit, maximum=100)
        return self._repository.list_policies(owner_user_id=actor.user_id, limit=limit)

    def runs(
        self, actor: DiscoveryAutomationActor, *, limit: int = 50
    ) -> tuple[DiscoveryRunView, ...]:
        _require_limit(limit, maximum=100)
        return self._repository.list_runs(owner_user_id=actor.user_id, limit=limit)

    def candidates(
        self, actor: DiscoveryAutomationActor, run_id: UUID, *, limit: int = 50
    ) -> tuple[ReleaseCandidateView, ...]:
        _require_limit(limit, maximum=50)
        return self._repository.list_candidates(
            owner_user_id=actor.user_id, run_id=run_id, limit=limit
        )

    def run_now(
        self,
        actor: DiscoveryAutomationActor,
        policy_id: UUID,
        operation_id: UUID,
        *,
        now: datetime,
    ) -> DiscoveryRunView:
        payload: dict[str, JsonValue] = {
            "action": "START_DISCOVERY",
            "contract_version": DISCOVERY_CONTRACT_VERSION,
            "operation_id": str(operation_id),
            "policy_id": str(policy_id),
            "schema_version": 1,
        }
        return self._repository.run_now(
            owner_user_id=actor.user_id,
            policy_id=policy_id,
            operation_id=operation_id,
            request_sha256=hashlib.sha256(rfc8785.dumps(payload)).digest(),
            now=now,
        )

    def select_candidate(
        self,
        actor: DiscoveryAutomationActor,
        candidate_id: UUID,
        operation_id: UUID,
        *,
        now: datetime,
    ) -> CandidateActionResult:
        return self._candidate_action(actor, candidate_id, operation_id, action="SELECT", now=now)

    def retry_candidate(
        self,
        actor: DiscoveryAutomationActor,
        candidate_id: UUID,
        operation_id: UUID,
        *,
        now: datetime,
    ) -> CandidateActionResult:
        return self._candidate_action(actor, candidate_id, operation_id, action="RETRY", now=now)

    def ignore_candidate(
        self,
        actor: DiscoveryAutomationActor,
        candidate_id: UUID,
        operation_id: UUID,
        *,
        now: datetime,
    ) -> CandidateActionResult:
        return self._candidate_action(actor, candidate_id, operation_id, action="IGNORE", now=now)

    def _candidate_action(
        self,
        actor: DiscoveryAutomationActor,
        candidate_id: UUID,
        operation_id: UUID,
        *,
        action: str,
        now: datetime,
    ) -> CandidateActionResult:
        payload: dict[str, JsonValue] = {
            "action": f"{action}_CANDIDATE",
            "candidate_id": str(candidate_id),
            "contract_version": DISCOVERY_CONTRACT_VERSION,
            "operation_id": str(operation_id),
            "schema_version": 1,
        }
        return self._repository.act_on_candidate(
            owner_user_id=actor.user_id,
            candidate_id=candidate_id,
            action=action,
            operation_id=operation_id,
            request_sha256=hashlib.sha256(rfc8785.dumps(payload)).digest(),
            now=now,
        )

    def dispatch_due(self, *, now: datetime, limit: int = 20) -> int:
        if not 1 <= limit <= 20:
            raise ValueError("scheduler claim limit is invalid")
        return self._repository.dispatch_due(now=now, limit=limit)

    def cleanup_expired(self, *, now: datetime, limit: int = 10_000) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("automation cleanup limit is invalid")
        return self._repository.cleanup_expired(now=now, limit=limit)


class DiscoveryScanHandler:
    """Read at most two release pages and commit each page atomically."""

    def __init__(
        self,
        repository: DiscoveryAutomationRepository,
        provider: ReleaseDiscoveryProvider,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._now = now

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        run_id = _run_id(lease)
        owner_id = lease.user_id
        if owner_id is None:
            raise TerminalJobError("discovery.invalid_job_payload")
        try:
            target = self._repository.claim_scan(
                run_id=run_id,
                owner_user_id=owner_id,
                fence=lease.fence,
                now=self._now(),
            )
            while target is not None and target.page_count < 2:
                context.raise_if_cancelled()
                self._repository.require_current(
                    run_id=run_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                    boundary="BEFORE_PROVIDER_IO",
                )
                page = self._provider.release_tracks(
                    target.provider_artist_id, offset=target.next_offset
                )
                context.raise_if_cancelled()
                target = self._repository.commit_page(
                    run_id=run_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                    page=page,
                    now=self._now(),
                )
                committed_pages = page.offset // 25 + 1
                context.checkpoint(
                    {
                        "page_count": committed_pages,
                        "stage": "PAGE_COMMITTED",
                    },
                    progress_current=committed_pages,
                    progress_total=2,
                )
                if page.next_offset is None:
                    break
            self._repository.complete_scan(
                run_id=run_id,
                owner_user_id=owner_id,
                fence=lease.fence,
                now=self._now(),
            )
        except DiscoveryAutomationError as error:
            code = normalize_run_error_code(error.code)
            self._record_failure(run_id, owner_id, lease, code, terminal=True)
            raise TerminalJobError(code) from None
        except DiscoveryError as error:
            code = normalize_run_error_code(error.code)
            terminal = code == "provider_schema_invalid"
            if lease.fence.attempt_no >= _AUTOMATION_MAX_ATTEMPTS:
                terminal = True
            self._record_failure(run_id, owner_id, lease, code, terminal=terminal)
            if terminal:
                raise TerminalJobError(code) from None
            raise RetryableJobError(code) from None
        except SQLAlchemyError as error:
            raise RetryableJobError("database_unavailable") from error

    def _record_failure(
        self, run_id: UUID, owner_id: UUID, lease: JobLease, code: str, *, terminal: bool
    ) -> None:
        try:
            self._repository.fail_scan(
                run_id=run_id,
                owner_user_id=owner_id,
                fence=lease.fence,
                error_code=code,
                terminal=terminal,
                now=self._now(),
            )
        except DiscoveryAutomationError as error:
            if error.code != "lease_fence_lost":
                raise


def _policy_hash(command: PolicyMutation) -> bytes:
    payload: dict[str, JsonValue] = {
        "action": "SET_ARTIST_POLICY",
        "automation_enabled": command.automation_enabled,
        "canonical_artist_id": str(command.canonical_artist_id),
        "consequence_confirmation": command.confirmation_code,
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "discovery_mode": command.discovery_mode,
        "expected_policy_revision": command.expected_revision,
        "import_mode": command.import_mode,
        "operation_id": str(command.operation_id),
        "provider_artist_id": command.provider_artist_id,
        "schema_version": 1,
    }
    return hashlib.sha256(rfc8785.dumps(payload)).digest()


def normalize_run_error_code(code: str) -> str:
    """Map internal/provider failures into the frozen owner-visible A1A vocabulary."""

    if code in _STABLE_RUN_ERROR_CODES:
        return code
    if code in {
        "discovery_content_invalid",
        "discovery_limit_invalid",
        "discovery_provider_response_invalid",
        "discovery_query_invalid",
        "discovery_response_too_large",
    }:
        return "provider_schema_invalid"
    if code in {"discovery_policy_not_found", "discovery_run_not_found"}:
        return "discovery_not_eligible"
    return "discovery_adapter_unavailable"


def _require_limit(limit: int, *, maximum: int) -> None:
    if not 1 <= limit <= maximum:
        raise ValueError("projection limit is invalid")


def _run_id(lease: JobLease) -> UUID:
    raw = lease.payload.get("run_id")
    if not isinstance(raw, str):
        raise TerminalJobError("discovery.invalid_job_payload")
    try:
        return UUID(raw)
    except ValueError as error:
        raise TerminalJobError("discovery.invalid_job_payload") from error


__all__ = (
    "AUTO_IMPORT_CONFIRMATION",
    "DISCOVERY_CONTRACT_VERSION",
    "DISCOVERY_SCAN_JOB",
    "CandidateActionResult",
    "DiscoveryAutomationActor",
    "DiscoveryAutomationError",
    "DiscoveryAutomationRepository",
    "DiscoveryAutomationService",
    "DiscoveryRunView",
    "DiscoveryScanHandler",
    "PolicyMutation",
    "PolicyMutationResult",
    "PolicyView",
    "ReleaseCandidateView",
    "ScanTarget",
    "normalize_run_error_code",
)
