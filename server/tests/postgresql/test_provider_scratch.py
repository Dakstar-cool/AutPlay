"""Successful provider scratch has independent durable ownership and replay."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.postgresql.models import ProviderStagingRow, UploadSessionRow, UserAccountRow
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.postgresql.provider_scratch import PostgresProviderScratchRepository
from autplay.adapters.postgresql.resource_limits import (
    RESOURCE_ADMISSION_LOCK,
    lock_resource_admission,
)
from autplay.application.provider_scratch import (
    ProviderScratchClaim,
    ProviderScratchService,
    provider_scratch_id,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import StorageOperationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from .conftest import DatabaseHarness, prepare_adjacent_downgrade
from .test_discovery_authority_clock import _blocked_by
from .test_discovery_handoff import ready as ready_a1
from .test_internet_handoff import ready as ready_internet
from .test_provider_staging import writer
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


@dataclass(frozen=True)
class HandedOff:
    execution_id: UUID
    upload_id: UUID
    owner: UUID
    staged: Path
    storage: FilesystemProviderStorage
    workspace: Path


def handed_off(harness: AdmissionHarness, root: Path, provider: str = "internet") -> HandedOff:
    if provider == "discovery":
        prepared = ready_a1(harness, root)
        receipt = prepared.handoff()
        harness.service.release(prepared.claim, prepared.ticket.permit.fence)
        execution, upload, owner = receipt.execution_id, receipt.upload_id, prepared.target.user_id
    else:
        repository, owned, target, verified = ready_internet(harness, root)
        result = repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
        harness.service.release(owned.claim, owned.ticket.permit.fence)
        execution, upload, owner = result.execution_id, result.upload_id, target.user_id
    storage = FilesystemProviderStorage(root)
    staged = root / "staging" / f"provider-{execution.hex}"
    workspace = storage.create_workspace(execution)
    (workspace / "audio.media").write_bytes(staged.read_bytes())
    return HandedOff(execution, upload, owner, staged, storage, workspace)


@pytest.mark.parametrize("provider", ["internet", "discovery"])
def test_retirement_is_independent_of_authority_and_preserves_upload(
    admission: AdmissionHarness, tmp_path: Path, provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = handed_off(admission, tmp_path, provider)
    repository = PostgresProviderScratchRepository(admission.sessions)
    original = owned.staged.stat()
    payload = owned.staged.read_bytes()
    with admission.sessions.begin() as session:
        present(session.get(UserAccountRow, owned.owner)).authority_generation += 1
    service = ProviderScratchService(repository, owned.storage)
    assert service.retire(owned.execution_id)
    claim = present(repository.claim(owned.execution_id))
    assert claim.completed and claim.claim_id == provider_scratch_id(owned.execution_id)
    assert os.path.samestat(original, owned.staged.stat()) and owned.staged.read_bytes() == payload
    assert not owned.workspace.exists()
    assert (
        tmp_path / "provider-retired" / claim.claim_id.hex / "audio.media"
    ).read_bytes() == payload
    assert PostgresProviderCleanupRepository(admission.sessions).claim(owned.execution_id) is None
    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.execution_id))
        upload = present(session.get(UploadSessionRow, owned.upload_id))
        assert row.state == "HANDED_OFF" and upload.state == "SEALED"
        assert row.upload_session_id == upload.upload_session_id
        assert row.staging_key == upload.staging_key
        assert row.sha256 == upload.declared_sha256
        assert (
            present(row.scratch_retired_at)
            >= present(row.scratch_claimed_at)
            >= present(row.handed_off_at)
        )

    def forbidden(*_args: object) -> None:
        pytest.fail("a completed scratch receipt must not access the filesystem")

    monkeypatch.setattr(owned.storage, "retire_scratch", forbidden)
    assert service.retire(owned.execution_id)


def test_only_successful_handoff_can_claim_scratch(admission: AdmissionHarness) -> None:
    owned = writer(admission)
    repository = PostgresProviderScratchRepository(admission.sessions)
    assert repository.claim(uuid4()) is None
    assert repository.claim(owned.ticket.execution_id) is None
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    admission.service.close_io(owned.ticket.permit)
    assert repository.claim(owned.ticket.execution_id) is None
    with admission.sessions.begin() as session:
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        row.state, row.byte_size, row.sha256 = "SEALED", 100, b"s" * 32
        row.sealed_at = row.updated_at = present(session.scalar(select(func.clock_timestamp())))
    assert repository.claim(owned.ticket.execution_id) is None
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        row.scratch_claim_id = provider_scratch_id(owned.ticket.execution_id)
        row.scratch_claimed_at = present(session.scalar(select(func.clock_timestamp())))
        session.flush()


def test_filesystem_runs_after_claim_commit_and_lost_completion_replays(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderScratchRepository(admission.sessions)
    calls: list[UUID] = []

    class Storage:
        def retire_scratch(self, claim: ProviderScratchClaim) -> None:
            with admission.sessions.begin() as session:
                assert session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RESOURCE_ADMISSION_LOCK}
                )
                row = present(session.get(ProviderStagingRow, claim.execution_id))
                assert row.scratch_claim_id == claim.claim_id and row.scratch_retired_at is None
            calls.append(claim.claim_id)
            owned.storage.retire_scratch(claim)

    service = ProviderScratchService(repository, Storage())

    def failed_complete(_claim: ProviderScratchClaim) -> None:
        raise SQLAlchemyError("synthetic lost scratch completion")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "complete", failed_complete)
        with pytest.raises(SQLAlchemyError, match="synthetic lost scratch completion"):
            service.retire(owned.execution_id)
    assert not owned.workspace.exists()
    assert not present(repository.claim(owned.execution_id)).completed
    assert service.retire(owned.execution_id)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert present(repository.claim(owned.execution_id)).completed


def test_missing_directories_never_record_completion(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    repository, owned, target, verified = ready_internet(admission, tmp_path)
    repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    scratch = PostgresProviderScratchRepository(admission.sessions)
    storage = FilesystemProviderStorage(tmp_path)
    with pytest.raises(StorageOperationError):
        ProviderScratchService(scratch, storage).retire(owned.ticket.execution_id)
    assert not present(scratch.claim(owned.ticket.execution_id)).completed
    assert (tmp_path / "staging" / owned.key.value).exists()


@pytest.mark.parametrize("provider", ["internet", "discovery"])
def test_concurrent_retirement_services_share_one_claim(
    admission: AdmissionHarness, tmp_path: Path, provider: str
) -> None:
    owned = handed_off(admission, tmp_path, provider)
    repository = PostgresProviderScratchRepository(admission.sessions)
    barrier = Barrier(2)

    class Storage:
        def retire_scratch(self, claim: ProviderScratchClaim) -> None:
            barrier.wait(timeout=5)
            owned.storage.retire_scratch(claim)

    service = ProviderScratchService(repository, Storage())
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(service.retire, owned.execution_id) for _ in range(2)]
        assert all(result.result(timeout=10) for result in pending)
    assert present(repository.claim(owned.execution_id)).completed
    assert len(tuple((tmp_path / "provider-retired").iterdir())) == 1
    assert owned.staged.exists()


def test_pending_keyset_includes_unfinished_claims_and_skips_completed(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    first = handed_off(admission, tmp_path)
    second = handed_off(admission, tmp_path, "discovery")
    repository = PostgresProviderScratchRepository(admission.sessions)
    expected = sorted((first.execution_id, second.execution_id))
    assert repository.pending(maximum=1) == (expected[0],)
    assert repository.pending(maximum=1, after=expected[0]) == (expected[1],)
    assert repository.pending(after=expected[1]) == ()
    repository.claim(expected[0])
    assert repository.pending() == tuple(expected)
    assert ProviderScratchService(repository, first.storage).retire(expected[0])
    assert repository.pending() == (expected[1],)
    for invalid in (0, 101, True):
        with pytest.raises(ResourceAdmissionError, match="provider_scratch_request_invalid"):
            repository.pending(maximum=invalid)


def test_scratch_receipts_are_immutable_and_cannot_complete_without_a_claim(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    owned = handed_off(admission, tmp_path)
    repository = PostgresProviderScratchRepository(admission.sessions)
    unclaimed = ProviderScratchClaim(owned.execution_id, provider_scratch_id(owned.execution_id))
    with pytest.raises(ResourceAdmissionError, match="provider_scratch_conflict"):
        repository.complete(unclaimed)
    claim = present(repository.claim(owned.execution_id))
    for field in ("scratch_claim_id", "scratch_claimed_at"):
        with admission.sessions.begin() as session, pytest.raises(IntegrityError):
            row = present(session.get(ProviderStagingRow, owned.execution_id))
            setattr(row, field, None)
            session.flush()
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        row = present(session.get(ProviderStagingRow, owned.execution_id))
        row.scratch_retired_at = present(row.scratch_claimed_at) - timedelta(seconds=1)
        session.flush()
    owned.storage.retire_scratch(claim)
    repository.complete(claim)
    with admission.sessions.begin() as session, pytest.raises(IntegrityError):
        present(session.get(ProviderStagingRow, owned.execution_id)).scratch_retired_at = None
        session.flush()


def test_downgrade_waits_for_inflight_claim_and_refuses_to_discard_it(
    admission: AdmissionHarness,
    tmp_path: Path,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    owned = handed_off(admission, tmp_path)
    prepare_adjacent_downgrade(database_harness, database_name, "0041_provider_scratch")
    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        lock_resource_admission(blocker)
        row = present(blocker.get(ProviderStagingRow, owned.execution_id, with_for_update=True))
        row.scratch_claim_id = provider_scratch_id(owned.execution_id)
        row.scratch_claimed_at = row.updated_at = present(
            blocker.scalar(select(func.clock_timestamp()))
        )
        blocker.flush()
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        pending = pool.submit(database_harness.downgrade, database_name, "0040_provider_staging")
        try:
            with admission.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert not pending.done() and monotonic() < until
                    sleep(0.005)
            blocker.commit()
        finally:
            blocker.rollback()
        with pytest.raises(DBAPIError, match="Refusing to discard provider scratch ownership"):
            pending.result(timeout=10)
    assert (
        PostgresProviderScratchRepository(admission.sessions).claim(owned.execution_id) is not None
    )
    with database_harness.connect(database_name) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0041_provider_scratch",
        )


def test_existing_unclaimed_handoff_survives_adjacent_downgrade_and_upgrade(
    admission: AdmissionHarness,
    tmp_path: Path,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    owned = handed_off(admission, tmp_path)
    original = owned.staged.read_bytes()
    database_harness.downgrade(database_name, "0040_provider_staging")
    with database_harness.connect(database_name) as connection:
        assert connection.execute(
            "SELECT state,upload_session_id FROM vault.provider_staging WHERE execution_id=%s",
            (owned.execution_id,),
        ).fetchone() == ("HANDED_OFF", owned.upload_id)
        assert connection.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema='vault' "
            "AND table_name='provider_staging' AND column_name LIKE 'scratch_%'"
        ).fetchone() == (0,)
    database_harness.upgrade(database_name)
    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.execution_id))
        assert row.scratch_claim_id is None
        assert row.scratch_claimed_at is None
        assert row.scratch_retired_at is None
        assert row.state == "HANDED_OFF" and row.upload_session_id == owned.upload_id
    repository = PostgresProviderScratchRepository(admission.sessions)
    assert repository.pending() == (owned.execution_id,)
    assert ProviderScratchService(repository, owned.storage).retire(owned.execution_id)
    assert owned.staged.read_bytes() == original
