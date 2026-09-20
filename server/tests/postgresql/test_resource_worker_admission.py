"""Real PostgreSQL worker lineage/rebind proof; no external provider or media launch."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.discovery_runtime import PostgresBulkDiscoveryRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    DiscoveryCandidateRow,
    JobRow,
    SourceAuthorizationRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.internet_music import InternetAcquisitionRow
from autplay.application.internet_music import INTERNET_ACQUIRE_JOB, InternetMusicService
from autplay.domain.auth import Principal
from autplay.domain.jobs import JobKey, LeaseFence
from autplay.domain.resource_admission import (
    AcquisitionClaim,
    AdmissionState,
    AuthorityKind,
    ResourceAdmissionError,
)
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from sqlalchemy import select

from .test_acquisition_authority import Provider
from .test_discovery_runtime import _seed_import, _track
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present

__all__ = ["admission"]


def internet(
    harness: AdmissionHarness, *, worker: str = "worker-one"
) -> tuple[Principal, AcquisitionClaim]:
    actor = harness.actor()
    service = InternetMusicService(harness.sessions, object(), Provider())
    search = UUID(service.search(actor, "synthetic worker", uuid4())["search_id"])
    acquisition = UUID(service.select(actor, search, "candidate00")["acquisition_id"])
    with harness.sessions.begin() as session:
        claim = PostgresJobRepository(session).claim(
            worker_id=worker,
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    return actor, AcquisitionClaim(claim.fence, "INTERNET_ACQUISITION", acquisition)


def successor(harness: AdmissionHarness, claim: AcquisitionClaim) -> AcquisitionClaim:
    new_fence = LeaseFence(claim.fence.job_id, "worker-next", claim.fence.attempt_no + 1)
    with harness.sessions.begin() as session:
        job = present(session.get(JobRow, claim.fence.job_id))
        job.lease_owner = new_fence.worker_id
        job.attempt_count = new_fence.attempt_no
        job.lease_deadline = datetime.now(UTC) + timedelta(minutes=1)
    return replace(claim, fence=new_fence)


def test_worker_rebind_retains_original_charge_until_exact_old_execution_closes(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor, claim = internet(admission)
    status = admission.service.acquire_worker(claim)
    original = fence(status)
    assert admission.service.acquire_worker(claim).operation.fence == original
    assert status.usage.account == status.usage.device == status.usage.server == 1
    with pytest.raises(ResourceAdmissionError):
        admission.service.acquire(actor, claim.request)
    with pytest.raises(ResourceAdmissionError):
        admission.service.poll(actor, original.operation_id)
    permit = admission.service.open_io(claim, original, claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    child = ProcessIdentity(23456, b"p" * 32)
    admission.service.prepare_execution(claim, ticket)
    admission.service.start_execution(claim, ticket, child)
    new_claim = successor(admission, claim)
    waiting = admission.service.acquire_worker(new_claim)
    assert waiting.operation.state == AdmissionState.WAITING
    assert waiting.operation.request_sha256 == status.operation.request_sha256
    assert waiting.usage.server == 1
    with pytest.raises(ResourceAdmissionError):
        admission.service.renew_execution_io(claim, ticket)
    with pytest.raises(ResourceAdmissionError):
        admission.service.release(claim, original)
    with pytest.raises(ResourceAdmissionError):
        admission.service.open_io(new_claim, original, claim.acquisition_id)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
        admission.service.close_io(permit)
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    )
    admission.service.close_io(permit)
    new_status = admission.service.acquire_worker(new_claim)
    next_fence = fence(new_status)
    assert next_fence.generation == original.generation + 1
    assert next_fence.activation_id != original.activation_id
    assert new_status.usage.server == 1
    with pytest.raises(ResourceAdmissionError):
        admission.service.release(new_claim, original)
    admission.service.release(new_claim, next_fence)


@pytest.mark.parametrize("intervening_scheduler", [False, True])
def test_waiting_rebind_preserves_queue_age_and_does_not_mint_another_operation(
    admission: AdmissionHarness,
    intervening_scheduler: bool,
) -> None:
    admission.budget(transfers=1)
    _, active = internet(admission)
    admitted = admission.service.acquire_worker(active)
    _, waiting = internet(admission, worker="worker-two")
    queued = admission.service.acquire_worker(waiting)
    assert queued.operation.state == AdmissionState.WAITING
    new_claim = successor(admission, waiting)
    if intervening_scheduler:
        admission.service.release(active, fence(admitted))
    rebound = admission.service.acquire_worker(new_claim)
    assert rebound.operation.enqueued_at == queued.operation.enqueued_at
    assert rebound.operation.request_sha256 == queued.operation.request_sha256
    assert rebound.operation.request.operation_id == queued.operation.request.operation_id
    if not intervening_scheduler:
        admission.service.release(active, fence(admitted))
    assert (
        admission.service.poll(new_claim, new_claim.request.operation_id).operation.state
        == AdmissionState.ACTIVE
    )


@pytest.mark.parametrize(
    "failure", ["session", "generation", "cancelled", "wrong_job", "wrong_target"]
)
def test_worker_cannot_derive_replacement_or_mismatched_authority(
    admission: AdmissionHarness,
    failure: str,
) -> None:
    admission.budget()
    actor, claim = internet(admission)
    with admission.sessions.begin() as session:
        if failure == "session":
            present(session.get(UserSessionRow, actor.session_id)).revoked_at = datetime.now(UTC)
        elif failure == "generation":
            present(session.get(UserAccountRow, actor.user_id)).authority_generation += 1
        elif failure == "cancelled":
            present(session.get(JobRow, claim.fence.job_id)).cancel_requested_at = datetime.now(UTC)
        elif failure == "wrong_job":
            claim = replace(claim, fence=LeaseFence(uuid4(), claim.fence.worker_id, 1))
        else:
            claim = replace(claim, acquisition_id=uuid4())
    with pytest.raises(ResourceAdmissionError):
        admission.service.acquire_worker(claim)


def test_a1_worker_uses_account_capacity_without_fabricated_device_and_rechecks_source(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    with admission.sessions.begin() as session:
        owner, _ = _seed_import(session, "Worker A1 owner")
    with admission.sessions.begin() as session:
        PostgresBulkDiscoveryRepository(session).start_search_acquisition(
            owner_user_id=owner, operation_id=uuid4(), evidence=_track()
        )
        attempt = present(session.scalar(select(AcquisitionAttemptRow)))
        acquisition_id, source = attempt.acquisition_attempt_id, attempt.source_authorization_id
    with admission.sessions.begin() as session:
        job = PostgresJobRepository(session).claim(
            worker_id="a1-worker",
            supported=(JobKey("discovery.acquire", 1),),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    claim = AcquisitionClaim(job.fence, "DISCOVERY_ACQUISITION", acquisition_id)
    status = admission.service.acquire_worker(claim)
    assert status.operation.authority.authority_kind == AuthorityKind.SERVER_ACQUISITION
    assert status.operation.authority.device_id is None and status.usage.device == 0
    assert status.usage.account == status.usage.server == 1
    active = fence(status)
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, acquisition_id))
        present(
            session.get(DiscoveryCandidateRow, attempt.candidate_id)
        ).acquisition_state = "INGESTING"
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(claim, active, acquisition_id)
    # Exact cleanup remains possible after a successful handoff.
    assert admission.service.release(claim, active).usage.server == 0
    with admission.sessions.begin() as session:
        present(session.get(SourceAuthorizationRow, source)).revoked_at = datetime.now(UTC)
    with pytest.raises(ResourceAdmissionError):
        admission.service.poll(claim, status.operation.request.operation_id)


def test_internet_handoff_releases_immediately_after_provider_execution_closes(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    _, claim = internet(admission)
    active = fence(admission.service.acquire_worker(claim))
    permit = admission.service.open_io(claim, active, claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    child = ProcessIdentity(23456, b"p" * 32)
    admission.service.prepare_execution(claim, ticket)
    admission.service.start_execution(claim, ticket, child)
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    )
    admission.service.close_io(permit)
    with admission.sessions.begin() as session:
        present(session.get(InternetAcquisitionRow, claim.acquisition_id)).state = "PROCESSING"
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(claim, active, claim.acquisition_id)
    assert admission.service.release(claim, active).usage.server == 0
    assert admission.service.release(claim, active).operation.state == AdmissionState.RELEASED
