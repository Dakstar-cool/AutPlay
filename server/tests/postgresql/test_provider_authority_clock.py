"""Waiting for an exact job lock cannot extend expired worker/media authority."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import JobRow, UserSessionRow
from autplay.adapters.postgresql.models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoExecutionRow,
    ResourceIoPermitRow,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionKind, ExecutionTicket, ProcessIdentity
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import internet

__all__ = ["admission"]


def _blocked_by(observer: Session, pid: int) -> bool:
    """Observe waiters that may have connected after the transaction snapshot."""
    observer.execute(select(func.pg_stat_clear_snapshot()))
    return bool(
        observer.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE :pid = ANY(pg_blocking_pids(pid)))"
            ),
            {"pid": pid},
        )
    )


@pytest.mark.parametrize(
    "phase,expiry",
    [
        (phase, expiry)
        for phase in ("open", "prepare", "start", "renew")
        for expiry in ("job", "session", "activation", "permit")
        if (phase, expiry) != ("open", "permit")
    ],
)
def test_authority_expiring_during_job_lock_cannot_grant_or_renew(
    admission: AdmissionHarness,
    phase: str,
    expiry: str,
) -> None:
    admission.budget()
    actor, claim = internet(admission)
    active = fence(admission.service.acquire_worker(claim))
    ticket = None
    if phase != "open":
        permit = admission.service.open_io(claim, active, claim.acquisition_id)
        ticket = ExecutionTicket(
            uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id
        )
        if phase in {"start", "renew"}:
            admission.service.prepare_execution(claim, ticket)
        if phase == "renew":
            admission.service.start_execution(claim, ticket, ProcessIdentity(12345, b"a" * 32))
    with admission.sessions.begin() as session:
        now = present(session.scalar(select(func.clock_timestamp())))
        deadline = now + timedelta(milliseconds=250)
        if expiry == "job":
            present(session.get(JobRow, claim.fence.job_id)).lease_deadline = deadline
        elif expiry == "session":
            present(session.get(UserSessionRow, actor.session_id)).expires_at = deadline
        elif expiry == "activation":
            present(session.get(ResourceAdmissionRow, active.operation_id)).lease_until = deadline
        else:
            assert ticket is not None
            present(session.get(ResourceIoPermitRow, ticket.permit.permit_id)).expires_at = deadline

    def invoke() -> object:
        if phase == "open":
            return admission.service.open_io(claim, active, claim.acquisition_id)
        assert ticket is not None
        if phase == "prepare":
            return admission.service.prepare_execution(claim, ticket)
        if phase == "start":
            return admission.service.start_execution(
                claim, ticket, ProcessIdentity(12345, b"a" * 32)
            )
        return admission.service.renew_execution_io(claim, ticket)

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute(select(JobRow).where(JobRow.job_id == claim.fence.job_id).with_for_update())
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        pending = pool.submit(invoke)
        try:
            until = monotonic() + 5
            with admission.sessions() as observer:
                while not _blocked_by(observer, pid):
                    assert not pending.done(), "RPC must actually wait behind the exact job lock"
                    assert monotonic() < until
                    sleep(0.005)
                remaining = (
                    deadline - present(observer.scalar(select(func.clock_timestamp())))
                ).total_seconds()
            sleep(max(0, remaining) + 0.05)
        finally:
            blocker.rollback()
        with pytest.raises(ResourceAdmissionError):
            pending.result(timeout=5)
    with admission.sessions() as session:
        if phase == "open":
            assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0
        elif phase == "prepare":
            assert session.scalar(select(func.count()).select_from(ResourceIoExecutionRow)) == 0
        elif phase == "start":
            assert session.scalar(select(ResourceIoExecutionRow.state)) == "PREPARED"
        if expiry == "permit":
            assert session.scalar(select(ResourceIoPermitRow.expires_at)) == deadline
