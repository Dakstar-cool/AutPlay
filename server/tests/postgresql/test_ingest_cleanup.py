"""Atomic finalization intent and exact, independently retained cleanup ownership."""

import hashlib
import io
import math
import os
import struct
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.ingest_cleanup import PostgresIngestCleanupRepository
from autplay.adapters.postgresql.ingest_execution import PostgresIngestExecutionRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import JobRow, UploadSessionRow, UserAccountRow
from autplay.adapters.postgresql.models.ingest_cleanup import (
    IngestCleanupClaimRow,
    IngestCleanupExecutionRow,
)
from autplay.adapters.postgresql.models.ingest_execution import IngestExecutionRow
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.resource_upload_guard import (
    require_upload_writer,
    upload_has_unclosed_writer,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.ingest_cleanup import IngestCleanupService
from autplay.application.job_worker import JobExecutionContext, JobResourceWait
from autplay.domain.ingest_cleanup import IngestCleanupClaim, IngestCleanupTicket
from autplay.domain.ingest_execution import IngestExecutionTicket
from autplay.domain.jobs import JobKey
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.ports.jobs import EnqueueJob
from autplay.runtime.controlled_ingest import ControlledVaultIngestHandler
from autplay.runtime.ingest_io import IngestCleanupCoordinator, IngestProcessCoordinator
from process_tree_support import process_tree_factory
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .conftest import DatabaseHarness
from .test_ingest_execution import ticket_for
from .test_internet_ingest_authority import PAYLOAD, PublicationFixture, publication
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_execution import upload_ticket
from .test_vault_ingest_fence import EVIDENCE, METADATA, IngestFixture, ingest

__all__ = ["admission", "ingest", "publication"]
IDENTITY = ProcessIdentity(123, b"i" * 32)


def finalized(
    fixture: IngestFixture, *, close: bool = True, exit_code: int = 0
) -> tuple[IngestExecutionTicket, IngestCleanupClaim]:
    work = PostgresIngestExecutionRepository(fixture.sessions)
    ticket = ticket_for(fixture)
    work.prepare(ticket)
    running = work.start(ticket, IDENTITY)
    current = present(
        fixture.repository.start_ingest(
            fixture.upload_id, ticket.fence.job_id, fence=ticket.fence, execution=running
        )
    )
    assert (
        fixture.repository.prepare_commit(current, fixture.verified, METADATA, EVIDENCE)
        == "PUBLISH"
    )
    committed = fixture.storage.commit_staging(current.staging_key, fixture.verified)
    assert fixture.repository.finalize_published(
        current, committed.storage_key, METADATA, EVIDENCE, reused=False
    )
    claim = present(PostgresIngestCleanupRepository(fixture.sessions).get(fixture.upload_id))
    if close:
        work.confirm(
            ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, exit_code, IDENTITY)
        )
    return ticket, claim


def cleanup_ticket(claim: IngestCleanupClaim) -> IngestCleanupTicket:
    return IngestCleanupTicket(uuid4(), uuid4(), claim.claim_id, claim.staging_key, claim.expected)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
