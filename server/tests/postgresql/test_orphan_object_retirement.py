"""Real PostgreSQL publication races and retained orphan CAS maintenance children."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.models import (
    OrphanObjectClaimRow,
    ProviderMaintenanceRow,
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.orphan_object_retirement import PostgresOrphanObjectRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.resource_limits import RESOURCE_ADMISSION_LOCK
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.orphan_object_retirement import (
    OrphanObjectClaim,
    OrphanObjectRetirementService,
)
from autplay.application.vault_ingest import IngestSession
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.domain.vault import (
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    VerifiedStagedFile,
)
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from psycopg import Connection
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from test_orphan_object_retirement import PAYLOAD, orphan_file

from .conftest import DatabaseHarness, prepare_adjacent_downgrade
from .test_discovery_authority_clock import _blocked_by
from .test_internet_vault_publication import EVIDENCE, METADATA
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_vault_runtime import _seed_processing_uploads

__all__ = ["admission"]


@dataclass
class Draft:
    claim: OrphanObjectClaim
    source: Path
    storage: FilesystemVaultStorage
    ingest: IngestSession
    verified: VerifiedStagedFile


@pytest.fixture
def draft(database_connection: Connection[Any], tmp_path: Path) -> Draft:
    claim, source = orphan_file(tmp_path)
    recording, uploads = _seed_processing_uploads(
        database_connection,
        count=1,
        expected_size=len(PAYLOAD),
        declared_sha256=bytes.fromhex(claim.storage_key.value),
    )
    database_connection.commit()
    upload, _, staged = uploads[0]
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey(staged)
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(bytes.fromhex(claim.storage_key.value)),
    )
    ingest = IngestSession(
        upload, recording, key, len(PAYLOAD), bytes.fromhex(claim.storage_key.value)
    )
    return Draft(claim, source, storage, ingest, storage.verify_staging(key))


def ingest_repository(harness: AdmissionHarness) -> TransactionalIngestRepository:
    return TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(harness.sessions))


def ticket_for(claim: OrphanObjectClaim) -> MaintenanceTicket:
    return MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.ORPHAN_OBJECT, claim.storage_key
    )


def test_claim_wins_publication_and_actual_child_ack_allows_republication(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(draft.claim.storage_key, draft.claim.claim_id))
    ingest = ingest_repository(admission)
    with pytest.raises(StorageOperationError):
        ingest.prepare_commit(draft.ingest, draft.verified, METADATA, EVIDENCE)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(VaultObjectRow)) == 0
        assert (
            present(session.get(UploadSessionRow, draft.ingest.upload_session_id)).state
            == "PROCESSING"
        )
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    original = repository.start

    def start(ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        result = original(ticket, child)
        # Launch is already registered; no authority or CAS transaction spans the FS action.
        with admission.sessions.begin() as session:
            assert session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RESOURCE_ADMISSION_LOCK}
            )
            assert session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": int.from_bytes(draft.verified.sha256.value[:8], "big", signed=True)},
            )
        assert draft.source.exists()
        return result

    monkeypatch.setattr(repository, "start", start)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    service = OrphanObjectRetirementService(claims, storage)
    try:
        assert service.retire(claim.storage_key, claim.claim_id)
        assert not draft.source.exists()
        with admission.sessions() as session:
            row = present(session.get(OrphanObjectClaimRow, claim.claim_id))
            run = present(session.get(ProviderMaintenanceRow, row.completed_execution_id))
            assert run.state == "CLOSED" and run.exit_code == 0 and run.child_pid is not None
            assert present(row.completed_at) >= present(run.closed_at)
            finished = run.execution_id
        other = present(claims.claim(OpaqueStorageKey("b" * 64), uuid4()))
        with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
            claims.complete(other, finished)
        with (
            admission.sessions.begin() as session,
            pytest.raises(IntegrityError, match="Orphan ownership is immutable"),
        ):
            completed = present(session.get(OrphanObjectClaimRow, claim.claim_id))
            completed.completed_at, completed.completed_execution_id = None, None
            session.flush()
        assert ingest.prepare_commit(draft.ingest, draft.verified, METADATA, EVIDENCE) == "PUBLISH"
        published = draft.storage.commit_staging(draft.ingest.staging_key, draft.verified)
        assert ingest.finalize_published(
            draft.ingest, published.storage_key, METADATA, EVIDENCE, reused=False
        )
        assert service.retire(claim.storage_key, claim.claim_id)
        # A caller holding the old, unfinished receipt cannot move the newly published inode.
        with pytest.raises(ResourceAdmissionError, match="maintenance_claim_stale"):
            storage.retire_orphan_object(claim)
        assert draft.source.read_bytes() == PAYLOAD
        assert (tmp_path / "quarantine" / claim.quarantine_key.value).read_bytes() == PAYLOAD
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 1
    finally:
        assert not storage.shutdown()


def test_uncommitted_publisher_wins_claim_after_actual_lock_wait(
    admission: AdmissionHarness, draft: Draft
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions() as blocker:
        assert (
            PostgresVaultRuntime(blocker).prepare_commit(
                draft.ingest, draft.verified, METADATA, EVIDENCE
            )
            == "PUBLISH"
        )
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        future = pool.submit(claims.claim, draft.claim.storage_key, draft.claim.claim_id)
        try:
            with admission.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert not future.done() and monotonic() < until
                    sleep(0.005)
            blocker.commit()
        finally:
            blocker.rollback()
        assert future.result(timeout=5) is None
    assert draft.source.read_bytes() == PAYLOAD


@pytest.mark.parametrize("state", ["STAGING", "QUARANTINED", "COMMITTED"])
def test_any_object_metadata_protects_bytes_without_replica(
    admission: AdmissionHarness, draft: Draft, state: str
) -> None:
    assert (
        ingest_repository(admission).prepare_commit(
            draft.ingest, draft.verified, METADATA, EVIDENCE
        )
        == "PUBLISH"
    )
    with admission.sessions.begin() as session:
        session.execute(delete(VaultReplicaRow))
        obj = present(session.scalar(select(VaultObjectRow)))
        obj.commit_status = state
        if state == "COMMITTED":
            obj.committed_at = session.scalar(select(func.clock_timestamp()))
        session.execute(
            text(
                "UPDATE vault.upload_session SET "
                "created_at=now()-interval '2 hours', "
                "expires_at=now()-interval '1 hour'"
            )
        )
        session.execute(text("UPDATE jobs.job SET state='FAILED'"))
    assert (
        PostgresOrphanObjectRepository(admission.sessions).claim(draft.claim.storage_key, uuid4())
        is None
    )
    assert draft.source.read_bytes() == PAYLOAD


def test_replica_key_is_protected_even_when_object_digest_differs(
    admission: AdmissionHarness, draft: Draft
) -> None:
    with admission.sessions.begin() as session:
        obj = VaultObjectRow(
            sha256=b"x" * 32, byte_size=1, detected_mime_type="audio/flac", commit_status="STAGING"
        )
        session.add(obj)
        session.flush()
        session.add(
            VaultReplicaRow(
                vault_object_id=obj.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=draft.claim.storage_key.value,
                replica_status="COPYING",
            )
        )
    assert (
        PostgresOrphanObjectRepository(admission.sessions).claim(draft.claim.storage_key, uuid4())
        is None
    )


@pytest.mark.parametrize("closure", ["PREPARED", "NOT_STARTED", "FAILED"])
def test_completion_requires_exact_successful_child_exit(
    admission: AdmissionHarness, closure: str
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(OpaqueStorageKey("a" * 64), uuid4()))
    ticket = ticket_for(claim)
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    repository.prepare(ticket)
    if closure == "NOT_STARTED":
        repository.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    elif closure == "FAILED":
        child = ProcessIdentity(12345, b"i" * 32)
        repository.start(ticket, child)
        repository.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 1, child))
    with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
        claims.complete(claim, ticket.execution_id)
    with (
        admission.sessions.begin() as session,
        pytest.raises(IntegrityError, match="Orphan exit is unconfirmed"),
    ):
        row = present(session.get(OrphanObjectClaimRow, claim.claim_id))
        row.completed_at = session.scalar(select(func.clock_timestamp()))
        row.completed_execution_id = ticket.execution_id
        session.flush()
    assert claims.pending() == (claim,)


def test_second_prepared_retry_prevents_first_completion_releasing_publisher(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(draft.claim.storage_key, draft.claim.claim_id))
    repository = PostgresProviderMaintenanceRepository(admission.sessions)
    storage = ProcessProviderMaintenanceStorage(repository, tmp_path)
    try:
        finished = storage.retire_orphan_object(claim)
        second = ticket_for(claim)
        repository.prepare(second)
        with pytest.raises(ResourceAdmissionError, match="orphan_object_execution_unconfirmed"):
            claims.complete(claim, finished)
        with pytest.raises(StorageOperationError):
            ingest_repository(admission).prepare_commit(
                draft.ingest, draft.verified, METADATA, EVIDENCE
            )
        repository.confirm(second, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
        claims.complete(claim, finished)
        with pytest.raises(ResourceAdmissionError, match="maintenance_claim_stale"):
            repository.prepare(ticket_for(claim))
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("commit_before_error", [False, True])
def test_child_failure_and_completion_loss_keep_retries_safe(
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
    destination = tmp_path / "quarantine" / draft.claim.quarantine_key.value
    destination.write_bytes(b"collision")
    try:
        with pytest.raises(ResourceAdmissionError, match="maintenance_storage_failed"):
            service.retire(draft.claim.storage_key, draft.claim.claim_id)
        assert claims.pending() == (draft.claim,)
        assert draft.source.read_bytes() == PAYLOAD
        destination.unlink()
        original = claims.complete

        def fail_complete(claim: OrphanObjectClaim, execution_id: UUID) -> None:
            if commit_before_error:
                original(claim, execution_id)
            raise SQLAlchemyError("synthetic completion transaction unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(claims, "complete", fail_complete)
            with pytest.raises(SQLAlchemyError):
                service.retire(draft.claim.storage_key, draft.claim.claim_id)
        assert claims.complete == original
        assert claims.pending() == (() if commit_before_error else (draft.claim,))
        assert not draft.source.exists() and destination.read_bytes() == PAYLOAD
        if not commit_before_error:
            with pytest.raises(StorageOperationError):
                ingest_repository(admission).prepare_commit(
                    draft.ingest, draft.verified, METADATA, EVIDENCE
                )
        with admission.sessions() as session:
            runs_before = present(
                session.scalar(select(func.count()).select_from(ProviderMaintenanceRow))
            )
        assert service.retire(draft.claim.storage_key, draft.claim.claim_id)
        assert not claims.pending()
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == (
                runs_before + (0 if commit_before_error else 1)
            )
    finally:
        assert not storage.shutdown()


def test_identity_pending_bounds_and_downgrade_preserve_durable_claim(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    claims = PostgresOrphanObjectRepository(admission.sessions)
    claim = present(claims.claim(OpaqueStorageKey("a" * 64), uuid4()))
    assert claims.claim(claim.storage_key, uuid4()) == claim
    with pytest.raises(ResourceAdmissionError, match="orphan_object_claim_conflict"):
        claims.claim(OpaqueStorageKey("b" * 64), claim.claim_id)
    for bound in (0, 101, True):
        with pytest.raises(ResourceAdmissionError, match="orphan_object_request_invalid"):
            claims.pending(maximum=bound)
    assert claims.pending(maximum=1) == (claim,)
    assert not claims.pending(after=claim.claim_id)
    for mutation in ("key", "delete"):
        with admission.sessions.begin() as session, pytest.raises(IntegrityError):
            row = present(session.get(OrphanObjectClaimRow, claim.claim_id))
            if mutation == "key":
                row.storage_key = "b" * 64
            else:
                session.delete(row)
            session.flush()
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0042_provider_maintenance")
    assert claims.pending() == (claim,)


def test_downgrade_waits_for_uncommitted_claim_before_deciding_emptiness(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    prepare_adjacent_downgrade(database_harness, database_name, "0043_orphan_object_claim")
    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions() as blocker:
        claim_id = uuid4()
        blocker.add(
            OrphanObjectClaimRow(
                claim_id=claim_id,
                storage_key="a" * 64,
                created_at=blocker.scalar(select(func.clock_timestamp())),
            )
        )
        blocker.flush()
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        future = pool.submit(database_harness.downgrade, database_name, "0042_provider_maintenance")
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
    assert PostgresOrphanObjectRepository(admission.sessions).pending()[0].claim_id == claim_id


pytestmark = pytest.mark.usefixtures("internal_io_budget")
