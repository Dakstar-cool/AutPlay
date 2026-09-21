"""Real job recovery and real process trees cannot silently replace an ingest writer."""

import os
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess, VaultProcessSupervisor
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.ingest_execution import PostgresIngestExecutionRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import JobRow, UploadSessionRow, UserAccountRow
from autplay.adapters.postgresql.models.ingest_execution import IngestExecutionRow
from autplay.adapters.postgresql.models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoPermitRow,
)
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.resource_upload_guard import upload_has_unclosed_writer
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.job_worker import JobLeaseLost
from autplay.domain.ingest_execution import IngestExecutionStatus, IngestExecutionTicket
from autplay.domain.jobs import JobKey, TerminalJobError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import MediaValidationError, OpaqueStorageKey
from autplay.runtime.ingest_io import IngestProcessCoordinator, IngestWork
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from process_tree_support import (
    process_tree_factory,
    tree_child_launch,
    wait_marker,
    wait_tree_exit,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.unitofwork import UOWTransaction

from .conftest import DatabaseHarness
from .test_internet_ingest_authority import PublicationFixture, publication
from .test_resource_admission_runtime import admission, present
from .test_vault_ingest_fence import EVIDENCE, METADATA, IngestFixture, ingest

__all__ = ["admission", "ingest", "publication"]


def ticket_for(fixture: IngestFixture) -> IngestExecutionTicket:
    with fixture.sessions() as session:
        upload = present(session.get(UploadSessionRow, fixture.upload_id))
        return IngestExecutionTicket(
            uuid4(),
            uuid4(),
            fixture.upload_id,
            OpaqueStorageKey(upload.staging_key),
            fixture.lease.fence,
        )


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
@pytest.mark.usefixtures("internal_io_budget")
def test_offline_restore_drain_closes_absent_ingest_writer(
    ingest: IngestFixture,
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    repository.start(ticket, ProcessIdentity(4_000_000_000, b"o" * 32))

    report = PostgresOfflineExecutionDrain(
        ingest.sessions,
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()

    assert report.ingest_executions == report.checked_pids == 1
    status = repository.status(ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with ingest.sessions() as session:
        row = present(session.get(IngestExecutionRow, ticket.execution_id))
        assert row.closure_kind == "SUPERVISOR_EXIT" and row.exit_code == 137


def test_uncertain_prepare_absence_waits_for_original_transaction(
    ingest: IngestFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed, release = Event(), Event()
    waiting = Event()
    backend_pids: list[int] = []
    original: list[Future[IngestExecutionStatus]] = []

    def observed_lock(session: Session) -> None:
        if flushed.is_set():
            pid = session.scalar(select(func.pg_backend_pid()))
            assert isinstance(pid, int)
            backend_pids.append(pid)
            waiting.set()
        lock_resource_admission(session)

    monkeypatch.setattr(
        "autplay.adapters.postgresql.ingest_execution.lock_resource_admission", observed_lock
    )

    def hold_insert(session: Session, context: UOWTransaction) -> None:
        del context
        if any(isinstance(row, IngestExecutionRow) for row in session.new):
            flushed.set()
            assert release.wait(10)

    event.listen(Session, "after_flush", hold_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:

            class UncertainRepository(PostgresIngestExecutionRepository):
                def prepare(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus:
                    original.append(pool.submit(super().prepare, ticket))
                    assert flushed.wait(5)
                    raise SQLAlchemyError("synthetic uncertain COMMIT response")

            repository = UncertainRepository(ingest.sessions)
            coordinator = IngestProcessCoordinator(
                repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
            )
            ticket = ticket_for(ingest)
            future = pool.submit(coordinator.run, ticket, lambda work: None)
            try:
                assert flushed.wait(5)
                assert repository.status(ticket) is None
                assert waiting.wait(3)
                until = monotonic() + 3
                with ingest.sessions() as observer:
                    while not observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"),
                        {"pid": backend_pids[0]},
                    ):
                        assert monotonic() < until
                        sleep(0.01)
                assert coordinator.pending() == (ticket.execution_id,)
                assert not future.done()
            finally:
                release.set()
            with pytest.raises(ResourceAdmissionError, match="ingest_execution_unavailable"):
                future.result(timeout=5)
            original[0].result(timeout=5)
            assert not coordinator.pending()
            with ingest.sessions() as session:
                row = present(session.get(IngestExecutionRow, ticket.execution_id))
                assert row.state == "CLOSED" and row.closure_kind == "NOT_STARTED"
    finally:
        release.set()
        event.remove(Session, "after_flush", hold_insert)


def test_coordinator_verifies_bytes_with_durable_receipt_then_confirms_exit(
    ingest: IngestFixture, tmp_path: Path
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    ticket = ticket_for(ingest)

    def action(work: IngestWork) -> int:
        current = present(
            ingest.repository.start_ingest(
                ingest.upload_id, ticket.fence.job_id, fence=ticket.fence, execution=work.running
            )
        )
        assert work.storage.verify_staging(current.staging_key) == ingest.verified
        with ingest.sessions() as session:
            assert upload_has_unclosed_writer(session, ingest.upload_id)
        return ingest.verified.byte_size

    assert coordinator.run(ticket, action) == ingest.verified.byte_size
    assert present(repository.status(ticket)).state == ExecutionState.CLOSED
    with ingest.sessions() as session:
        assert not upload_has_unclosed_writer(session, ingest.upload_id)


def test_media_failure_retains_receipt_through_quarantine_transaction(
    ingest: IngestFixture, tmp_path: Path
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    ticket = ticket_for(ingest)

    def action(work: IngestWork) -> None:
        current = present(
            ingest.repository.start_ingest(
                ingest.upload_id, ticket.fence.job_id, fence=ticket.fence, execution=work.running
            )
        )
        work.storage.verify_staging(current.staging_key)
        try:
            work.storage.inspect(work.storage.staging_path_for_media(current.staging_key))
        except MediaValidationError as error:
            work.freeze_renewals()
            assert present(repository.status(ticket)).state == ExecutionState.RUNNING
            ingest.repository.quarantine(current, error.code)
            raise TerminalJobError(error.code) from error
        raise AssertionError("synthetic non-audio input unexpectedly decoded")

    with pytest.raises(TerminalJobError, match="media_validation_failed"):
        coordinator.run(ticket, action)
    assert present(repository.status(ticket)).state == ExecutionState.CLOSED
    with ingest.sessions() as session:
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "QUARANTINED"


def test_prepared_receipt_survives_recovery_and_blocks_legacy_and_new_writers(
    ingest: IngestFixture,
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    prepared = repository.prepare(ticket)
    assert prepared.state == ExecutionState.PREPARED and prepared.child is None
    assert repository.prepare(ticket) == prepared
    successor = ingest.replace_worker()
    replacement = replace(ticket, execution_id=uuid4(), owner_run_id=uuid4(), fence=successor.fence)
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
        ingest.start(successor)
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
        repository.prepare(replacement)
    with pytest.raises(JobLeaseLost):
        repository.start(ticket, ProcessIdentity(123, b"i" * 32))
    with ingest.sessions() as session:
        assert upload_has_unclosed_writer(session, ingest.upload_id)
        assert session.scalar(select(func.count()).select_from(ResourceAdmissionRow)) == 0
        assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0
    proof = ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
    closed = repository.confirm(ticket, proof)
    assert closed.state == ExecutionState.CLOSED and repository.confirm(ticket, proof) == closed
    assert repository.prepare(replacement).state == ExecutionState.PREPARED


@pytest.mark.parametrize("boundary", ["start", "prepare", "finalize", "quarantine"])
def test_registered_writer_is_required_at_every_metadata_boundary(
    ingest: IngestFixture, boundary: str
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    running = repository.start(ticket, ProcessIdentity(123, b"i" * 32))
    current = present(
        ingest.repository.start_ingest(
            ingest.upload_id,
            ticket.fence.job_id,
            fence=ticket.fence,
            execution=running,
        )
    )
    assert current.execution == running
    if boundary == "finalize":
        assert (
            ingest.repository.prepare_commit(current, ingest.verified, METADATA, EVIDENCE)
            == "PUBLISH"
        )
    legacy = replace(current, execution=None)
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
        match boundary:
            case "start":
                ingest.start(ingest.lease)
            case "prepare":
                ingest.repository.prepare_commit(legacy, ingest.verified, METADATA, EVIDENCE)
            case "finalize":
                ingest.repository.finalize_published(
                    legacy,
                    OpaqueStorageKey(ingest.verified.sha256.hex),
                    METADATA,
                    EVIDENCE,
                    reused=False,
                )
            case "quarantine":
                ingest.repository.quarantine(legacy, "vault.integrity_mismatch")
    repository.confirm(
        ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, running.child)
    )
    # An expected execution never falls back to legacy behavior after closure.
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_stale"):
        ingest.repository.prepare_commit(current, ingest.verified, METADATA, EVIDENCE)


def test_start_replay_and_renew_revalidate_cancel_but_ack_can_drain(ingest: IngestFixture) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity)
    renewed = repository.renew(ticket, identity)
    assert present(renewed.io_deadline_at) >= present(running.io_deadline_at)
    with ingest.sessions.begin() as session:
        present(session.get(JobRow, ticket.fence.job_id)).cancel_requested_at = datetime.now(UTC)
    for action in (repository.start, repository.renew):
        with pytest.raises(JobLeaseLost):
            action(ticket, identity)
    assert present(repository.status(ticket)).state == ExecutionState.RUNNING
    proof = ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -1, identity)
    assert repository.confirm(ticket, proof).state == ExecutionState.CLOSED
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_conflict"):
        repository.confirm(ticket, replace(proof, evidence_sha256=b"x" * 32))


def test_expired_io_deadline_cannot_renew_or_release_writer(ingest: IngestFixture) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity)
    sleep(5.05)
    for action in (repository.start, repository.renew):
        with pytest.raises(ResourceAdmissionError, match="ingest_execution_stale"):
            action(ticket, identity)
    assert repository.status(ticket) == running
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
        repository.prepare(replace(ticket, execution_id=uuid4()))
    assert (
        repository.confirm(
            ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, identity)
        ).state
        == ExecutionState.CLOSED
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE vault.ingest_execution SET owner_run_id=uuidv7()",
        "DELETE FROM vault.ingest_execution",
        "UPDATE vault.upload_session SET staging_key='replacement'",
        "UPDATE vault.upload_session SET expected_size=expected_size+1",
        "UPDATE vault.upload_session SET job_id=NULL",
        "UPDATE vault.upload_session SET state='SEALED'",
    ],
)
def test_sql_cannot_rewrite_registered_owner(ingest: IngestFixture, mutation: str) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    repository.prepare(ticket_for(ingest))
    with pytest.raises(DBAPIError), ingest.sessions.begin() as session:
        session.execute(text(mutation))


def test_downgrade_refuses_closed_ownership(
    ingest: IngestFixture, database_harness: DatabaseHarness, database_name: str
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    repository.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0046_upload_cleanup")


def test_actual_ingest_descendant_blocks_recovered_job_until_durable_ack(
    ingest: IngestFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = process_tree_factory()
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    supervisor = VaultProcessSupervisor[IngestExecutionTicket](maximum=1)
    child = supervisor.retain(
        ticket, ResourceIoDeadline(monotonic()), tree_factory=tree, launch=tree_child_launch
    )
    identity = None
    try:
        identity = child.spawn()
        running = repository.start(ticket, identity)
        child.allow_go(running)
        marker = tmp_path / "ingest-descendant"
        child.go({"marker": str(marker)})
        child.read_result()
        wait_marker(marker)
        assert child._process is not None and child._process.wait(timeout=5) == 0
        assert child.exit_evidence(identity) is None
        successor = ingest.replace_worker()
        replacement = replace(
            ticket, execution_id=uuid4(), owner_run_id=uuid4(), fence=successor.fence
        )
        with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
            repository.prepare(replacement)
        child.request_stop()
        proof = wait_tree_exit(child, identity)
        with monkeypatch.context() as patch:

            def unavailable(session: object) -> datetime:
                raise SQLAlchemyError("synthetic exit acknowledgement unavailable")

            patch.setattr(repository, "_now", unavailable)
            with pytest.raises(SQLAlchemyError):
                repository.confirm(ticket, proof)
        assert present(repository.status(ticket)).state == ExecutionState.RUNNING
        with pytest.raises(ResourceAdmissionError, match="resource_execution_unconfirmed"):
            supervisor.forget(child, running)
        with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
            repository.prepare(replacement)
        closed = repository.confirm(ticket, proof)
        supervisor.forget(child, closed)
        assert not supervisor.snapshot()
        assert repository.prepare(replacement).state == ExecutionState.PREPARED
    finally:
        child.request_stop()
        if supervisor.snapshot():
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()


def test_ingest_cannot_spawn_without_explicit_tree(ingest: IngestFixture) -> None:
    with pytest.raises(ResourceAdmissionError, match="resource_process_tree_unavailable"):
        RetainedVaultProcess(ticket_for(ingest), ResourceIoDeadline(monotonic()))


@pytest.mark.parametrize("expires", ["io", "job"])
@pytest.mark.parametrize("operation", ["start", "renew"])
def test_expiry_during_renew_cannot_resurrect_authority(
    ingest: IngestFixture,
    monkeypatch: pytest.MonkeyPatch,
    expires: str,
    operation: str,
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity)
    if expires == "job":
        with ingest.sessions.begin() as session:
            present(session.get(JobRow, ticket.fence.job_id)).lease_deadline = datetime.now(
                UTC
            ) + timedelta(seconds=1)
    original_now = repository._now
    refresh_calls = 0

    def delayed_now(session: Session) -> datetime:
        nonlocal refresh_calls
        refresh_calls += 1
        sleep(5.05 if expires == "io" else 1.05)
        return original_now(session)

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_now", delayed_now)
        with pytest.raises(ResourceAdmissionError if expires == "io" else JobLeaseLost):
            (repository.start if operation == "start" else repository.renew)(ticket, identity)
    assert refresh_calls == 1
    assert repository.status(ticket) == running


@pytest.mark.parametrize("phase", ["start", "renew"])
def test_revoked_source_stops_registered_work_but_allows_quarantine_and_exit(
    publication: PublicationFixture,
    phase: str,
) -> None:
    sessions = publication.harness.sessions
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="owned-internet-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
        key = present(session.get(UploadSessionRow, publication.upload_id)).staging_key
    ticket = IngestExecutionTicket(
        uuid4(), uuid4(), publication.upload_id, OpaqueStorageKey(key), lease.fence
    )
    repository = PostgresIngestExecutionRepository(sessions)
    repository.prepare(ticket)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity) if phase == "renew" else None
    ingest_repository = TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions))
    current = (
        None
        if running is None
        else ingest_repository.start_ingest(
            publication.upload_id,
            lease.fence.job_id,
            fence=lease.fence,
            execution=running,
        )
    )
    with sessions.begin() as session:
        lock_resource_admission(session)
        present(session.get(UserAccountRow, publication.actor.user_id)).authority_generation += 1
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_stale"):
        (repository.start if phase == "start" else repository.renew)(ticket, identity)
    if current is not None:
        ingest_repository.quarantine(current, "source_authorization_unavailable")
        with sessions() as session:
            assert (
                present(session.get(UploadSessionRow, publication.upload_id)).state == "QUARANTINED"
            )
    proof = (
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
        if running is None
        else ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -1, identity)
    )
    assert repository.confirm(ticket, proof).state == ExecutionState.CLOSED


def test_finalization_does_not_release_process_or_claim_staging_cleanup(
    ingest: IngestFixture,
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity)
    current = present(
        ingest.repository.start_ingest(
            ingest.upload_id,
            ticket.fence.job_id,
            fence=ticket.fence,
            execution=running,
        )
    )
    assert (
        ingest.repository.prepare_commit(current, ingest.verified, METADATA, EVIDENCE) == "PUBLISH"
    )
    committed = ingest.storage.commit_staging(current.staging_key, ingest.verified)
    assert ingest.repository.finalize_published(
        current, committed.storage_key, METADATA, EVIDENCE, reused=False
    )
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
        ingest.start(ingest.lease)
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_stale"):
        repository.renew(ticket, identity)
    assert present(repository.status(ticket)).state == ExecutionState.RUNNING
    assert (
        repository.confirm(
            ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, identity)
        ).state
        == ExecutionState.CLOSED
    )
    # Exit evidence does not mark filesystem cleanup complete.
    assert ingest.storage.verify_staging(current.staging_key) == ingest.verified


def test_expiry_immediately_before_sql_write_cannot_extend_deadline(
    ingest: IngestFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    with ingest.sessions.begin() as session:
        present(session.get(JobRow, ticket.fence.job_id)).lease_deadline = datetime.now(
            UTC
        ) + timedelta(seconds=3)
    identity = ProcessIdentity(123, b"i" * 32)
    running = repository.start(ticket, identity)
    with ingest.sessions.begin() as session:
        present(session.get(JobRow, ticket.fence.job_id)).lease_deadline = datetime.now(
            UTC
        ) + timedelta(minutes=2)
    original_flush = Session.flush
    entered = 0

    def delayed_flush(session: Session, objects: Sequence[object] | None = None) -> None:
        nonlocal entered
        if any(isinstance(row, IngestExecutionRow) for row in session.dirty):
            entered += 1
            # The old authorization expires, but the freshly computed 5-second
            # replacement remains live: post-flush checking alone cannot catch it.
            sleep(3.05)
            with session.no_autoflush:
                now = present(session.scalar(select(func.clock_timestamp())))
                row = next(row for row in session.dirty if isinstance(row, IngestExecutionRow))
                assert present(running.io_deadline_at) <= now < present(row.io_deadline_at)
        original_flush(session, objects)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", delayed_flush)
        with pytest.raises(ResourceAdmissionError, match="ingest_execution_stale"):
            repository.renew(ticket, identity)
    assert entered == 1 and repository.status(ticket) == running


pytestmark = pytest.mark.usefixtures("internal_io_budget")
