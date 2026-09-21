"""Real PostgreSQL proof that clocks, revocation and cleanup never prove child exit."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from multiprocessing import get_context
from uuid import UUID, uuid4

import pytest
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.models import AudioVariantRow, DeviceRow
from autplay.adapters.postgresql.models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoExecutionRow,
    ResourceIoPermitRow,
)
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.resource_admission import (
    SqlAlchemyResourceAdmissionRepository,
    SqlAlchemyResourceAdmissionUnitOfWork,
    SqlAlchemyResourceAdmissionUnitOfWorkFactory,
)
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    AdmissionState,
    ResourceAdmissionError,
    ResourceKind,
    ResourceRequest,
)
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionState,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from .conftest import DatabaseHarness
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, play, present

__all__ = ["admission"]


def upload_ticket(
    harness: AdmissionHarness,
    actor: Principal,
    upload_id: UUID | None = None,
) -> ExecutionTicket:
    upload_id = upload_id or harness.upload(actor)
    request = ResourceRequest(uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", uuid4(), upload_id)
    active = harness.service.acquire(actor, request)
    permit = harness.service.open_io(actor, fence(active), upload_id, resource_type="UPLOAD_INTENT")
    return ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.VAULT_UPLOAD, upload_id)


def shift_clock(monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    # Shift both lock-time and post-authority reads through the same clock boundary.
    original = SqlAlchemyResourceAdmissionRepository.current_time

    def shifted(repo: SqlAlchemyResourceAdmissionRepository) -> datetime:
        return original(repo) + timedelta(seconds=seconds)

    monkeypatch.setattr(SqlAlchemyResourceAdmissionRepository, "current_time", shifted)


def never_started() -> ProcessExitEvidence:
    return ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)


def test_stop_requires_exact_process_exit_and_replay_cannot_reopen(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    prepared = admission.service.prepare_execution(actor, ticket)
    assert prepared.state == ExecutionState.PREPARED
    assert admission.service.prepare_execution(actor, ticket) == prepared
    child = ProcessIdentity(12345, b"p" * 32)
    started = admission.service.start_execution(actor, ticket, child)
    assert admission.service.start_execution(actor, ticket, child) == started
    assert admission.service.stop_execution(ticket).state == ExecutionState.STOPPING
    with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
        admission.service.renew_io(actor, ticket.permit)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
        admission.service.close_io(ticket.permit)
    proof = ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.confirm_execution_exit(
            ticket, replace(proof, child=ProcessIdentity(12345, b"q" * 32))
        )
    closed = admission.service.confirm_execution_exit(ticket, proof)
    assert closed.state == ExecutionState.CLOSED
    assert admission.service.confirm_execution_exit(ticket, proof) == closed
    with pytest.raises(ResourceAdmissionError, match="resource_execution_conflict"):
        admission.service.confirm_execution_exit(ticket, replace(proof, exit_code=1))
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.start_execution(actor, ticket, child)
    admission.service.close_io(ticket.permit)
    admission.service.close_io(ticket.permit)
    # A confirmed writer frees the single permit bound on this still-active lease.
    assert admission.service.open_io(actor, ticket.permit.fence, ticket.actual_target_id)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
def test_offline_restore_drain_closes_absent_resource_writer_with_supervisor_evidence(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    admission.service.start_execution(
        actor,
        ticket,
        ProcessIdentity(4_000_000_000, b"o" * 32),
    )

    report = PostgresOfflineExecutionDrain(
        admission.sessions,
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()

    assert report.resource_executions == report.checked_pids == 1
    assert admission.service.inspect_execution(ticket).state == ExecutionState.CLOSED
    with admission.sessions() as session:
        row = present(session.get(ResourceIoExecutionRow, ticket.execution_id))
        assert row.closure_kind == "SUPERVISOR_EXIT" and row.exit_code == 137


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
def test_offline_restore_drain_is_atomic_when_a_persisted_writer_is_still_alive(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    child = ProcessIdentity(os.getpid(), b"l" * 32)
    admission.service.prepare_execution(actor, ticket)
    admission.service.start_execution(actor, ticket, child)

    with pytest.raises(ResourceAdmissionError, match="offline_process_still_running"):
        PostgresOfflineExecutionDrain(
            admission.sessions,
            OfflineProcessEvidenceProbe(None),
        ).close_restored_reservations()

    execution = admission.service.inspect_execution(ticket)
    assert execution.state == ExecutionState.RUNNING
    assert execution.closed_at is None

    proof = ProcessExitEvidence(ExitKind.SUPERVISOR_EXIT, b"x" * 32, 137, child)
    admission.service.confirm_execution_exit(ticket, proof)
    admission.service.close_io(ticket.permit)


def test_expired_permit_keeps_transfer_request_bound_and_same_upload_writer_exclusion(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    first = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, first)
    second = upload_ticket(admission, actor, first.actual_target_id)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_busy"):
        admission.service.prepare_execution(actor, second)
    shift_clock(monkeypatch, 6)
    with pytest.raises(ResourceAdmissionError, match="resource_io_busy"):
        admission.service.open_io(actor, first.permit.fence, first.actual_target_id)
    # The second operation may get a fresh permit, but may never start another writer.
    second = replace(
        second,
        permit=admission.service.open_io(actor, second.permit.fence, second.actual_target_id),
    )
    with pytest.raises(ResourceAdmissionError, match="resource_execution_busy"):
        admission.service.prepare_execution(actor, second)
    admission.service.confirm_execution_exit(first, never_started())
    admission.service.close_io(first.permit)
    assert admission.service.prepare_execution(actor, second).state == ExecutionState.PREPARED


def test_crash_orphan_remains_charged_through_sweep_and_reacquire(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget(transfers=1)
    actor, other = admission.actor(), admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    shift_clock(monkeypatch, 60)
    # Recreate the service as after restart; it has no in-memory process ownership state.
    restarted = ResourceAdmissionService(
        SqlAlchemyResourceAdmissionUnitOfWorkFactory(admission.sessions)
    )
    assert restarted.sweep() >= 1
    assert restarted.inspect_execution(ticket).state == ExecutionState.ORPHANED
    with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
        restarted.confirm_execution_exit(ticket, never_started())
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        restarted.heartbeat_execution(replace(ticket, owner_run_id=uuid4()))
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        restarted.heartbeat_execution(ticket)
    with admission.sessions() as session:
        op = present(session.get(ResourceAdmissionRow, ticket.permit.fence.operation_id))
        request = ResourceRequest(
            op.operation_id, ResourceKind(op.kind), op.resource_type, op.resource_id, op.target_id
        )
    requeued = restarted.acquire(actor, request)
    assert requeued.operation.state == AdmissionState.WAITING
    assert requeued.usage.account == requeued.usage.device == requeued.usage.server == 1
    other_request = ResourceRequest(
        uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", uuid4(), admission.upload(other)
    )
    waiting = restarted.acquire(other, other_request)
    assert waiting.operation.state == AdmissionState.WAITING
    assert waiting.usage.server == 1
    proof = ProcessExitEvidence(ExitKind.SUPERVISOR_EXIT, b"s" * 32, 0)
    restarted.confirm_execution_exit(ticket, proof)
    restarted.close_io(ticket.permit)
    granted = restarted.poll(other, other_request.operation_id)
    assert granted.operation.state == AdmissionState.ACTIVE
    restarted.release(other, fence(granted))
    resumed = restarted.poll(actor, request.operation_id)
    assert fence(resumed).generation == ticket.permit.fence.generation + 1
    assert fence(resumed).activation_id != ticket.permit.fence.activation_id


def test_old_unclosed_rows_survive_terminal_retention_and_database_delete(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    admission.service.release(actor, ticket.permit.fence)
    shift_clock(monkeypatch, 9 * 86400)
    admission.service.sweep()
    with admission.sessions() as session:
        assert session.get(ResourceAdmissionRow, ticket.permit.fence.operation_id) is not None
        assert session.get(ResourceIoPermitRow, ticket.permit.permit_id) is not None
        assert session.get(ResourceIoExecutionRow, ticket.execution_id) is not None
    with (
        pytest.raises(DBAPIError, match="Cannot discard unclosed"),
        admission.sessions.begin() as session,
    ):
        session.execute(text("DELETE FROM account.resource_io_execution"))
    assert admission.service.poll(actor, ticket.permit.fence.operation_id).usage.server == 1
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.SUPERVISOR_EXIT, b"s" * 32, 0)
    )
    admission.service.sweep()
    with admission.sessions() as session:
        assert session.get(ResourceAdmissionRow, ticket.permit.fence.operation_id) is None


def test_expired_playback_executions_retain_outgoing_attachments(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    variants = [admission.variant(actor) for _ in range(3)]
    with admission.sessions() as session:
        recordings = [
            present(session.get(AudioVariantRow, variant)).recording_id for variant in variants
        ]
    active = admission.service.acquire(actor, play())
    admitted = fence(active)
    admission.service.attach(actor, admitted, 0, recordings[0], recordings[1])
    tickets = []
    for variant in variants[:2]:
        permit = admission.service.open_io(actor, admitted, variant, resource_type="PLAY_INSTANCE")
        ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.VAULT_STREAM, variant)
        admission.service.prepare_execution(actor, ticket)
        tickets.append(ticket)
    shift_clock(monkeypatch, 6)
    with pytest.raises(ResourceAdmissionError, match="resource_attachment_draining"):
        admission.service.attach(actor, admitted, 1, recordings[1], recordings[2])
    admission.service.confirm_execution_exit(tickets[0], never_started())
    admission.service.close_io(tickets[0].permit)
    assert (
        admission.service.attach(
            actor, admitted, 1, recordings[1], recordings[2]
        ).operation.attachment_revision
        == 2
    )


@pytest.mark.parametrize("field", ["actual_target_id", "kind", "owner_run_id", "permit_id"])
def test_registration_rejects_wrong_target_purpose_owner_or_permit(
    admission: AdmissionHarness,
    field: str,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    if field == "kind":
        changed = replace(ticket, kind=ExecutionKind.VAULT_STREAM)
    elif field == "permit_id":
        changed = replace(ticket, permit=replace(ticket.permit, permit_id=uuid4()))
    elif field == "actual_target_id":
        changed = replace(ticket, actual_target_id=uuid4())
    else:
        changed = replace(ticket, owner_run_id=uuid4())
    with pytest.raises(ResourceAdmissionError):
        admission.service.prepare_execution(actor, changed)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ResourceIoExecutionRow)) == 1


def test_revocation_cannot_start_or_release_unconfirmed_execution(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    with admission.sessions.begin() as session:
        present(session.get(DeviceRow, actor.device_id)).revoked_at = session.scalar(
            select(func.clock_timestamp())
        )
    with pytest.raises(ResourceAdmissionError):
        admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
    with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
        admission.service.close_io(ticket.permit)
    # Trusted owner can still acknowledge its never-started child after user revocation.
    admission.service.confirm_execution_exit(ticket, never_started())
    admission.service.close_io(ticket.permit)


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_exit_commit_is_reconciled_without_early_capacity_release(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
    committed: bool,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    original = SqlAlchemyResourceAdmissionUnitOfWork.commit

    def fail(unit: SqlAlchemyResourceAdmissionUnitOfWork) -> None:
        if committed:
            original(unit)
        raise DBAPIError("synthetic acknowledgement lost", None, RuntimeError("synthetic"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyResourceAdmissionUnitOfWork, "commit", fail)
        with pytest.raises(DBAPIError):
            admission.service.confirm_execution_exit(ticket, never_started())
    assert admission.service.inspect_execution(ticket).state == (
        ExecutionState.CLOSED if committed else ExecutionState.PREPARED
    )
    assert (
        admission.service.confirm_execution_exit(ticket, never_started()).state
        == ExecutionState.CLOSED
    )
    admission.service.close_io(ticket.permit)


def _process_usage(url: str, actor: Principal, operation_id: UUID) -> int:
    engine = create_engine(url)
    try:
        service = ResourceAdmissionService(
            SqlAlchemyResourceAdmissionUnitOfWorkFactory(sessionmaker(engine))
        )
        service.sweep()
        return service.poll(actor, operation_id).usage.server
    finally:
        engine.dispose()


def test_separate_process_observes_unconfirmed_charge_after_permit_expiry(
    admission: AdmissionHarness,
) -> None:
    admission.budget(transfers=1)
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    admission.service.release(actor, ticket.permit.fence)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_io_permit "
                "SET opened_at=clock_timestamp()-interval '8 seconds',"
                "renewed_at=clock_timestamp()-interval '6 seconds',"
                "expires_at=clock_timestamp()-interval '2 seconds'"
            )
        )
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        assert (
            pool.submit(
                _process_usage,
                admission.engine.url.render_as_string(hide_password=False),
                actor,
                ticket.permit.fence.operation_id,
            ).result(timeout=40)
            == 1
        )


def test_execution_evidence_blocks_downgrade(
    admission: AdmissionHarness,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    with pytest.raises(DBAPIError, match="Refusing to discard resource execution evidence"):
        database_harness.downgrade(database_name, "0035_resource_admission")


@pytest.mark.parametrize("transition", ["resume_stopping", "resume_orphan", "orphan_not_started"])
def test_database_rejects_backward_transitions_and_unverified_orphan_closure(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    if transition == "resume_stopping":
        admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
        admission.service.stop_execution(ticket)
        change = "state='RUNNING'"
    else:
        shift_clock(monkeypatch, 20)
        admission.service.sweep()
        assert admission.service.inspect_execution(ticket).state == ExecutionState.ORPHANED
        change = (
            "state='PREPARED'"
            if transition == "resume_orphan"
            else (
                "state='CLOSED',closed_at=clock_timestamp()+interval '20 seconds',"
                "closure_kind='NOT_STARTED',closure_evidence_sha256=decode(repeat('ab',32),'hex')"
            )
        )
    with (
        pytest.raises(DBAPIError, match="cannot resume or weaken"),
        admission.sessions.begin() as session,
    ):
        session.execute(text("UPDATE account.resource_io_execution SET " + change))


def test_cleanup_between_exit_confirmation_and_retry_has_explicit_absent_projection(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    # A missing execution alone is not a removed/confirmed permit.
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.confirm_execution_exit(ticket, never_started())
    admission.service.prepare_execution(actor, ticket)
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.inspect_execution(replace(ticket, owner_run_id=uuid4()))
    shift_clock(monkeypatch, 6)
    admission.service.confirm_execution_exit(ticket, never_started())
    admission.service.sweep()
    for result in (
        admission.service.inspect_execution(ticket),
        admission.service.confirm_execution_exit(ticket, never_started()),
    ):
        assert result.state == ExecutionState.ABSENT
        assert result.heartbeat_at is None
        assert result.closed_at is None
        assert result.child is None
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
    with pytest.raises(ResourceAdmissionError, match="resource_execution_stale"):
        admission.service.heartbeat_execution(ticket)


def test_absent_old_ticket_cannot_touch_a_new_activation(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    admission.service.confirm_execution_exit(ticket, never_started())
    with admission.sessions() as session:
        row = present(session.get(ResourceAdmissionRow, ticket.permit.fence.operation_id))
        request = ResourceRequest(
            row.operation_id,
            ResourceKind(row.kind),
            row.resource_type,
            row.resource_id,
            row.target_id,
        )
    shift_clock(monkeypatch, 35)
    active = admission.service.acquire(actor, request)
    assert fence(active).generation == ticket.permit.fence.generation + 1
    successor = replace(
        ticket,
        execution_id=uuid4(),
        owner_run_id=uuid4(),
        permit=admission.service.open_io(actor, fence(active), ticket.actual_target_id),
    )
    admission.service.prepare_execution(actor, successor)
    assert (
        admission.service.confirm_execution_exit(ticket, never_started()).state
        == ExecutionState.ABSENT
    )
    assert admission.service.inspect_execution(successor).state == ExecutionState.PREPARED