def test_offline_restore_drain_closes_absent_ingest_cleanup_process(
    ingest: IngestFixture,
) -> None:
    _, claim = finalized(ingest)
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    ticket = cleanup_ticket(claim)
    repository.prepare(ticket)
    repository.start(ticket, ProcessIdentity(4_000_000_000, b"o" * 32))

    report = PostgresOfflineExecutionDrain(
        ingest.sessions,
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()

    assert report.ingest_cleanup_executions == report.checked_pids == 1
    status = repository.status(ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with ingest.sessions() as session:
        row = present(session.get(IngestCleanupExecutionRow, ticket.execution_id))
        assert row.closure_kind == "SUPERVISOR_EXIT" and row.exit_code == 137
    assert repository.get(claim.claim_id) is not None


def test_registered_finalization_queues_once_and_waits_for_exact_work_exit(
    ingest: IngestFixture,
) -> None:
    work, claim = finalized(ingest, close=False)
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    cleanup = cleanup_ticket(claim)
    assert repository.pending(maximum=1) == (ingest.upload_id,)
    assert repository.pending(maximum=1, after=ingest.upload_id) == ()
    with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_busy"):
        repository.prepare(cleanup)
    PostgresIngestExecutionRepository(ingest.sessions).confirm(
        work, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY)
    )
    assert repository.prepare(cleanup).state == ExecutionState.PREPARED
    with ingest.sessions() as session:
        row = present(session.get(IngestCleanupClaimRow, ingest.upload_id))
        assert (
            row.work_execution_id == work.execution_id
            and row.sha256 == ingest.verified.sha256.value
        )
        assert row.final_state == "COMMITTED" and row.completed_at is None


def test_finalization_and_cleanup_intent_roll_back_together(
    ingest: IngestFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PostgresVaultRuntime._finalize_published

    def fail(self: PostgresVaultRuntime, *args: object, **kwargs: object) -> bool:
        result = original(self, *args, **kwargs)  # type: ignore[arg-type]
        assert result and self._session.get(IngestCleanupClaimRow, ingest.upload_id) is not None
        raise SQLAlchemyError("synthetic final commit failure")

    monkeypatch.setattr(PostgresVaultRuntime, "_finalize_published", fail)
    with pytest.raises(SQLAlchemyError, match="synthetic"):
        finalized(ingest)
    with ingest.sessions() as session:
        assert session.get(IngestCleanupClaimRow, ingest.upload_id) is None
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "COMMIT_PREPARED"


@pytest.mark.parametrize(
    "mutation", ["claim_key", "claim_hash", "claim_work", "upload_state", "upload_cas", "delete"]
)
def test_sql_cannot_retarget_finalized_cleanup(ingest: IngestFixture, mutation: str) -> None:
    finalized(ingest)
    with pytest.raises(DBAPIError), ingest.sessions.begin() as session:
        claim = present(session.get(IngestCleanupClaimRow, ingest.upload_id))
        upload = present(session.get(UploadSessionRow, ingest.upload_id))
        if mutation == "claim_key":
            claim.staging_key = "replacement"
        elif mutation == "claim_hash":
            claim.sha256 = b"r" * 32
        elif mutation == "claim_work":
            claim.work_execution_id = uuid4()
        elif mutation == "upload_state":
            upload.state = "PROCESSING"
        elif mutation == "upload_cas":
            upload.computed_sha256 = b"r" * 32
        else:
            session.delete(claim)
        session.flush()


def test_actual_cleanup_ignores_expired_job_and_replays_lost_completion_reply(
    ingest: IngestFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, claim = finalized(ingest, exit_code=-9)
    with ingest.sessions.begin() as session:
        present(session.get(JobRow, ingest.lease.fence.job_id)).lease_deadline = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    coordinator = IngestCleanupCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    service = IngestCleanupService(repository, coordinator)
    original = repository.complete

    def lost_reply(target: IngestCleanupClaim, execution_id: UUID) -> None:
        original(target, execution_id)
        raise SQLAlchemyError("synthetic lost completion reply")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "complete", lost_reply)
        with pytest.raises(SQLAlchemyError):
            service.clean(ingest.upload_id)
    assert not (tmp_path / "staging" / claim.staging_key.value).exists()
    assert (
        ingest.storage.verify_object(OpaqueStorageKey(claim.expected.sha256.hex)) == claim.expected
    )

    def forbidden(ticket: IngestCleanupTicket) -> UUID:
        raise AssertionError("completed claim replay must not launch or touch files")

    monkeypatch.setattr(coordinator, "run", forbidden)
    assert service.clean(ingest.upload_id) and not coordinator.pending()
    assert repository.pending() == ()


def test_unclosed_cleanup_blocks_writers_and_old_success_completion(ingest: IngestFixture) -> None:
    _, claim = finalized(ingest)
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    first = cleanup_ticket(claim)
    repository.prepare(first)
    repository.start(first, IDENTITY)
    repository.confirm(first, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY))
    second = replace(first, execution_id=uuid4(), owner_run_id=uuid4())
    repository.prepare(second)
    with ingest.sessions() as session:
        assert upload_has_unclosed_writer(session, ingest.upload_id)
        with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_busy"):
            require_upload_writer(session, ingest.upload_id)
    with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_busy"):
        repository.complete(claim, first.execution_id)
    repository.confirm(second, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    repository.complete(claim, first.execution_id)
    repository.complete(claim, first.execution_id)


def test_cleanup_expiry_never_releases_ownership_or_accepts_late_renewal(
    ingest: IngestFixture,
) -> None:
    _, claim = finalized(ingest)
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    ticket = cleanup_ticket(claim)
    repository.prepare(ticket)
    repository.start(ticket, IDENTITY)
    sleep(5.05)
    with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_stale"):
        repository.renew(ticket, IDENTITY)
    with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_busy"):
        repository.prepare(replace(ticket, execution_id=uuid4()))
    repository.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY))
    with pytest.raises(ResourceAdmissionError, match="ingest_cleanup_unconfirmed"):
        repository.complete(claim, ticket.execution_id)


