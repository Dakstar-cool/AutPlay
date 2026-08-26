"""PostgreSQL persistence for bounded, owner-scoped A1C release discovery."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.discovery_automation import (
    CandidateActionResult,
    DiscoveryAutomationError,
    DiscoveryRunView,
    PolicyMutation,
    PolicyMutationResult,
    PolicyView,
    ReleaseCandidateView,
    ScanTarget,
    normalize_run_error_code,
)
from autplay.domain.discovery import DiscoveryCandidate, ProviderTrackPage
from autplay.domain.jobs import JobKey, JsonValue, LeaseFence
from autplay.ports.jobs import EnqueueJob

from .discovery_runtime import (
    JAMENDO_PROVIDER_ID,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from .jobs_runtime import PostgresJobRepository
from .models import (
    AcquisitionAttemptRow,
    ArtistCreditNameRow,
    ArtistPolicyRevisionRow,
    ArtistPolicyRow,
    ArtistRow,
    CandidateActionReceiptRow,
    DiscoveryCandidateRow,
    DiscoveryRunCandidateRow,
    DiscoveryRunPageRow,
    DiscoveryRunRow,
    JobRow,
    LibraryEntryRow,
    RecordingRow,
    SourceAuthorizationRow,
    SourceProviderRow,
    UserTrackRefRow,
)

DISCOVERY_SCAN_JOB = JobKey("discovery.scan", 1)
DISCOVERY_ACQUIRE_JOB = JobKey("discovery.acquire", 1)
_CADENCE = timedelta(hours=24)
_RELEASE_WATCH = {"SEARCH", "DOWNLOAD", "RELEASE_WATCH"}


class PostgresDiscoveryAutomationRepository:
    """Run A1C state transitions in the caller-owned short transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def set_policy(
        self, *, owner_user_id: UUID, command: PolicyMutation, request_sha256: bytes, now: datetime
    ) -> PolicyMutationResult:
        self._lock_operation(owner_user_id, command.operation_id)
        lock_scope = b"a1c-policy-operation-v1" + owner_user_id.bytes
        lock_key = int.from_bytes(hashlib.sha256(lock_scope).digest()[:8], "big", signed=True)
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        replay = self._session.scalar(
            select(ArtistPolicyRevisionRow).where(
                ArtistPolicyRevisionRow.owner_user_id == owner_user_id,
                ArtistPolicyRevisionRow.operation_id == command.operation_id,
            )
        )
        if replay is not None:
            if replay.request_sha256 is None or not hmac.compare_digest(
                replay.request_sha256, request_sha256
            ):
                raise DiscoveryAutomationError("operation_conflict")
            replay_policy = self._session.get(ArtistPolicyRow, replay.policy_id)
            if replay_policy is None:
                raise DiscoveryAutomationError("discovery_policy_not_found")
            return PolicyMutationResult(_policy_revision_view(replay_policy, replay), replayed=True)
        self._require_operation_available(owner_user_id, command.operation_id, action="POLICY")
        policy = self._session.scalar(
            select(ArtistPolicyRow)
            .where(
                ArtistPolicyRow.user_id == owner_user_id,
                ArtistPolicyRow.canonical_artist_id == command.canonical_artist_id,
            )
            .with_for_update()
        )
        advances_existing_policy = policy is not None
        if policy is not None:
            if command.expected_revision != policy.current_revision:
                raise DiscoveryAutomationError("policy_revision_stale")
            revision = policy.current_revision + 1
        else:
            if command.expected_revision is not None:
                raise DiscoveryAutomationError("policy_revision_stale")
            self._require_provider()
            self._require_artist(owner_user_id, command.canonical_artist_id)
            self._require_provider_artist_binding(
                owner_user_id, command.canonical_artist_id, command.provider_artist_id
            )
            policy = ArtistPolicyRow(
                policy_id=uuid5(
                    NAMESPACE_URL,
                    f"autplay:a1c-policy-v1:{owner_user_id}:{command.canonical_artist_id}",
                ),
                user_id=owner_user_id,
                canonical_artist_id=command.canonical_artist_id,
                provider_id=JAMENDO_PROVIDER_ID,
                provider_artist_id=command.provider_artist_id,
                discovery_mode=command.discovery_mode,
                import_mode=command.import_mode,
                automation_enabled=command.automation_enabled,
                current_revision=1,
                next_eligible_at=now if command.discovery_mode == "SCHEDULED" else None,
                row_version=1,
            )
            self._session.add(policy)
            # The append-only revision references the newly created policy.  Flush the
            # parent explicitly because the ORM models intentionally do not expose a
            # relationship that SQLAlchemy could use to order these independent rows.
            self._session.flush([policy])
            revision = 1
        if policy.provider_artist_id != command.provider_artist_id:
            raise DiscoveryAutomationError("policy_provider_binding_conflict")
        if advances_existing_policy and self._increases_policy_authority(policy, command):
            self._require_provider()
            self._require_artist(owner_user_id, command.canonical_artist_id)
            self._require_provider_artist_binding(
                owner_user_id, command.canonical_artist_id, command.provider_artist_id
            )
        previous_discovery_mode = policy.discovery_mode
        previous_next_eligible_at = policy.next_eligible_at
        policy.discovery_mode = command.discovery_mode
        policy.import_mode = command.import_mode
        policy.automation_enabled = command.automation_enabled
        policy.current_revision = revision
        policy.next_eligible_at = (
            previous_next_eligible_at
            if command.discovery_mode == "SCHEDULED"
            and previous_discovery_mode == "SCHEDULED"
            and previous_next_eligible_at is not None
            else now
            if command.discovery_mode == "SCHEDULED"
            else None
        )
        policy.updated_at = now
        policy.row_version += 1
        self._session.add(
            ArtistPolicyRevisionRow(
                policy_id=policy.policy_id,
                owner_user_id=owner_user_id,
                revision=revision,
                discovery_mode=command.discovery_mode,
                import_mode=command.import_mode,
                automation_enabled=command.automation_enabled,
                change_kind=(
                    "DISABLED" if command.discovery_mode == "DISABLED" else "OWNER_CONFIRMED"
                ),
                confirmation_code=command.confirmation_code,
                operation_id=command.operation_id,
                request_sha256=request_sha256,
                last_checked_at=policy.last_checked_at,
                next_eligible_at=policy.next_eligible_at,
            )
        )
        if advances_existing_policy:
            self._cancel_auto_work(policy.policy_id, now)
        self._session.flush()
        return PolicyMutationResult(_policy_view(policy), replayed=False)

    def list_policies(self, *, owner_user_id: UUID, limit: int) -> tuple[PolicyView, ...]:
        return tuple(
            _policy_view(row)
            for row in self._session.scalars(
                select(ArtistPolicyRow)
                .where(ArtistPolicyRow.user_id == owner_user_id)
                .order_by(ArtistPolicyRow.updated_at.desc(), ArtistPolicyRow.policy_id)
                .limit(limit)
            )
        )

    def list_runs(self, *, owner_user_id: UUID, limit: int) -> tuple[DiscoveryRunView, ...]:
        return tuple(
            _run_view(row)
            for row in self._session.scalars(
                select(DiscoveryRunRow)
                .where(DiscoveryRunRow.user_id == owner_user_id)
                .order_by(DiscoveryRunRow.created_at.desc(), DiscoveryRunRow.run_id)
                .limit(limit)
            )
        )

    def list_candidates(
        self, *, owner_user_id: UUID, run_id: UUID, limit: int
    ) -> tuple[ReleaseCandidateView, ...]:
        rows = self._session.execute(
            select(DiscoveryCandidateRow, DiscoveryRunCandidateRow.selected_at)
            .join(
                DiscoveryRunCandidateRow,
                DiscoveryRunCandidateRow.candidate_id == DiscoveryCandidateRow.candidate_id,
            )
            .join(DiscoveryRunRow, DiscoveryRunRow.run_id == DiscoveryRunCandidateRow.run_id)
            .where(DiscoveryRunRow.run_id == run_id, DiscoveryRunRow.user_id == owner_user_id)
            .order_by(
                DiscoveryCandidateRow.released_at.desc().nullslast(),
                DiscoveryCandidateRow.candidate_id,
            )
            .limit(limit)
        )
        return tuple(
            _candidate_view(candidate, run_id, selected_at is not None)
            for candidate, selected_at in rows
        )

    def run_now(
        self,
        *,
        owner_user_id: UUID,
        policy_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> DiscoveryRunView:
        self._lock_operation(owner_user_id, operation_id)
        policy = self._session.scalar(
            select(ArtistPolicyRow)
            .where(ArtistPolicyRow.policy_id == policy_id, ArtistPolicyRow.user_id == owner_user_id)
            .with_for_update()
        )
        if policy is None:
            raise DiscoveryAutomationError("discovery_policy_not_found")
        existing = self._session.scalar(
            select(DiscoveryRunRow).where(
                DiscoveryRunRow.user_id == owner_user_id,
                DiscoveryRunRow.operation_id == operation_id,
            )
        )
        if existing is not None:
            if existing.request_sha256 is None or not hmac.compare_digest(
                existing.request_sha256, request_sha256
            ):
                raise DiscoveryAutomationError("operation_conflict")
            return _run_view(existing)
        self._require_operation_available(owner_user_id, operation_id, action="RUN")
        self._require_current_policy(policy, now)
        if policy.next_eligible_at is None or policy.next_eligible_at > now:
            raise DiscoveryAutomationError("automation_not_active")
        run = self._create_run(policy, now, operation_id, request_sha256)
        policy.last_checked_at = now
        policy.next_eligible_at = now + _CADENCE
        policy.updated_at = now
        policy.row_version += 1
        self._session.flush()
        return _run_view(run)

    def act_on_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> CandidateActionResult:
        if action not in {"SELECT", "RETRY", "IGNORE"}:
            raise DiscoveryAutomationError("candidate_action_invalid")
        self._lock_operation(owner_user_id, operation_id)
        receipt = self._session.scalar(
            select(CandidateActionReceiptRow).where(
                CandidateActionReceiptRow.user_id == owner_user_id,
                CandidateActionReceiptRow.operation_id == operation_id,
            )
        )
        if receipt is not None:
            if not hmac.compare_digest(receipt.request_sha256, request_sha256):
                raise DiscoveryAutomationError("operation_conflict")
            return CandidateActionResult(
                receipt.candidate_id,
                receipt.result_disposition,
                receipt.result_acquisition_state,
                True,
            )
        self._require_operation_available(owner_user_id, operation_id, action="CANDIDATE")
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.candidate_id == candidate_id,
                DiscoveryCandidateRow.user_id == owner_user_id,
            )
            .with_for_update()
        )
        if candidate is None:
            raise DiscoveryAutomationError("discovery_candidate_not_found")
        if action == "IGNORE":
            if candidate.disposition not in {
                "SELECTABLE",
                "SELECTED",
            } or candidate.acquisition_state not in {
                "NOT_REQUESTED",
                "FAILED_TERMINAL",
                "CANCELLED",
            }:
                raise DiscoveryAutomationError("candidate_action_invalid")
            candidate.disposition = "IGNORED"
            candidate.updated_at = now
            candidate.row_version += 1
        else:
            try:
                candidate = PostgresBulkDiscoveryRepository(
                    self._session
                ).enqueue_explicit_manual_candidate(
                    owner_user_id=owner_user_id,
                    candidate_id=candidate_id,
                    operation_id=operation_id,
                    action=action,
                    now=now,
                )
            except BulkDiscoveryError as error:
                raise DiscoveryAutomationError(error.code) from None
        result = _candidate_action_result(candidate, replayed=False)
        self._session.add(
            CandidateActionReceiptRow(
                user_id=owner_user_id,
                candidate_id=candidate_id,
                action=action,
                operation_id=operation_id,
                request_sha256=request_sha256,
                result_disposition=result.disposition,
                result_acquisition_state=result.acquisition_state,
            )
        )
        self._session.flush()
        return result

    def dispatch_due(self, *, now: datetime, limit: int) -> int:
        self._reconcile_terminal_jobs(now)
        policies = tuple(
            self._session.scalars(
                select(ArtistPolicyRow)
                .where(
                    ArtistPolicyRow.automation_enabled.is_(True),
                    ArtistPolicyRow.discovery_mode == "SCHEDULED",
                    ArtistPolicyRow.next_eligible_at <= now,
                )
                .order_by(ArtistPolicyRow.next_eligible_at, ArtistPolicyRow.policy_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        dispatched = 0
        for policy in policies:
            try:
                self._require_current_policy(policy, now)
            except DiscoveryAutomationError as error:
                if error.code not in {
                    "automation_not_active",
                    "discovery_target_not_found",
                    "source_authorization_unavailable",
                }:
                    raise
                self._cancel_auto_work(policy.policy_id, now)
                policy.next_eligible_at = now + _CADENCE
                policy.updated_at = now
                policy.row_version += 1
                continue
            active = self._session.scalar(
                select(DiscoveryRunRow.run_id).where(
                    DiscoveryRunRow.policy_id == policy.policy_id,
                    DiscoveryRunRow.state.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
                )
            )
            if active is not None:
                continue
            self._create_run(policy, now, None, None)
            policy.last_checked_at = now
            policy.next_eligible_at = now + _CADENCE
            policy.updated_at = now
            policy.row_version += 1
            dispatched += 1
        self._session.flush()
        return dispatched

    def _reconcile_terminal_jobs(self, now: datetime) -> None:
        """Close stale run projections after the generic worker exhausts or terminalizes a job."""

        rows = self._session.execute(
            select(DiscoveryRunRow, JobRow)
            .join(JobRow, JobRow.job_id == DiscoveryRunRow.job_id)
            .where(
                DiscoveryRunRow.state.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
                JobRow.state.in_(("FAILED", "CANCELLED")),
            )
            .with_for_update(of=(DiscoveryRunRow, JobRow), skip_locked=True)
        )
        for run, job in rows:
            run.state = "PARTIAL" if run.page_count else "FAILED_TERMINAL"
            run.error_code = normalize_run_error_code(job.error_code or "job_attempts_exhausted")
            run.completed_at = now
            run.updated_at = now
            run.row_version += 1

    def claim_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> ScanTarget | None:
        self._require_fence(fence, owner_user_id)
        run = self._locked_run(run_id, owner_user_id)
        if run is None or run.job_id != fence.job_id:
            raise DiscoveryAutomationError("discovery_run_not_found")
        if run.state in {"COMPLETED", "PARTIAL", "FAILED_TERMINAL", "CANCELLED"}:
            return None
        self._require_run_current(run, now)
        run.state = "RUNNING"
        run.started_at = run.started_at or now
        run.updated_at = now
        run.row_version += 1
        last_page = self._session.scalar(
            select(DiscoveryRunPageRow)
            .where(DiscoveryRunPageRow.run_id == run.run_id)
            .order_by(DiscoveryRunPageRow.ordinal.desc())
            .limit(1)
        )
        return _next_target(run, 0 if last_page is None else last_page.next_offset)

    def require_current(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        boundary: str,
    ) -> None:
        if boundary not in {
            "BEFORE_PROVIDER_IO",
            "PAGE_COMMIT",
            "AUTO_SELECTION",
            "ACQUISITION_CLAIM",
        }:
            raise DiscoveryAutomationError("discovery_boundary_invalid")
        self._require_fence(fence, owner_user_id)
        run = self._locked_run(run_id, owner_user_id)
        if run is None:
            raise DiscoveryAutomationError("discovery_run_not_found")
        self._require_run_current(run, datetime.now(UTC))

    def commit_page(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        page: ProviderTrackPage,
        now: datetime,
    ) -> ScanTarget | None:
        self._require_fence(fence, owner_user_id)
        run = self._locked_run(run_id, owner_user_id)
        if (
            run is None
            or run.job_id != fence.job_id
            or page.provider_artist_id != run.provider_artist_id
        ):
            raise DiscoveryAutomationError("discovery_run_not_found")
        self._require_run_current(run, now)
        digest = hashlib.sha256(rfc8785.dumps(_page_document(page, run))).digest()
        if page.offset not in {0, 25}:
            raise DiscoveryAutomationError("discovery_page_conflict")
        page_ordinal = page.offset // 25
        existing = self._session.get(DiscoveryRunPageRow, (run_id, page_ordinal))
        if existing is not None:
            if not hmac.compare_digest(existing.response_sha256, digest):
                raise DiscoveryAutomationError("discovery_page_conflict")
            return _next_target(run, existing.next_offset)
        if page.offset != run.page_count * 25:
            raise DiscoveryAutomationError("discovery_page_conflict")
        policy = self._session.get(ArtistPolicyRow, run.policy_id)
        if policy is None:
            raise DiscoveryAutomationError("discovery_policy_not_found")
        self._lock_owner_auto_quota(policy.user_id)
        self._session.add(
            DiscoveryRunPageRow(
                run_id=run_id,
                ordinal=page_ordinal,
                page_offset=page.offset,
                response_sha256=digest,
                observed_count=len(page.observations),
                checkpoint=page.checkpoint,
                next_offset=page.next_offset,
            )
        )
        selected = 0
        for observation in page.observations:
            candidate = self._coalesce_candidate(
                run, policy, observation.candidate, observation.release_date, now
            )
            automatic = (
                policy.import_mode == "AUTO_IMPORT" and observation.candidate.acquisition_allowed
            )
            if (
                automatic
                and run.auto_selected_count + selected < 10
                and self._auto_quota(policy.user_id, now) < 50
            ):
                enqueued = self._enqueue_auto(candidate, policy, now)
                selected += int(enqueued)
                selected_at = now if enqueued else None
            else:
                selected_at = None
            self._session.execute(
                insert(DiscoveryRunCandidateRow)
                .values(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    selected_at=selected_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DiscoveryRunCandidateRow.run_id,
                        DiscoveryRunCandidateRow.candidate_id,
                    ]
                )
            )
        run.observed_count += len(page.observations)
        run.auto_selected_count += selected
        run.page_count += 1
        run.checkpoint = page.checkpoint
        run.updated_at = now
        run.row_version += 1
        self._session.flush()
        return _next_target(run, page.next_offset)

    def complete_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> None:
        self._require_fence(fence, owner_user_id)
        run = self._locked_run(run_id, owner_user_id)
        if run is None or run.job_id != fence.job_id:
            raise DiscoveryAutomationError("discovery_run_not_found")
        if run.state in {"COMPLETED", "PARTIAL", "FAILED_TERMINAL", "CANCELLED"}:
            return
        run.state, run.completed_at, run.updated_at = "COMPLETED", now, now
        run.row_version += 1

    def fail_scan(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        error_code: str,
        terminal: bool,
        now: datetime,
    ) -> None:
        self._require_fence(fence, owner_user_id)
        run = self._locked_run(run_id, owner_user_id)
        if run is None or run.job_id != fence.job_id:
            return
        if run.state in {"COMPLETED", "PARTIAL", "FAILED_TERMINAL", "CANCELLED"}:
            return
        run.state = (
            ("PARTIAL" if run.page_count else "FAILED_TERMINAL") if terminal else "RETRY_WAIT"
        )
        run.error_code, run.completed_at, run.updated_at = (
            normalize_run_error_code(error_code),
            now if terminal else None,
            now,
        )
        run.row_version += 1

    def cleanup_expired(self, *, now: datetime, limit: int) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("automation cleanup limit is invalid")
        cutoff = now - timedelta(days=30)
        receipt_ids = tuple(
            self._session.scalars(
                select(CandidateActionReceiptRow.action_receipt_id)
                .where(CandidateActionReceiptRow.created_at <= cutoff)
                .order_by(
                    CandidateActionReceiptRow.created_at,
                    CandidateActionReceiptRow.action_receipt_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        if receipt_ids:
            self._session.execute(
                delete(CandidateActionReceiptRow).where(
                    CandidateActionReceiptRow.action_receipt_id.in_(receipt_ids)
                )
            )
        remaining = limit - len(receipt_ids)
        if remaining == 0:
            return len(receipt_ids)
        run_ids = tuple(
            self._session.scalars(
                select(DiscoveryRunRow.run_id)
                .where(
                    DiscoveryRunRow.state.in_(
                        {"COMPLETED", "PARTIAL", "FAILED_TERMINAL", "CANCELLED"}
                    ),
                    DiscoveryRunRow.updated_at <= cutoff,
                )
                .order_by(DiscoveryRunRow.updated_at, DiscoveryRunRow.run_id)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            )
        )
        if run_ids:
            self._session.execute(
                delete(DiscoveryRunCandidateRow).where(DiscoveryRunCandidateRow.run_id.in_(run_ids))
            )
            self._session.execute(
                delete(DiscoveryRunPageRow).where(DiscoveryRunPageRow.run_id.in_(run_ids))
            )
            self._session.execute(
                delete(DiscoveryRunRow).where(DiscoveryRunRow.run_id.in_(run_ids))
            )
        return len(receipt_ids) + len(run_ids)

    def _create_run(
        self,
        policy: ArtistPolicyRow,
        now: datetime,
        operation_id: UUID | None,
        request_sha256: bytes | None,
    ) -> DiscoveryRunRow:
        due_slot = now.replace(minute=0, second=0, microsecond=0) if operation_id is None else now
        existing = self._session.scalar(
            select(DiscoveryRunRow).where(
                DiscoveryRunRow.policy_id == policy.policy_id,
                DiscoveryRunRow.policy_revision == policy.current_revision,
                DiscoveryRunRow.due_slot_at == due_slot,
            )
        )
        if existing is not None:
            return existing
        provider = self._require_provider()
        query_sha256 = _canonical_query_sha256(
            provider_id=policy.provider_id,
            adapter_id=provider.adapter_id,
            adapter_version=provider.adapter_version,
            provider_artist_id=policy.provider_artist_id,
        )
        job = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=DISCOVERY_SCAN_JOB,
                user_id=policy.user_id,
                priority=3,
                payload={
                    "run_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"autplay:a1c-run-v1:{policy.policy_id}:{policy.current_revision}:{due_slot.isoformat()}",
                        )
                    )
                },
                idempotency_scope=f"discovery.scan:{policy.user_id}",
                idempotency_key=f"{policy.policy_id}:{policy.current_revision}:{due_slot.isoformat()}",
            )
        )
        run = DiscoveryRunRow(
            run_id=uuid5(
                NAMESPACE_URL,
                f"autplay:a1c-run-v1:{policy.policy_id}:{policy.current_revision}:{due_slot.isoformat()}",
            ),
            user_id=policy.user_id,
            policy_id=policy.policy_id,
            policy_revision=policy.current_revision,
            provider_id=policy.provider_id,
            adapter_id=provider.adapter_id,
            adapter_version=provider.adapter_version,
            provider_artist_id=policy.provider_artist_id,
            canonical_query_sha256=query_sha256,
            due_slot_at=due_slot,
            operation_id=operation_id,
            request_sha256=request_sha256,
            job_id=job.job_id,
        )
        self._session.add(run)
        return run

    def _locked_run(self, run_id: UUID, owner: UUID) -> DiscoveryRunRow | None:
        return self._session.scalar(
            select(DiscoveryRunRow)
            .where(DiscoveryRunRow.run_id == run_id, DiscoveryRunRow.user_id == owner)
            .with_for_update()
        )

    def _require_provider(self) -> SourceProviderRow:
        provider = self._session.get(SourceProviderRow, JAMENDO_PROVIDER_ID)
        if (
            provider is None
            or not provider.enabled
            or provider.deleted_at is not None
            or provider.adapter_id != "autplay.jamendo.manual"
            or provider.adapter_version != "1.0.0"
            or not _RELEASE_WATCH.issubset(set(provider.capabilities))
        ):
            raise DiscoveryAutomationError("source_authorization_unavailable")
        return provider

    def _lock_operation(self, owner: UUID, operation_id: UUID) -> None:
        scope = b"a1c-web-operation-v1" + owner.bytes + operation_id.bytes
        key = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def _require_operation_available(self, owner: UUID, operation_id: UUID, *, action: str) -> None:
        """Keep owner operation identifiers global across every A1C Web mutation."""

        if (
            action != "POLICY"
            and self._session.scalar(
                select(ArtistPolicyRevisionRow.policy_id).where(
                    ArtistPolicyRevisionRow.owner_user_id == owner,
                    ArtistPolicyRevisionRow.operation_id == operation_id,
                )
            )
            is not None
        ):
            raise DiscoveryAutomationError("operation_conflict")
        if (
            action != "RUN"
            and self._session.scalar(
                select(DiscoveryRunRow.run_id).where(
                    DiscoveryRunRow.user_id == owner,
                    DiscoveryRunRow.operation_id == operation_id,
                )
            )
            is not None
        ):
            raise DiscoveryAutomationError("operation_conflict")
        if (
            action != "CANDIDATE"
            and self._session.scalar(
                select(CandidateActionReceiptRow.action_receipt_id).where(
                    CandidateActionReceiptRow.user_id == owner,
                    CandidateActionReceiptRow.operation_id == operation_id,
                )
            )
            is not None
        ):
            raise DiscoveryAutomationError("operation_conflict")

    @staticmethod
    def _increases_policy_authority(policy: ArtistPolicyRow, command: PolicyMutation) -> bool:
        discovery_rank = {"DISABLED": 0, "MANUAL_ONLY": 1, "SCHEDULED": 2}
        import_rank = {"REVIEW_REQUIRED": 0, "AUTO_IMPORT": 1}
        return (
            discovery_rank[command.discovery_mode] > discovery_rank[policy.discovery_mode]
            or import_rank[command.import_mode] > import_rank[policy.import_mode]
        )

    def _require_artist(self, owner: UUID, artist_id: UUID) -> None:
        reachable = self._session.scalar(
            select(ArtistRow.artist_id)
            .join(ArtistCreditNameRow, ArtistCreditNameRow.artist_id == ArtistRow.artist_id)
            .join(
                RecordingRow, RecordingRow.artist_credit_id == ArtistCreditNameRow.artist_credit_id
            )
            .join(UserTrackRefRow, UserTrackRefRow.recording_id == RecordingRow.recording_id)
            .join(
                LibraryEntryRow,
                LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .where(
                ArtistRow.artist_id == artist_id,
                ArtistRow.deleted_at.is_(None),
                RecordingRow.deleted_at.is_(None),
                UserTrackRefRow.user_id == owner,
                UserTrackRefRow.resolution_status == "RESOLVED",
                UserTrackRefRow.deleted_at.is_(None),
                LibraryEntryRow.user_id == owner,
                LibraryEntryRow.removed_at.is_(None),
                ~select(DiscoveryCandidateRow.candidate_id)
                .where(
                    DiscoveryCandidateRow.library_entry_id == LibraryEntryRow.library_entry_id,
                    DiscoveryCandidateRow.acquisition_state != "READY",
                )
                .exists(),
            )
            .limit(1)
        )
        if reachable is None:
            raise DiscoveryAutomationError("discovery_target_not_found")

    def _require_provider_artist_binding(
        self, owner: UUID, artist_id: UUID, provider_artist_id: str
    ) -> None:
        bound = self._session.scalar(
            select(DiscoveryCandidateRow.candidate_id)
            .where(
                DiscoveryCandidateRow.user_id == owner,
                DiscoveryCandidateRow.provider_id == JAMENDO_PROVIDER_ID,
                DiscoveryCandidateRow.canonical_artist_id == artist_id,
                DiscoveryCandidateRow.provider_artist_id == provider_artist_id,
            )
            .limit(1)
        )
        if bound is None:
            raise DiscoveryAutomationError("policy_provider_binding_unconfirmed")

    def _require_current_policy(self, policy: ArtistPolicyRow, now: datetime) -> None:
        self._require_provider()
        self._require_artist(policy.user_id, policy.canonical_artist_id)
        if not policy.automation_enabled or policy.discovery_mode != "SCHEDULED":
            raise DiscoveryAutomationError("automation_not_active")

    def _require_run_current(self, run: DiscoveryRunRow, now: datetime) -> None:
        policy = self._session.get(ArtistPolicyRow, run.policy_id)
        if (
            policy is None
            or policy.current_revision != run.policy_revision
            or run.user_id != policy.user_id
            or run.provider_id != policy.provider_id
            or run.provider_artist_id != policy.provider_artist_id
        ):
            raise DiscoveryAutomationError("policy_revision_stale")
        self._require_current_policy(policy, now)

    def _coalesce_candidate(
        self,
        run: DiscoveryRunRow,
        policy: ArtistPolicyRow,
        evidence: DiscoveryCandidate,
        release_date: date,
        now: datetime,
    ) -> DiscoveryCandidateRow:
        candidate_data = evidence
        candidate = self._session.scalar(
            select(DiscoveryCandidateRow)
            .where(
                DiscoveryCandidateRow.user_id == policy.user_id,
                DiscoveryCandidateRow.provider_id == JAMENDO_PROVIDER_ID,
                DiscoveryCandidateRow.market_scope == "GLOBAL",
                DiscoveryCandidateRow.provider_track_id == candidate_data.provider_track_id,
            )
            .with_for_update()
        )
        released_at = datetime(release_date.year, release_date.month, release_date.day, tzinfo=UTC)
        if candidate is None:
            candidate = DiscoveryCandidateRow(
                user_id=policy.user_id,
                provider_id=JAMENDO_PROVIDER_ID,
                canonical_artist_id=policy.canonical_artist_id,
                market_scope="GLOBAL",
                provider_track_id=candidate_data.provider_track_id,
                provider_artist_id=candidate_data.provider_artist_id,
                title=candidate_data.title,
                artist=candidate_data.artist,
                album=candidate_data.album,
                duration_seconds=candidate_data.duration_seconds,
                license_url=candidate_data.license_url,
                share_url=candidate_data.share_url,
                disposition="SELECTABLE" if candidate_data.acquisition_allowed else "UNAVAILABLE",
                acquisition_state="NOT_REQUESTED",
                source_authorization_revision=1,
                released_at=released_at,
                selection_origin="AUTOMATIC",
                policy_id=policy.policy_id,
                policy_revision=policy.current_revision,
            )
            self._session.add(candidate)
        else:
            if (
                candidate.canonical_artist_id != policy.canonical_artist_id
                or candidate.provider_artist_id != evidence.provider_artist_id
            ):
                raise DiscoveryAutomationError("identity_review_required")
            candidate.title, candidate.artist, candidate.album, candidate.released_at = (
                candidate_data.title,
                candidate_data.artist,
                candidate_data.album,
                released_at,
            )
            candidate.updated_at, candidate.row_version = now, candidate.row_version + 1
        self._session.flush([candidate])
        return candidate

    def _auto_quota(self, owner: UUID, now: datetime) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(AcquisitionAttemptRow)
                .where(
                    AcquisitionAttemptRow.candidate_id == DiscoveryCandidateRow.candidate_id,
                    DiscoveryCandidateRow.user_id == owner,
                    AcquisitionAttemptRow.origin == "AUTOMATIC",
                    AcquisitionAttemptRow.created_at >= now - _CADENCE,
                )
            )
            or 0
        )

    def _lock_owner_auto_quota(self, owner: UUID) -> None:
        scope = b"a1c-owner-auto-quota-v1" + owner.bytes
        key = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def _enqueue_auto(
        self, candidate: DiscoveryCandidateRow, policy: ArtistPolicyRow, now: datetime
    ) -> bool:
        if candidate.disposition != "SELECTABLE" or candidate.acquisition_state != "NOT_REQUESTED":
            return False
        auth = self._session.scalar(
            select(SourceAuthorizationRow)
            .where(
                SourceAuthorizationRow.user_id == policy.user_id,
                SourceAuthorizationRow.provider_id == JAMENDO_PROVIDER_ID,
                SourceAuthorizationRow.canonical_artist_id == policy.canonical_artist_id,
                SourceAuthorizationRow.purpose == "AUTO_IMPORT",
                SourceAuthorizationRow.policy_id == policy.policy_id,
                SourceAuthorizationRow.policy_revision == policy.current_revision,
                SourceAuthorizationRow.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if auth is not None and auth.expires_at <= now:
            auth.revoked_at = now
            auth.row_version += 1
            auth = None
        if auth is None:
            revision = (
                self._session.scalar(
                    select(func.max(SourceAuthorizationRow.revision)).where(
                        SourceAuthorizationRow.user_id == policy.user_id,
                        SourceAuthorizationRow.provider_id == JAMENDO_PROVIDER_ID,
                        SourceAuthorizationRow.canonical_artist_id == policy.canonical_artist_id,
                        SourceAuthorizationRow.purpose == "AUTO_IMPORT",
                    )
                )
                or 0
            ) + 1
            auth = SourceAuthorizationRow(
                user_id=policy.user_id,
                provider_id=JAMENDO_PROVIDER_ID,
                canonical_artist_id=policy.canonical_artist_id,
                adapter_id="autplay.jamendo.manual",
                adapter_version="1.0.0",
                market_scope="GLOBAL",
                rights_capability="AUTHORIZED_DOWNLOAD",
                purpose="AUTO_IMPORT",
                policy_id=policy.policy_id,
                policy_revision=policy.current_revision,
                revision=revision,
                policy_reference="ADR-042:a1c-auto-import:1",
                expires_at=now + _CADENCE,
                granted_at=now,
            )
            self._session.add(auth)
            self._session.flush([auth])
        attempt = AcquisitionAttemptRow(
            candidate_id=candidate.candidate_id,
            origin="AUTOMATIC",
            policy_id=policy.policy_id,
            policy_revision=policy.current_revision,
            source_authorization_id=auth.authorization_id,
            source_authorization_revision=auth.revision,
            state="QUEUED",
        )
        self._session.add(attempt)
        self._session.flush([attempt])
        job = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=DISCOVERY_ACQUIRE_JOB,
                user_id=policy.user_id,
                priority=4,
                payload={"candidate_id": str(candidate.candidate_id)},
                idempotency_scope=f"discovery.acquire:{policy.user_id}",
                idempotency_key=f"{candidate.candidate_id}:{attempt.acquisition_attempt_id}",
            )
        )
        attempt.job_id = job.job_id
        candidate.current_acquisition_attempt_id, candidate.source_authorization_id = (
            attempt.acquisition_attempt_id,
            auth.authorization_id,
        )
        candidate.source_authorization_revision, candidate.job_id = auth.revision, job.job_id
        candidate.disposition, candidate.acquisition_state = "SELECTED", "QUEUED"
        candidate.selection_origin = "AUTOMATIC"
        candidate.policy_id = policy.policy_id
        candidate.policy_revision = policy.current_revision
        candidate.error_code = None
        candidate.updated_at = now
        candidate.row_version += 1
        return True

    def _cancel_auto_work(self, policy_id: UUID, now: datetime) -> None:
        policy = self._session.get(ArtistPolicyRow, policy_id)
        if policy is None:
            raise DiscoveryAutomationError("discovery_policy_not_found")
        for run in self._session.scalars(
            select(DiscoveryRunRow)
            .where(
                DiscoveryRunRow.policy_id == policy_id,
                DiscoveryRunRow.state.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
            )
            .with_for_update()
        ):
            run.state, run.completed_at, run.updated_at = "CANCELLED", now, now
            run.row_version += 1
            if run.job_id is not None:
                PostgresJobRepository(self._session).request_cancel_for_owner(
                    job_id=run.job_id, owner_user_id=policy.user_id
                )
        for attempt in self._session.scalars(
            select(AcquisitionAttemptRow)
            .where(
                AcquisitionAttemptRow.policy_id == policy_id,
                AcquisitionAttemptRow.origin == "AUTOMATIC",
                AcquisitionAttemptRow.state.in_(("QUEUED", "RUNNING")),
            )
            .with_for_update()
        ):
            attempt.state, attempt.completed_at, attempt.updated_at = "CANCELLED", now, now
            attempt.row_version += 1
            candidate = self._session.get(DiscoveryCandidateRow, attempt.candidate_id)
            if attempt.job_id is not None and candidate is not None:
                PostgresJobRepository(self._session).request_cancel_for_owner(
                    job_id=attempt.job_id, owner_user_id=candidate.user_id
                )
            if (
                candidate is not None
                and candidate.current_acquisition_attempt_id == attempt.acquisition_attempt_id
                and candidate.acquisition_state != "READY"
            ):
                candidate.acquisition_state = "CANCELLED"
                candidate.error_code = "policy_revision_stale"
                candidate.updated_at = now
                candidate.row_version += 1
        for authorization in self._session.scalars(
            select(SourceAuthorizationRow)
            .where(
                SourceAuthorizationRow.policy_id == policy_id,
                SourceAuthorizationRow.purpose == "AUTO_IMPORT",
                SourceAuthorizationRow.revoked_at.is_(None),
            )
            .with_for_update()
        ):
            authorization.revoked_at = now
            authorization.row_version += 1

    def _require_fence(self, fence: LeaseFence, owner: UUID) -> None:
        job = self._session.scalar(
            select(JobRow)
            .where(
                JobRow.job_id == fence.job_id,
                JobRow.user_id == owner,
                JobRow.job_type == DISCOVERY_SCAN_JOB.job_type,
                JobRow.schema_version == 1,
                JobRow.state == "RUNNING",
                JobRow.lease_owner == fence.worker_id,
                JobRow.attempt_count == fence.attempt_no,
                JobRow.lease_deadline.is_not(None),
                JobRow.lease_deadline > func.now(),
            )
            .with_for_update()
        )
        if job is None:
            raise DiscoveryAutomationError("lease_fence_lost")


