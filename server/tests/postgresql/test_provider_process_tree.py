"""Contained descendants retain PostgreSQL charge until exact tree exit."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.vault_process import VaultProcessSupervisor
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthRepository
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    DiscoveryCandidateRow,
    ProviderStagingRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.resource_execution import SqlAlchemyResourceExecutionRepository
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    terminate_resource_authority,
)
from autplay.domain.resource_admission import AdmissionState, ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionKind, ExecutionState, ExecutionTicket
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from process_tree_support import (
    process_tree_factory,
    tree_child_launch,
    wait_marker,
    wait_tree_exit,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import internet
from .test_worker_resource_wait import a1

__all__ = ["admission"]


@pytest.mark.parametrize("transition", ["release", "revoke", "lower_quota"])
@pytest.mark.parametrize("source", ["internet", "discovery"])
def test_live_descendant_keeps_capacity_after_root_exit(
    admission: AdmissionHarness,
    tmp_path: Path,
    transition: str,
    source: str,
) -> None:
    admission.budget(transfers=2 if transition == "lower_quota" else 1)
    actor = None
    if source == "internet":
        actor, claim = internet(admission)
    else:
        claim = a1(admission)
    _, waiting_claim = internet(admission, worker="waiting-worker")
    active = admission.service.acquire_worker(claim)
    permit = admission.service.open_io(claim, fence(active), claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    admission.service.prepare_execution(claim, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        ticket,
        ResourceIoDeadline(monotonic()),
        tree_factory=process_tree_factory(),
        launch=tree_child_launch,
    )
    identity = None
    try:
        identity = child.spawn()
        running = admission.service.start_execution(claim, ticket, identity)
        child.allow_go(running)
        marker = tmp_path / "active-descendant"
        child.go({"marker": str(marker)})
        assert child.read_result()[0] == b"R"
        assert child._process is not None and child._process.wait(timeout=5) == 0
        wait_marker(marker)
        assert child.exit_evidence(identity) is None
        if transition == "release":
            admission.service.release(claim, fence(active))
        elif transition == "revoke":
            with admission.sessions.begin() as session:
                lock_resource_admission(session)
                if actor is not None:
                    auth = SqlAlchemyAuthRepository(session)
                    assert auth.lock_owned_device(actor.user_id, actor.device_id)
                    auth.revoke_device(
                        actor.user_id,
                        actor.device_id,
                        revoked_at=present(session.scalar(select(func.clock_timestamp()))),
                    )
                else:
                    attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
                    candidate = present(session.get(DiscoveryCandidateRow, attempt.candidate_id))
                    present(
                        session.get(UserAccountRow, candidate.user_id, with_for_update=True)
                    ).authority_generation += 1
                    terminate_resource_authority(
                        session,
                        candidate.user_id,
                        present(session.scalar(select(func.clock_timestamp()))),
                    )
            with pytest.raises(ResourceAdmissionError):
                admission.service.renew_execution_io(claim, ticket)
        else:
            admission.budget(transfers=1)
        # Accounting cleanup still cannot replace exact local process-tree proof.
        admission.service.sweep()
        waiting = admission.service.acquire_worker(waiting_claim)
        assert waiting.operation.state == AdmissionState.WAITING
        assert waiting.usage.server == 1
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            admission.service.close_io(permit)
        assert supervisor.snapshot() == (child,)
        with admission.sessions() as session:
            receipt = present(session.get(ProviderStagingRow, ticket.execution_id))
            assert receipt.state == "OWNED" and receipt.closed_at is None
        child.request_stop()
        proof = wait_tree_exit(child, identity)
        closed = admission.service.confirm_execution_exit(ticket, proof)
        assert closed.state == ExecutionState.CLOSED
        # Private OS containment is retained until this durable acknowledgement.
        supervisor.forget(child, closed)
        admission.service.close_io(permit)
        if transition == "lower_quota":
            admission.service.release(claim, fence(active))
        admitted = admission.service.poll(waiting_claim, waiting_claim.request.operation_id)
        assert admitted.operation.state == AdmissionState.ACTIVE
        assert admitted.usage.server == 1
        admission.service.release(waiting_claim, fence(admitted))
        assert (
            admission.service.poll(waiting_claim, waiting_claim.request.operation_id).usage.server
            == 0
        )
        with admission.sessions() as session:
            receipt = present(session.scalar(select(ProviderStagingRow)))
            assert receipt.state == "EXITED" and receipt.closed_at is not None
            assert receipt.closure_evidence_sha256 == proof.evidence_sha256
        assert not supervisor.snapshot()
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


def test_empty_tree_retains_ownership_until_database_exit_acknowledgement(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission.budget(transfers=1)
    _, claim = internet(admission)
    _, other = internet(admission, worker="waiting-worker")
    active = admission.service.acquire_worker(claim)
    permit = admission.service.open_io(claim, fence(active), claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    admission.service.prepare_execution(claim, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(
        ticket,
        ResourceIoDeadline(monotonic()),
        tree_factory=process_tree_factory(),
        launch=tree_child_launch,
    )
    identity = None
    try:
        identity = child.spawn()
        running = admission.service.start_execution(claim, ticket, identity)
        child.allow_go(running)
        marker = tmp_path / "descendant"
        child.go({"marker": str(marker)})
        child.read_result()
        wait_marker(marker)
        admission.service.release(claim, fence(active))
        child.request_stop()
        proof = wait_tree_exit(child, identity)

        def unavailable(*args: object, **kwargs: object) -> None:
            raise SQLAlchemyError("synthetic exit acknowledgement unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyResourceExecutionRepository, "confirm_exit", unavailable)
            with pytest.raises(SQLAlchemyError):
                admission.service.confirm_execution_exit(ticket, proof)
        assert admission.service.inspect_execution(ticket).state == ExecutionState.RUNNING
        waiting = admission.service.acquire_worker(other)
        assert waiting.operation.state == AdmissionState.WAITING and waiting.usage.server == 1
        assert supervisor.snapshot() == (child,)
        assert child._tree is not None and child._tree.seal_if_empty() is not None
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, running)
        with admission.sessions() as session:
            receipt = present(session.get(ProviderStagingRow, ticket.execution_id))
            assert receipt.closed_at is None and receipt.state == "OWNED"
        closed = admission.service.confirm_execution_exit(ticket, proof)
        supervisor.forget(child, closed)
        admission.service.close_io(permit)
        assert not supervisor.snapshot()
        admitted = admission.service.poll(other, other.request.operation_id)
        assert admitted.operation.state == AdmissionState.ACTIVE
        admission.service.release(other, fence(admitted))
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()