def test_legacy_finalization_is_not_backfilled_from_terminal_metadata(
    ingest: IngestFixture,
) -> None:
    current = ingest.start(ingest.lease)
    ingest.repository.prepare_commit(current, ingest.verified, METADATA, EVIDENCE)
    committed = ingest.storage.commit_staging(current.staging_key, ingest.verified)
    assert ingest.repository.finalize_published(
        current, committed.storage_key, METADATA, EVIDENCE, reused=False
    )
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    assert repository.get(ingest.upload_id) is None and repository.pending() == ()


def test_waiting_for_registered_writer_preserves_retry_budget(ingest: IngestFixture) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    successor = ingest.replace_worker()
    with pytest.raises(JobResourceWait):
        repository.defer(successor.fence, ingest.upload_id, ())
    with ingest.sessions() as session:
        job = present(session.get(JobRow, successor.fence.job_id))
        assert job.state == "RETRY_WAIT" and job.resource_waiting and job.resource_wait_count == 1
    assert present(repository.status(ticket)).state == ExecutionState.PREPARED


def test_controlled_handler_defers_for_unclosed_http_upload(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget()
    actor = admission.actor()
    upload = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, upload)
    with admission.sessions.begin() as session:
        job = PostgresJobRepository(session).enqueue(
            EnqueueJob(
                key=JobKey("vault.ingest", 1),
                user_id=actor.user_id,
                payload={"upload_session_id": str(upload.actual_target_id)},
            )
        )
        row = present(session.get(UploadSessionRow, upload.actual_target_id))
        row.state, row.job_id, row.received_size = "SEALED", job.job_id, row.expected_size
        row.sealed_at = datetime.now(UTC)
    with admission.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="waiting-for-upload",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    work_repo = PostgresIngestExecutionRepository(admission.sessions)
    cleanup_repo = PostgresIngestCleanupRepository(admission.sessions)
    settings = IngestChildSettings(tmp_path)
    work = IngestProcessCoordinator(work_repo, settings, tree_factory=process_tree_factory())
    cleanup = IngestCleanupCoordinator(cleanup_repo, settings, tree_factory=process_tree_factory())
    handler = ControlledVaultIngestHandler(
        work_repo,
        work,
        TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(admission.sessions)),
        IngestCleanupService(cleanup_repo, cleanup),
        cleanup_pending=cleanup.pending,
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
        fence=lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    with pytest.raises(JobResourceWait):
        handler(context, lease)
    with admission.sessions() as session:
        deferred_job = present(session.get(JobRow, lease.fence.job_id))
        assert deferred_job.state == "RETRY_WAIT" and deferred_job.resource_waiting
        assert deferred_job.attempt_count == deferred_job.resource_wait_count == 1
        assert session.scalar(select(func.count()).select_from(IngestExecutionRow)) == 0
    assert admission.service.inspect_execution(upload).state == ExecutionState.PREPARED
    assert not work.pending() and not cleanup.pending()