class SqlAlchemyDiscoveryAutomationRepository:
    """Open one short transaction for every application/worker repository call."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def set_policy(
        self, *, owner_user_id: UUID, command: PolicyMutation, request_sha256: bytes, now: datetime
    ) -> PolicyMutationResult:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).set_policy(
                owner_user_id=owner_user_id,
                command=command,
                request_sha256=request_sha256,
                now=now,
            )

    def list_policies(self, *, owner_user_id: UUID, limit: int) -> tuple[PolicyView, ...]:
        with self._sessions() as session:
            return PostgresDiscoveryAutomationRepository(session).list_policies(
                owner_user_id=owner_user_id, limit=limit
            )

    def list_runs(self, *, owner_user_id: UUID, limit: int) -> tuple[DiscoveryRunView, ...]:
        with self._sessions() as session:
            return PostgresDiscoveryAutomationRepository(session).list_runs(
                owner_user_id=owner_user_id, limit=limit
            )

    def list_candidates(
        self, *, owner_user_id: UUID, run_id: UUID, limit: int
    ) -> tuple[ReleaseCandidateView, ...]:
        with self._sessions() as session:
            return PostgresDiscoveryAutomationRepository(session).list_candidates(
                owner_user_id=owner_user_id, run_id=run_id, limit=limit
            )

    def run_now(
        self,
        *,
        owner_user_id: UUID,
        policy_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> DiscoveryRunView:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).run_now(
                owner_user_id=owner_user_id,
                policy_id=policy_id,
                operation_id=operation_id,
                request_sha256=request_sha256,
                now=now,
            )

    def act_on_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
        now: datetime,
    ) -> CandidateActionResult:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).act_on_candidate(
                owner_user_id=owner_user_id,
                candidate_id=candidate_id,
                action=action,
                operation_id=operation_id,
                request_sha256=request_sha256,
                now=now,
            )

    def dispatch_due(self, *, now: datetime, limit: int) -> int:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).dispatch_due(now=now, limit=limit)

    def claim_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> ScanTarget | None:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).claim_scan(
                run_id=run_id, owner_user_id=owner_user_id, fence=fence, now=now
            )

    def require_current(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        boundary: str,
    ) -> None:
        with self._sessions.begin() as session:
            PostgresDiscoveryAutomationRepository(session).require_current(
                run_id=run_id,
                owner_user_id=owner_user_id,
                fence=fence,
                boundary=boundary,
            )

    def commit_page(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        page: ProviderTrackPage,
        now: datetime,
    ) -> ScanTarget | None:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).commit_page(
                run_id=run_id,
                owner_user_id=owner_user_id,
                fence=fence,
                page=page,
                now=now,
            )

    def complete_scan(
        self, *, run_id: UUID, owner_user_id: UUID, fence: LeaseFence, now: datetime
    ) -> None:
        with self._sessions.begin() as session:
            PostgresDiscoveryAutomationRepository(session).complete_scan(
                run_id=run_id, owner_user_id=owner_user_id, fence=fence, now=now
            )

    def fail_scan(
        self,
        *,
        run_id: UUID,
        owner_user_id: UUID,
        fence: LeaseFence,
        error_code: str,
        terminal: bool,
        now: datetime,
    ) -> None:
        with self._sessions.begin() as session:
            PostgresDiscoveryAutomationRepository(session).fail_scan(
                run_id=run_id,
                owner_user_id=owner_user_id,
                fence=fence,
                error_code=error_code,
                terminal=terminal,
                now=now,
            )

    def cleanup_expired(self, *, now: datetime, limit: int) -> int:
        with self._sessions.begin() as session:
            return PostgresDiscoveryAutomationRepository(session).cleanup_expired(
                now=now, limit=limit
            )


def _policy_view(row: ArtistPolicyRow) -> PolicyView:
    return PolicyView(
        row.policy_id,
        row.canonical_artist_id,
        row.provider_artist_id,
        row.discovery_mode,
        row.import_mode,
        row.automation_enabled,
        row.current_revision,
        row.last_checked_at,
        row.next_eligible_at,
    )


def _policy_revision_view(policy: ArtistPolicyRow, revision: ArtistPolicyRevisionRow) -> PolicyView:
    return PolicyView(
        policy.policy_id,
        policy.canonical_artist_id,
        policy.provider_artist_id,
        revision.discovery_mode,
        revision.import_mode,
        revision.automation_enabled,
        revision.revision,
        revision.last_checked_at,
        revision.next_eligible_at,
    )


def _run_view(row: DiscoveryRunRow) -> DiscoveryRunView:
    return DiscoveryRunView(
        row.run_id,
        row.policy_id,
        row.policy_revision,
        row.state,
        row.observed_count,
        row.auto_selected_count,
        row.page_count,
        row.created_at,
        row.completed_at,
        row.error_code,
    )


def _candidate_view(
    row: DiscoveryCandidateRow, run_id: UUID, selected: bool
) -> ReleaseCandidateView:
    return ReleaseCandidateView(
        row.candidate_id,
        run_id,
        row.title,
        row.artist,
        row.album,
        row.released_at,
        row.disposition,
        row.acquisition_state,
        selected,
    )


def _candidate_action_result(
    row: DiscoveryCandidateRow, *, replayed: bool
) -> CandidateActionResult:
    return CandidateActionResult(
        row.candidate_id,
        row.disposition,
        row.acquisition_state,
        replayed,
    )


def _next_target(run: DiscoveryRunRow, next_offset: int | None) -> ScanTarget | None:
    return (
        ScanTarget(run.run_id, run.user_id, run.provider_artist_id, next_offset, run.page_count)
        if next_offset is not None and run.page_count < 2
        else None
    )


def _canonical_query_sha256(
    *, provider_id: UUID, adapter_id: str, adapter_version: str, provider_artist_id: str
) -> bytes:
    query: dict[str, JsonValue] = {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "contract_version": "release-discovery-v1",
        "order": ["RELEASE_DATE_DESC", "TRACK_ID_DESC"],
        "page_size": 25,
        "provider_artist_id": provider_artist_id,
        "provider_id": str(provider_id),
        "schema_version": 1,
    }
    return hashlib.sha256(rfc8785.dumps(query)).digest()


def _page_document(page: ProviderTrackPage, run: DiscoveryRunRow) -> dict[str, JsonValue]:
    return {
        "adapter_id": run.adapter_id,
        "adapter_version": run.adapter_version,
        "canonical_query_sha256": run.canonical_query_sha256.hex(),
        "provider_id": str(run.provider_id),
        "provider_artist_id": page.provider_artist_id,
        "offset": page.offset,
        "next_offset": page.next_offset,
        "checkpoint": page.checkpoint,
        "tracks": [
            {"id": item.candidate.provider_track_id, "released": item.release_date.isoformat()}
            for item in page.observations
        ],
    }


__all__ = (
    "DISCOVERY_SCAN_JOB",
    "PostgresDiscoveryAutomationRepository",
    "SqlAlchemyDiscoveryAutomationRepository",
)
