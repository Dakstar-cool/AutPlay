"""Explicit missing outcomes release digest claims only after exact process closure."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import OrphanObjectClaimRow, ProviderMaintenanceRow
from autplay.adapters.postgresql.orphan_object_retirement import PostgresOrphanObjectRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.application.orphan_object_retirement import (
    OrphanObjectClaim,
    OrphanObjectOutcome,
    OrphanObjectRetirementService,
)
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey, StorageOperationError
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from test_orphan_object_retirement import PAYLOAD

from .conftest import DatabaseHarness, prepare_adjacent_downgrade
from .test_discovery_authority_clock import _blocked_by
from .test_internet_vault_publication import EVIDENCE, METADATA
from .test_orphan_object_retirement import Draft, draft, ingest_repository, ticket_for
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission", "draft"]


def missing_ticket(claim: OrphanObjectClaim) -> MaintenanceTicket:
    return replace(ticket_for(claim), action=MaintenanceAction.ORPHAN_MISSING)


def test_actual_absence_resolution_allows_publication_and_old_replay_is_inert(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    service = OrphanObjectRetirementService(claims, storage)
    # A scan observed the source before a different claim retired it.
    assert service.retire(draft.claim.storage_key, draft.claim.claim_id)
    claim = present(service.claim(draft.claim.storage_key, uuid4()))
    assert not claim.completed and claim.claim_id != draft.claim.claim_id
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_storage_failed"):
            service.retire(claim.storage_key, claim.claim_id)
        with pytest.raises(StorageOperationError):
            ingest_repository(admission).prepare_commit(
                draft.ingest, draft.verified, METADATA, EVIDENCE
            )
        assert service.resolve_missing(claim.storage_key, claim.claim_id)
        receipt = present(service.claim(claim.storage_key, claim.claim_id))
        assert receipt.completed and receipt.outcome == OrphanObjectOutcome.MISSING
        assert not claims.pending()
        with admission.sessions() as session:
            row = present(session.get(OrphanObjectClaimRow, claim.claim_id))
            run = present(session.get(ProviderMaintenanceRow, row.completed_execution_id))
            assert run.action == "ORPHAN_MISSING" and run.exit_code == 0
            assert run.state == "CLOSED" and run.closure_kind == "PROCESS_EXIT"
            assert run.child_pid is not None
            assert present(row.completed_at) >= present(run.closed_at)
        ingest = ingest_repository(admission)
        assert ingest.prepare_commit(draft.ingest, draft.verified, METADATA, EVIDENCE) == "PUBLISH"
        published = draft.storage.commit_staging(draft.ingest.staging_key, draft.verified)
        assert ingest.finalize_published(
            draft.ingest, published.storage_key, METADATA, EVIDENCE, reused=False
        )
        assert service.resolve_missing(claim.storage_key, claim.claim_id)
        assert not service.retire(claim.storage_key, claim.claim_id)
        assert not service.resolve_missing(draft.claim.storage_key, draft.claim.claim_id)
        with pytest.raises(ResourceAdmissionError, match="maintenance_claim_stale"):
            storage.confirm_orphan_missing(claim)
        assert draft.source.read_bytes() == PAYLOAD
        assert (tmp_path / "quarantine" / draft.claim.quarantine_key.value).read_bytes() == PAYLOAD
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 3
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("commit_before_error", [False, True])
def test_lost_resolution_reply_replays_canonical_joined_claim(
    admission: AdmissionHarness,
    draft: Draft,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit_before_error: bool,
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    service = OrphanObjectRetirementService(claims, storage)
    original_claim = present(service.claim(draft.claim.storage_key, draft.claim.claim_id))
    requested_id = uuid4()
    canonical = present(service.claim(draft.claim.storage_key, requested_id))
    assert canonical == original_claim and canonical.claim_id != requested_id
    draft.source.unlink()
    complete = claims.resolve_missing

    def lost(claim: OrphanObjectClaim, execution_id: UUID) -> None:
        if commit_before_error:
            complete(claim, execution_id)
        raise SQLAlchemyError("synthetic lost absence completion reply")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(claims, "resolve_missing", lost)
            with pytest.raises(SQLAlchemyError):
                service.resolve_missing(canonical.storage_key, canonical.claim_id)
        assert claims.pending() == (() if commit_before_error else (canonical,))
        assert service.resolve_missing(canonical.storage_key, canonical.claim_id)
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(OrphanObjectClaimRow)) == 1
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == (
                1 if commit_before_error else 2
            )
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("closure", ["PREPARED", "NOT_STARTED", "FAILED"])
def test_missing_resolution_rejects_unconfirmed_exit_in_repository_and_trigger(
    admission: AdmissionHarness, closure: str
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(OpaqueStorageKey("a" * 64), uuid4()))
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    ticket = missing_ticket(claim)
    repository.prepare(ticket)
    if closure == "NOT_STARTED":
        repository.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    elif closure == "FAILED":
        child = ProcessIdentity(12345, b"i" * 32)
        repository.start(ticket, child)
        repository.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 1, child))
    with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
        claims.resolve_missing(claim, ticket.execution_id)
    with (
        admission.sessions.begin() as session,
        pytest.raises(IntegrityError, match="Orphan exit is unconfirmed"),
    ):
        row = present(session.get(OrphanObjectClaimRow, claim.claim_id))
        row.completed_at = session.scalar(select(func.clock_timestamp()))
        row.completed_execution_id = ticket.execution_id
        session.flush()
    assert claims.pending() == (claim,)


@pytest.mark.parametrize(
    "action", [MaintenanceAction.ORPHAN_OBJECT, MaintenanceAction.ORPHAN_MISSING]
)
def test_cross_action_completion_is_rejected_before_and_after_completion(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, action: MaintenanceAction
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(draft.claim.storage_key, draft.claim.claim_id))
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        if action == MaintenanceAction.ORPHAN_MISSING:
            draft.source.unlink()
            execution = storage.confirm_orphan_missing(claim)
            correct, wrong = claims.resolve_missing, claims.complete
        else:
            execution = storage.retire_orphan_object(claim)
            correct, wrong = claims.complete, claims.resolve_missing
        with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
            wrong(claim, execution)
        correct(claim, execution)
        correct(claim, execution)
        with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
            wrong(claim, execution)
    finally:
        assert not storage.shutdown()


def test_pending_retirement_retry_blocks_absence_completion(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(draft.claim.storage_key, draft.claim.claim_id))
    draft.source.unlink()
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    try:
        finished = storage.confirm_orphan_missing(claim)
        second = ticket_for(claim)
        repository.prepare(second)
        with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
            claims.resolve_missing(claim, finished)
        assert claims.pending() == (claim,)
        repository.confirm(second, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
        claims.resolve_missing(claim, finished)
        assert not claims.pending()
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("present_path", ["source", "destination"])
def test_actual_child_rejects_present_bytes_and_keeps_claim(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, present_path: str
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    if present_path == "destination":
        draft.source.rename(tmp_path / "quarantine" / draft.claim.quarantine_key.value)
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_storage_failed"):
            OrphanObjectRetirementService(claims, storage).resolve_missing(
                draft.claim.storage_key, draft.claim.claim_id
            )
        assert claims.pending() == (draft.claim,)
        with admission.sessions() as session:
            run = present(session.scalar(select(ProviderMaintenanceRow)))
            assert run.state == "CLOSED" and run.exit_code != 0
    finally:
        assert not storage.shutdown()


def test_delayed_exit_ack_keeps_claim_and_global_slot(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(draft.claim.storage_key, draft.claim.claim_id))
    draft.source.unlink()
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    entered, release = Event(), Event()
    confirm = repository.confirm

    def delayed(ticket: MaintenanceTicket, proof: ProcessExitEvidence) -> MaintenanceStatus:
        assert proof.kind == ExitKind.PROCESS_EXIT and proof.exit_code == 0
        entered.set()
        assert release.wait(4)
        return confirm(ticket, proof)

    monkeypatch.setattr(repository, "confirm", delayed)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    service = OrphanObjectRetirementService(claims, storage)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.resolve_missing, claim.storage_key, claim.claim_id)
            assert entered.wait(3)
            assert not future.done() and storage.pending() and claims.pending() == (claim,)
            with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                repository.prepare(missing_ticket(claim))
            with pytest.raises(StorageOperationError):
                ingest_repository(admission).prepare_commit(
                    draft.ingest, draft.verified, METADATA, EVIDENCE
                )
            release.set()
            assert future.result(timeout=3)
    finally:
        release.set()
        assert not storage.shutdown()


def test_downgrade_waits_for_uncommitted_missing_run_and_preserves_history(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    prepare_adjacent_downgrade(database_harness, database_name, "0045_orphan_missing")
    claim = present(
        PostgresOrphanObjectRepository(admission.sessions).claim(
            OpaqueStorageKey("a" * 64), uuid4()
        )
    )
    ticket = missing_ticket(claim)
    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions() as blocker:
        blocker.execute(
            text(
                """INSERT INTO vault.provider_maintenance(
                execution_id,owner_run_id,provider_execution_id,claim_id,orphan_claim_id,
                storage_key,action,singleton_id,state,created_at)
                VALUES (:execution,:owner,NULL,:claim,:claim,:storage,
                'ORPHAN_MISSING',1,'PREPARED',clock_timestamp())"""
            ),
            {
                "execution": ticket.execution_id,
                "owner": ticket.owner_run_id,
                "claim": claim.claim_id,
                "storage": claim.storage_key.value,
            },
        )
        blocker.flush()
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        future = pool.submit(
            database_harness.downgrade, database_name, "0044_inventory_maintenance"
        )
        try:
            with admission.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert not future.done() and monotonic() < until
                    sleep(0.005)
            blocker.commit()
        finally:
            blocker.rollback()
        with pytest.raises(DBAPIError, match="Refusing to discard"):
            future.result(timeout=10)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                """UPDATE vault.provider_maintenance SET state='CLOSED',closed_at=clock_timestamp(),
                closure_kind='NOT_STARTED',closure_evidence_sha256=:evidence
                WHERE execution_id=:execution"""
            ),
            {"execution": ticket.execution_id, "evidence": b"n" * 32},
        )
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0044_inventory_maintenance")
    with database_harness.connect(database_name) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0045_orphan_missing",
        )


pytestmark = pytest.mark.usefixtures("internal_io_budget")