def test_combined_full_registries_can_defer_without_losing_retry_budget(
    ingest: IngestFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = PostgresIngestExecutionRepository(ingest.sessions)
    ticket = ticket_for(ingest)
    repository.prepare(ticket)
    successor = ingest.replace_worker()
    cleanup_repo = PostgresIngestCleanupRepository(ingest.sessions)
    settings = IngestChildSettings(tmp_path)
    work = IngestProcessCoordinator(
        repository, settings, tree_factory=process_tree_factory(), maximum=100
    )
    cleanup = IngestCleanupCoordinator(
        cleanup_repo, settings, tree_factory=process_tree_factory(), maximum=100
    )
    work_ids = (ticket.execution_id, *(uuid4() for _ in range(99)))
    cleanup_ids = tuple(uuid4() for _ in range(100))
    monkeypatch.setattr(work, "pending", lambda: work_ids)
    monkeypatch.setattr(cleanup, "pending", lambda: cleanup_ids)
    handler = ControlledVaultIngestHandler(
        repository,
        work,
        ingest.repository,
        IngestCleanupService(cleanup_repo, cleanup),
        cleanup_pending=cleanup.pending,
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(ingest.sessions),
        fence=successor.fence,
        lease_interval=timedelta(minutes=2),
    )
    with pytest.raises(JobResourceWait):
        handler(context, successor)
    with ingest.sessions() as session:
        row = present(session.get(JobRow, successor.fence.job_id))
        assert row.resource_waiting and row.resource_wait_count == 1


def test_downgrade_preserves_claim_history(
    ingest: IngestFixture, database_harness: DatabaseHarness
) -> None:
    finalized(ingest)
    database_name = str(ingest.sessions.kw["bind"].url.database)
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0047_ingest_execution")


def test_source_revocation_after_finalization_does_not_revoke_cleanup(
    publication: PublicationFixture, tmp_path: Path
) -> None:
    sessions = publication.harness.sessions
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="ingest-cleanup-internet",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
        key = OpaqueStorageKey(
            present(session.get(UploadSessionRow, publication.upload_id)).staging_key
        )
    storage = FilesystemVaultStorage(tmp_path, limits=VaultLimits())
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    fixture = IngestFixture(
        sessions,
        TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions)),
        storage,
        publication.upload_id,
        lease,
        storage.verify_staging(key),
    )
    finalized(fixture)
    with sessions.begin() as session:
        present(session.get(UserAccountRow, publication.actor.user_id)).authority_generation += 1
    repository = PostgresIngestCleanupRepository(sessions)
    coordinator = IngestCleanupCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    assert IngestCleanupService(repository, coordinator).clean(publication.upload_id)
    assert not (tmp_path / "staging" / key.value).exists()


@pytest.mark.skipif(os.name == "nt", reason="real pinned media path runs in Linux proof image")
def test_controlled_handler_publishes_then_cleans_in_two_distinct_owned_processes(
    ingest: IngestFixture, tmp_path: Path
) -> None:
    source = io.BytesIO()
    with wave.open(source, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(16000 * math.sin(index * 440 * 2 * math.pi / 22050)))
                for index in range(22050 * 12)
            )
        )
    payload = source.getvalue()
    digest = hashlib.sha256(payload).digest()
    with ingest.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, ingest.upload_id))
        upload.expected_size = upload.received_size = len(payload)
        upload.chunk_size = len(payload)
        upload.declared_sha256 = digest
        key = upload.staging_key
    (tmp_path / "staging" / key).write_bytes(payload)
    work_repo, cleanup_repo = (
        PostgresIngestExecutionRepository(ingest.sessions),
        PostgresIngestCleanupRepository(ingest.sessions),
    )
    work = IngestProcessCoordinator(
        work_repo, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    cleanup = IngestCleanupCoordinator(
        cleanup_repo, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    handler = ControlledVaultIngestHandler(
        work_repo,
        work,
        ingest.repository,
        IngestCleanupService(cleanup_repo, cleanup),
        cleanup_pending=cleanup.pending,
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(ingest.sessions),
        fence=ingest.lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    handler(context, ingest.lease)
    handler(context, ingest.lease)
    with ingest.sessions() as session:
        assert present(session.get(UploadSessionRow, ingest.upload_id)).state == "COMMITTED"
        claim = present(session.get(IngestCleanupClaimRow, ingest.upload_id))
        assert (
            claim.completed_at is not None
            and claim.completed_execution_id != claim.work_execution_id
        )
        assert session.scalar(select(func.count()).select_from(IngestExecutionRow)) == 1
        assert session.scalar(select(func.count()).select_from(IngestCleanupExecutionRow)) == 1
    assert (
        not (tmp_path / "staging" / key).exists() and not work.pending() and not cleanup.pending()
    )


pytestmark = pytest.mark.usefixtures("internal_io_budget")
