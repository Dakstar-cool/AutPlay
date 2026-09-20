"""Failure reporting remains fenced and preserves the original classified error."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import cast

import pytest
from autplay.adapters.postgresql.controlled_discovery import PostgresControlledDiscoveryRepository
from autplay.adapters.postgresql.discovery_runtime import DISCOVERY_ACQUIRE_JOB
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    ArtistPolicyRow,
    BulkOperationRow,
    DiscoveryCandidateRow,
    JobRow,
    SourceAuthorizationRow,
)
from autplay.application.controlled_discovery import DiscoveryExecutor
from autplay.application.discovery_acquisition import ControlledDiscoveryAcquisitionHandler
from autplay.domain.jobs import JobLease, TerminalJobError
from sqlalchemy import func, select

from .test_discovery_authority_clock import _automatic, _wait_until_expired
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_worker_resource_wait import a1, context

__all__ = ["admission"]


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("expiry", ["job", "source"])
def test_bulk_lock_wait_rolls_back_failure_after_expiry(
    admission: AdmissionHarness, terminal: bool, expiry: str
) -> None:
    claim = a1(admission)
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate = present(session.get(DiscoveryCandidateRow, attempt.candidate_id))
        candidate_id, owner = candidate.candidate_id, candidate.user_id
        initial = (
            candidate.acquisition_state,
            candidate.row_version,
            attempt.state,
            attempt.row_version,
        )
        deadline = present(session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=1)
        if expiry == "job":
            present(session.get(JobRow, claim.fence.job_id)).lease_deadline = deadline
        else:
            present(
                session.get(SourceAuthorizationRow, attempt.source_authorization_id)
            ).expires_at = deadline
    repository = PostgresControlledDiscoveryRepository(admission.sessions)

    def invoke() -> object:
        repository.fail(
            candidate_id, owner, claim.fence, "discovery_acquisition_failed", terminal=terminal
        )
        return None

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute(select(BulkOperationRow).with_for_update())
        pending = pool.submit(invoke)
        try:
            _wait_until_expired(admission, blocker, pending, deadline)
        finally:
            blocker.rollback()
        # Reporting an old failure is ignored after its transaction rolls back.
        assert pending.result(timeout=5) is None
    with admission.sessions() as session:
        candidate = present(session.get(DiscoveryCandidateRow, candidate_id))
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        assert (
            candidate.acquisition_state,
            candidate.row_version,
            attempt.state,
            attempt.row_version,
        ) == initial
        assert candidate.error_code is None and attempt.error_code is None


def test_stale_policy_failure_keeps_terminal_code_and_does_not_mutate_attempt(
    admission: AdmissionHarness,
) -> None:
    claim = _automatic(admission)
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate = present(session.get(DiscoveryCandidateRow, attempt.candidate_id))
        present(session.get(ArtistPolicyRow, attempt.policy_id)).import_mode = "REVIEW_REQUIRED"
        job = present(session.get(JobRow, claim.fence.job_id))
        lease = JobLease(
            claim.fence,
            DISCOVERY_ACQUIRE_JOB,
            candidate.user_id,
            job.priority,
            {"candidate_id": str(candidate.candidate_id)},
            None,
            present(job.lease_deadline),
            None,
        )
        candidate_id = candidate.candidate_id
    handler = ControlledDiscoveryAcquisitionHandler(
        PostgresControlledDiscoveryRepository(admission.sessions, automatic_enabled=lambda: True),
        admission.service,
        cast(DiscoveryExecutor, object()),
    )
    with pytest.raises(TerminalJobError, match="policy_revision_stale"):
        handler(context(admission, claim), lease)
    with admission.sessions() as session:
        candidate = present(session.get(DiscoveryCandidateRow, candidate_id))
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        assert candidate.acquisition_state == attempt.state == "QUEUED"
        assert candidate.error_code is None and attempt.error_code is None
