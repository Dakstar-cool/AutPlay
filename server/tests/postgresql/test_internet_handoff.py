"""One exited provider receipt atomically becomes one Internet upload and ingest job."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql import internet_acquisition
from autplay.adapters.postgresql.internet_acquisition import PostgresInternetAcquisitionRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    JobRow,
    LibraryEntryRow,
    ProviderStagingRow,
    RecordingRow,
    UploadSessionRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.provider_scratch import PostgresProviderScratchRepository
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.internet_acquisition import (
    InternetAcquisitionTarget,
    InternetHandoffReceipt,
)
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost
from autplay.application.provider_scratch import ProviderScratchService
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.jobs import JobKey, LeaseTransition, RetryableJobError, TerminalJobError
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VerifiedStagedFile
from sqlalchemy import func, select, text

from .test_internet_ingest_authority import PAYLOAD
from .test_internet_vault_publication import Fingerprints, Media
from .test_provider_staging import Writer, writer
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


def ready(
    harness: AdmissionHarness, tmp_path: Path
) -> tuple[
    PostgresInternetAcquisitionRepository, Writer, InternetAcquisitionTarget, VerifiedStagedFile
]:
    owned = writer(harness)
    repository = PostgresInternetAcquisitionRepository(harness.sessions)
    target = repository.prepare(owned.claim)
    assert isinstance(target, InternetAcquisitionTarget)
    assert repository.prepare(owned.claim) == target
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(owned.key)
    storage.write_chunk(
        owned.key,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    verified = storage.verify_staging(owned.key)
    harness.service.confirm_execution_exit(owned.ticket, owned.proof)
    # Durable proof survives this normal permit acknowledgement before metadata handoff.
    harness.service.close_io(owned.ticket.permit)
    return repository, owned, target, verified


def assert_unbound(harness: AdmissionHarness, owned: Writer) -> None:
    with harness.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, owned.claim.acquisition_id))
        assert source.upload_id is None and source.state == "DOWNLOADING"
        receipt = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert (
            receipt.state == "EXITED"
            and receipt.sha256 is None
            and receipt.upload_session_id is None
        )
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
        assert (
            session.scalar(
                select(func.count()).select_from(JobRow).where(JobRow.job_type == "vault.ingest")
            )
            == 0
        )


def test_handoff_is_single_atomic_and_replay_precedes_revoked_authority(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    receipt = repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    assert isinstance(receipt, InternetHandoffReceipt)
    assert receipt.target == target and receipt.sha256 == verified.sha256
    assert repository.handoff(owned.claim, target, owned.ticket.execution_id, verified) == receipt
    with admission.sessions.begin() as session:
        source = present(session.get(InternetAcquisitionRow, target.acquisition_id))
        upload = present(session.get(UploadSessionRow, receipt.upload_id))
        staging = present(session.get(ProviderStagingRow, receipt.execution_id))
        assert source.state == "PROCESSING" and source.upload_id == receipt.upload_id
        assert (
            upload.state == "SEALED"
            and upload.actor_kind == "INTERNET"
            and upload.device_id is None
        )
        assert staging.state == "HANDED_OFF" and staging.upload_session_id == receipt.upload_id
        assert staging.sha256 == upload.declared_sha256 == verified.sha256.value
        assert upload.staging_key == owned.key.value
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(JobRow).where(JobRow.job_type == "vault.ingest")
            )
            == 1
        )
        present(session.get(UserAccountRow, target.user_id)).authority_generation += 1
    assert repository.prepare(owned.claim) == receipt
    assert repository.handoff(owned.claim, target, owned.ticket.execution_id, verified) == receipt
    for changed in (
        replace(verified, byte_size=verified.byte_size + 1),
        replace(verified, sha256=Sha256Digest(b"x" * 32)),
    ):
        with pytest.raises(TerminalJobError, match="music_handoff_conflict"):
            repository.handoff(owned.claim, target, owned.ticket.execution_id, changed)


@pytest.mark.parametrize("boundary", ["enqueue", "after_binding"])
def test_handoff_lost_transaction_never_strands_half_a_binding(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    original = PostgresJobRepository.enqueue

    def enqueue_then_fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("test.abort_handoff")

    with monkeypatch.context() as patch:
        if boundary == "enqueue":
            # No generated upload or durable seal can survive a failed enqueue.
            patch.setattr(PostgresJobRepository, "enqueue", enqueue_then_fail)
        else:
            patch.setattr(
                internet_acquisition, "require_internet_ingest_authority", enqueue_then_fail
            )
        with pytest.raises(RuntimeError, match=r"test\.abort_handoff"):
            repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    assert PostgresJobRepository.enqueue is original
    assert_unbound(admission, owned)
    receipt = repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    assert receipt.target == target


@pytest.mark.parametrize("failure", ["generation", "family", "removed", "recording", "worker"])
def test_handoff_rechecks_original_authority_target_and_exact_worker(
    admission: AdmissionHarness, tmp_path: Path, failure: str
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    with admission.sessions.begin() as session:
        match failure:
            case "generation":
                present(session.get(UserAccountRow, target.user_id)).authority_generation += 1
            case "family":
                for row in session.scalars(
                    select(UserSessionRow).where(UserSessionRow.user_id == target.user_id)
                ):
                    row.revoked_at = datetime.now(UTC)
            case "removed":
                present(
                    session.scalar(
                        select(LibraryEntryRow).where(
                            LibraryEntryRow.user_track_ref_id == target.ref_id
                        )
                    )
                ).removed_at = datetime.now(UTC)
            case "recording":
                target = replace(target, recording_id=uuid4())
            case _:
                owned = replace(
                    owned,
                    claim=replace(
                        owned.claim, fence=replace(owned.claim.fence, worker_id="stale-worker")
                    ),
                )
    with pytest.raises((TerminalJobError, JobLeaseLost)):
        repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    assert_unbound(admission, owned)


def test_handoff_requires_exit_proof_even_if_bytes_are_present(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    owned = writer(admission)
    repository = PostgresInternetAcquisitionRepository(admission.sessions)
    target = repository.prepare(owned.claim)
    assert isinstance(target, InternetAcquisitionTarget)
    with pytest.raises(RetryableJobError, match="music_staging_busy"):
        repository.handoff(
            owned.claim,
            target,
            owned.ticket.execution_id,
            VerifiedStagedFile(10, Sha256Digest(hashlib.sha256(PAYLOAD).digest())),
        )
    with admission.sessions() as session:
        assert present(session.get(ProviderStagingRow, owned.ticket.execution_id)).state == "OWNED"
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0


@pytest.mark.parametrize("retirement", ["before_ingest", "after_ingest"])
def test_typed_handoff_reaches_ready_through_actual_ingest_without_second_transfer(
    admission: AdmissionHarness, tmp_path: Path, retirement: str
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    receipt = repository.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    scratch = FilesystemProviderStorage(tmp_path)
    workspace = scratch.create_workspace(receipt.execution_id)
    (workspace / "audio.media").write_bytes(PAYLOAD)
    retire = ProviderScratchService(PostgresProviderScratchRepository(admission.sessions), scratch)
    if retirement == "before_ingest":
        assert retire.retire(receipt.execution_id)
    admission.service.release(owned.claim, owned.ticket.permit.fence)
    with admission.sessions.begin() as session:
        jobs = PostgresJobRepository(session)
        assert jobs.complete(owned.claim.fence) is LeaseTransition.APPLIED
        lease = jobs.claim(
            worker_id="handoff-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
        assert lease.fence.job_id == receipt.ingest_job_id
    storage = FilesystemVaultStorage(tmp_path)
    handler = VaultIngestHandler(
        repository=TransactionalIngestRepository(
            SqlAlchemyVaultUnitOfWorkFactory(admission.sessions)
        ),
        storage=storage,
        media=Media(),
        fingerprints=Fingerprints(),
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
        fence=lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    handler(context, lease)
    with admission.sessions() as session:
        source = present(session.get(InternetAcquisitionRow, target.acquisition_id))
        upload = present(session.get(UploadSessionRow, receipt.upload_id))
        assert source.state == "READY" and upload.state == "COMMITTED"
        assert (
            source.audio_variant_id == upload.audio_variant_id
            and source.audio_variant_id is not None
        )
        assert session.scalars(select(ResourceAdmissionRow.resource_type)).all() == [
            "INTERNET_ACQUISITION"
        ]
        assert session.scalar(select(ResourceAdmissionRow.state)) == "RELEASED"
        assert (
            session.scalar(
                select(LibraryEntryRow.availability_status).where(
                    LibraryEntryRow.user_track_ref_id == target.ref_id
                )
            )
            == "VAULT"
        )
    assert storage.inventory().staging_keys == ()
    with admission.sessions.begin() as session:
        present(session.get(UserAccountRow, target.user_id)).authority_generation += 1
    assert retire.retire(receipt.execution_id) and retire.retire(receipt.execution_id)
    assert not workspace.exists()
    assert storage.verify_object(OpaqueStorageKey(verified.sha256.hex)) == verified


def test_handoff_wait_crossing_source_lease_rolls_back_every_binding(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    with admission.sessions.begin() as session:
        present(session.get(JobRow, owned.claim.fence.job_id)).lease_deadline = deadline
    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker.execute(
            select(RecordingRow)
            .where(RecordingRow.recording_id == target.recording_id)
            .with_for_update()
        )
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        pending = executor.submit(
            repository.handoff, owned.claim, target, owned.ticket.execution_id, verified
        )
        try:
            stop_waiting = time.monotonic() + 5
            with admission.sessions() as observer:
                while not observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"blocker": pid},
                ):
                    assert not pending.done()
                    assert time.monotonic() < stop_waiting
                    time.sleep(0.01)
            time.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.1)
        finally:
            blocker.rollback()
        with pytest.raises(JobLeaseLost):
            pending.result(timeout=10)
    assert_unbound(admission, owned)


@pytest.mark.parametrize("removed", ["entry", "ref"])
def test_prepare_classifies_removed_owned_identity_as_terminal_authority_failure(
    admission: AdmissionHarness, tmp_path: Path, removed: str
) -> None:
    repository, owned, target, _verified = ready(admission, tmp_path)
    with admission.sessions.begin() as session:
        if removed == "ref":
            present(session.get(UserTrackRefRow, target.ref_id)).deleted_at = datetime.now(UTC)
        else:
            present(
                session.scalar(
                    select(LibraryEntryRow).where(
                        LibraryEntryRow.user_track_ref_id == target.ref_id
                    )
                )
            ).removed_at = datetime.now(UTC)
    with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
        repository.prepare(owned.claim)
    assert_unbound(admission, owned)


def test_session_expiring_during_staging_lock_wait_keeps_terminal_reason_and_rolls_back(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    with admission.sessions.begin() as session:
        for row in session.scalars(
            select(UserSessionRow).where(UserSessionRow.user_id == target.user_id)
        ):
            row.expires_at = deadline
    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker.execute(
            select(ProviderStagingRow)
            .where(ProviderStagingRow.execution_id == owned.ticket.execution_id)
            .with_for_update()
        )
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        pending = executor.submit(
            repository.handoff, owned.claim, target, owned.ticket.execution_id, verified
        )
        try:
            stop_waiting = time.monotonic() + 5
            with admission.sessions() as observer:
                while not observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"blocker": pid},
                ):
                    assert not pending.done()
                    assert time.monotonic() < stop_waiting
                    time.sleep(0.01)
            time.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.1)
        finally:
            blocker.rollback()
        with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
            pending.result(timeout=10)
    assert_unbound(admission, owned)
