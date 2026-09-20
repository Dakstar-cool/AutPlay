"""A1 provider ownership becomes one canonical ingest with no split commit."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.controlled_discovery import PostgresControlledDiscoveryRepository
from autplay.adapters.postgresql.discovery_runtime import (
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    AudioVariantRow,
    CatalogChangeSetRow,
    DiscoveryCandidateRow,
    ExternalReferenceRow,
    JobRow,
    LibraryEntryRow,
    ProviderStagingRow,
    RecordingRedirectRow,
    RecordingRow,
    SourceAuthorizationRow,
    UploadSessionRow,
    UserAccountRow,
    UserTrackRefRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.postgresql.provider_scratch import PostgresProviderScratchRepository
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.controlled_discovery import (
    DiscoveryAcquisitionTarget,
    DiscoveryHandoffReceipt,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.application.provider_scratch import ProviderScratchService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.discovery import AcquisitionAuthorizationReceipt
from autplay.domain.jobs import JobKey, RetryableJobError
from autplay.domain.resource_admission import AcquisitionClaim
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VerifiedStagedFile,
)
from sqlalchemy import func, select

from .test_discovery_authority_clock import _blocked_by, _wait_until_expired
from .test_discovery_runtime import _track
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_worker_resource_wait import a1

__all__ = ["admission"]
PAYLOAD = b"ID3" + b"synthetic controlled A1 bytes" * 100


@dataclass(frozen=True)
class Prepared:
    repository: PostgresControlledDiscoveryRepository
    target: DiscoveryAcquisitionTarget
    claim: AcquisitionClaim
    ticket: ExecutionTicket
    verified: VerifiedStagedFile
    storage: FilesystemVaultStorage

    def handoff(self) -> DiscoveryHandoffReceipt:
        return self.repository.handoff(
            self.claim, self.target, self.ticket.execution_id, self.verified, _track()
        )


def ready(harness: AdmissionHarness, tmp_path: Path, *, exit_code: int | None = 0) -> Prepared:
    harness.budget(transfers=1)
    claim = a1(harness)
    with harness.sessions() as session:
        attempt = present(session.get(AcquisitionAttemptRow, claim.acquisition_id))
        candidate = present(session.get(DiscoveryCandidateRow, attempt.candidate_id))
        candidate_id, owner = candidate.candidate_id, candidate.user_id
    repository = PostgresControlledDiscoveryRepository(harness.sessions)
    target = repository.prepare(candidate_id, owner, claim.fence)
    assert isinstance(target, DiscoveryAcquisitionTarget)
    assert repository.prepare(candidate_id, owner, claim.fence) == target
    active = fence(harness.service.acquire_worker(claim))
    permit = harness.service.open_io(claim, active, claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    harness.service.prepare_execution(claim, ticket)
    child = ProcessIdentity(54321, b"a" * 32)
    harness.service.start_execution(claim, ticket, child)
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey(f"provider-{ticket.execution_id.hex}")
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    verified = storage.verify_staging(key)
    if exit_code is not None:
        harness.service.confirm_execution_exit(
            ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, exit_code, child)
        )
        harness.service.close_io(ticket.permit)
    return Prepared(repository, target, claim, ticket, verified, storage)


def snapshot(harness: AdmissionHarness, prepared: Prepared) -> tuple[object, ...]:
    with harness.sessions() as session:
        candidate = present(session.get(DiscoveryCandidateRow, prepared.target.candidate_id))
        stage = present(session.get(ProviderStagingRow, prepared.ticket.execution_id))
        return (
            candidate.acquisition_state,
            candidate.recording_id,
            candidate.staging_key,
            stage.state,
            stage.upload_session_id,
            stage.sha256,
            *(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    RecordingRow,
                    UserTrackRefRow,
                    LibraryEntryRow,
                    ExternalReferenceRow,
                    UploadSessionRow,
                    JobRow,
                )
            ),
        )


def test_atomic_handoff_replays_before_revoked_authority_and_rejects_conflicts(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    prepared = ready(admission, tmp_path)
    result = prepared.handoff()
    assert prepared.handoff() == result
    with admission.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, result.upload_id))
        stage = present(session.get(ProviderStagingRow, result.execution_id))
        assert upload.state == "SEALED" and upload.actor_kind == "PROVIDER"
        assert upload.source_acquisition_attempt_id == prepared.claim.acquisition_id
        assert upload.staging_key == stage.staging_key
        assert stage.state == "HANDED_OFF" and stage.upload_session_id == upload.upload_session_id
        assert stage.byte_size == upload.expected_size == len(PAYLOAD)
        assert stage.sha256 == upload.declared_sha256 == prepared.verified.sha256.value
        present(session.get(UserAccountRow, prepared.target.user_id)).authority_generation += 1
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
    assert (
        prepared.repository.prepare(
            prepared.target.candidate_id, prepared.target.user_id, prepared.claim.fence
        )
        == result
    )
    assert prepared.handoff() == result
    for changed in (
        replace(prepared, verified=replace(prepared.verified, sha256=Sha256Digest(b"z" * 32))),
        replace(prepared, ticket=replace(prepared.ticket, execution_id=uuid4())),
    ):
        with pytest.raises(BulkDiscoveryError, match="discovery_handoff_conflict"):
            changed.handoff()
    assert PostgresProviderCleanupRepository(admission.sessions).claim(result.execution_id) is None


@pytest.mark.parametrize("boundary", ["identity", "enqueue", "after_binding"])
def test_failed_transaction_rolls_back_identity_seal_upload_and_job(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    prepared = ready(admission, tmp_path)
    initial = snapshot(admission, prepared)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic.abort_handoff")

    with monkeypatch.context() as patch:
        if boundary == "identity":
            patch.setattr(PostgresBulkDiscoveryRepository, "_materialize_identity", fail)
        elif boundary == "enqueue":
            patch.setattr(PostgresJobRepository, "enqueue", fail)
        else:
            patch.setattr(PostgresControlledDiscoveryRepository, "_validate_identity", fail)
        with pytest.raises(RuntimeError, match=r"synthetic\.abort_handoff"):
            prepared.handoff()
    assert snapshot(admission, prepared) == initial
    assert prepared.handoff().byte_size == len(PAYLOAD)


@pytest.mark.parametrize("failure", ["live", "failed", "old_worker", "evidence", "owner"])
def test_handoff_requires_exact_exited_worker_owner_and_fresh_evidence(
    admission: AdmissionHarness, tmp_path: Path, failure: str
) -> None:
    prepared = ready(
        admission,
        tmp_path,
        exit_code=None if failure == "live" else 2 if failure == "failed" else 0,
    )
    initial = snapshot(admission, prepared)
    if failure == "old_worker":
        with admission.sessions.begin() as session:
            present(session.get(JobRow, prepared.claim.fence.job_id)).attempt_count += 1
        prepared = replace(
            prepared,
            claim=replace(prepared.claim, fence=replace(prepared.claim.fence, attempt_no=2)),
        )
    elif failure == "owner":
        prepared = replace(prepared, target=replace(prepared.target, user_id=uuid4()))
    with pytest.raises((BulkDiscoveryError, RetryableJobError)):
        prepared.repository.handoff(
            prepared.claim,
            prepared.target,
            prepared.ticket.execution_id,
            prepared.verified,
            _track("11") if failure == "evidence" else _track(),
        )
    assert snapshot(admission, prepared) == initial


@pytest.mark.parametrize(
    "blocked,expiry",
    [(blocked, expiry) for blocked in ("owner", "receipt") for expiry in ("job", "source")],
)
def test_handoff_wait_expiry_never_commits_a_partial_binding(
    admission: AdmissionHarness, tmp_path: Path, blocked: str, expiry: str
) -> None:
    prepared = ready(admission, tmp_path)
    initial = snapshot(admission, prepared)
    with admission.sessions.begin() as session:
        deadline = present(session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=1)
        if expiry == "job":
            present(session.get(JobRow, prepared.claim.fence.job_id)).lease_deadline = deadline
        else:
            attempt = present(session.get(AcquisitionAttemptRow, prepared.claim.acquisition_id))
            present(
                session.get(SourceAuthorizationRow, attempt.source_authorization_id)
            ).expires_at = deadline

    def invoke() -> object:
        return prepared.handoff()

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        if blocked == "owner":
            _acquire_sync_owner_publish_lock(blocker, prepared.target.user_id)
        else:
            blocker.execute(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == prepared.ticket.execution_id)
                .with_for_update()
            )
        pending = pool.submit(invoke)
        try:
            _wait_until_expired(admission, blocker, pending, deadline)
        finally:
            blocker.rollback()
        with pytest.raises(
            BulkDiscoveryError,
            match="lease_fence_lost" if expiry == "job" else "source_authorization_unavailable",
        ):
            pending.result(timeout=5)
    assert snapshot(admission, prepared) == initial


class Media:
    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        assert path.read_bytes() == PAYLOAD
        return AudioTechnicalMetadata("mp3", "mp3", 48000, 2, 180000, 128000, None)

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        assert path.read_bytes() == PAYLOAD
        return ChromaprintEvidence("chromaprint", "1.6.1", 180000, b"fixture-fp")


class Authorization:
    def authorize(
        self,
        candidate_id: UUID,
        provider_track_id: str,
        *,
        boundary: str,
        owner_user_id: UUID,
        acquisition_attempt_id: UUID,
    ) -> AcquisitionAuthorizationReceipt:
        return AcquisitionAuthorizationReceipt(
            candidate_id, provider_track_id, "20", boundary, datetime.now(UTC)
        )


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize("retirement", ["before_ingest", "after_ingest"])
def test_actual_ingest_ready_receipt_replays_after_source_expiry_without_second_transfer(
    admission: AdmissionHarness, tmp_path: Path, reused: bool, retirement: str
) -> None:
    prepared = ready(admission, tmp_path)
    receipt = prepared.handoff()
    scratch = FilesystemProviderStorage(tmp_path)
    workspace = scratch.create_workspace(receipt.execution_id)
    (workspace / "audio.mp3").write_bytes(PAYLOAD)
    retire = ProviderScratchService(PostgresProviderScratchRepository(admission.sessions), scratch)
    if retirement == "before_ingest":
        assert retire.retire(receipt.execution_id)
    if reused:
        key = OpaqueStorageKey(f"provider-{prepared.ticket.execution_id.hex}")
        prepared.storage.commit_staging(key, prepared.verified)
        with admission.sessions.begin() as session:
            now = present(session.scalar(select(func.clock_timestamp())))
            stored = VaultObjectRow(
                sha256=prepared.verified.sha256.value,
                byte_size=len(PAYLOAD),
                detected_mime_type="audio/mpeg",
                commit_status="COMMITTED",
                committed_at=now,
            )
            session.add(stored)
            session.flush()
            session.add(
                VaultReplicaRow(
                    vault_object_id=stored.vault_object_id,
                    storage_backend="LOCAL_FILESYSTEM",
                    storage_key=prepared.verified.sha256.hex,
                    replica_status="AVAILABLE",
                    verified_at=now,
                )
            )
            session.add(
                AudioVariantRow(
                    recording_id=receipt.recording_id,
                    vault_object_id=stored.vault_object_id,
                    codec="mp3",
                    container="mp3",
                    sample_rate_hz=48000,
                    channels=2,
                    duration_ms=180000,
                    bitrate_bps=128000,
                    validation_status="VALID",
                )
            )
    admission.service.release(prepared.claim, prepared.ticket.permit.fence)
    with admission.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="a1-controlled-ingest",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
        assert lease.fence.job_id == receipt.ingest_job_id
    media = Media()
    handler = VaultIngestHandler(
        repository=TransactionalIngestRepository(
            SqlAlchemyVaultUnitOfWorkFactory(admission.sessions)
        ),
        storage=prepared.storage,
        media=media,
        fingerprints=media,
        source_authorizer=Authorization(),
    )
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
        fence=lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    handler(context, lease)
    with admission.sessions.begin() as session:
        candidate = present(session.get(DiscoveryCandidateRow, prepared.target.candidate_id))
        attempt = present(session.get(AcquisitionAttemptRow, prepared.claim.acquisition_id))
        upload = present(session.get(UploadSessionRow, receipt.upload_id))
        assert candidate.acquisition_state == "READY" and candidate.staging_key is None
        assert attempt.state == "COMPLETED" and upload.state == (
            "REUSED" if reused else "COMMITTED"
        )
        present(session.get(SourceAuthorizationRow, attempt.source_authorization_id)).expires_at = (
            present(session.scalar(select(func.clock_timestamp()))) - timedelta(seconds=1)
        )
        present(session.get(UserAccountRow, prepared.target.user_id)).authority_generation += 1
        assert session.scalars(select(ResourceAdmissionRow.resource_type)).all() == [
            "DISCOVERY_ACQUISITION"
        ]
        assert session.scalar(select(ResourceAdmissionRow.state)) == "RELEASED"
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 1
    assert (
        prepared.repository.prepare(
            prepared.target.candidate_id, prepared.target.user_id, prepared.claim.fence
        )
        == receipt
    )
    assert prepared.handoff() == receipt
    assert prepared.storage.inventory().staging_keys == ()
    assert retire.retire(receipt.execution_id) and retire.retire(receipt.execution_id)
    assert not workspace.exists()
    assert (
        prepared.storage.verify_object(OpaqueStorageKey(prepared.verified.sha256.hex))
        == prepared.verified
    )


def identity(harness: AdmissionHarness, prepared: Prepared) -> tuple[UUID, UUID, UUID]:
    with harness.sessions.begin() as session:
        candidate = present(session.get(DiscoveryCandidateRow, prepared.target.candidate_id))
        recording, ref, entry, _ = PostgresBulkDiscoveryRepository(session)._materialize_identity(
            candidate, _track()
        )
        return recording.recording_id, ref.user_track_ref_id, entry.library_entry_id


def test_handoff_reuses_the_existing_owned_identity_without_duplicate_graph(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    prepared = ready(admission, tmp_path)
    recording, _, _ = identity(admission, prepared)
    with admission.sessions() as session:
        count = session.scalar(select(func.count()).select_from(RecordingRow))
    assert prepared.handoff().recording_id == recording
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RecordingRow)) == count


@pytest.mark.parametrize("removed", ["recording", "ref", "entry", "redirect"])
def test_removal_winning_existing_identity_lock_prevents_handoff(
    admission: AdmissionHarness, tmp_path: Path, removed: str
) -> None:
    prepared = ready(admission, tmp_path)
    recording_id, ref_id, entry_id = identity(admission, prepared)
    initial = snapshot(admission, prepared)

    def invoke() -> object:
        return prepared.handoff()

    with admission.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        if removed in {"recording", "redirect"}:
            recording = present(
                blocker.scalar(
                    select(RecordingRow)
                    .where(RecordingRow.recording_id == recording_id)
                    .with_for_update()
                )
            )
        elif removed == "ref":
            ref = present(
                blocker.scalar(
                    select(UserTrackRefRow)
                    .where(UserTrackRefRow.user_track_ref_id == ref_id)
                    .with_for_update()
                )
            )
        else:
            entry = present(
                blocker.scalar(
                    select(LibraryEntryRow)
                    .where(LibraryEntryRow.library_entry_id == entry_id)
                    .with_for_update()
                )
            )
        pending = pool.submit(invoke)
        try:
            deadline = present(blocker.scalar(select(func.clock_timestamp()))) + timedelta(
                milliseconds=100
            )
            _wait_until_expired(admission, blocker, pending, deadline)
            now = present(blocker.scalar(select(func.clock_timestamp())))
            if removed == "recording":
                recording.deleted_at = now
            elif removed == "redirect":
                replacement = present(
                    blocker.scalar(
                        select(RecordingRow.recording_id).where(
                            RecordingRow.recording_id != recording_id
                        )
                    )
                )
                change = CatalogChangeSetRow(
                    operation_type="MERGE", actor_type="SYSTEM", reason="synthetic handoff race"
                )
                blocker.add(change)
                blocker.flush()
                blocker.add(
                    RecordingRedirectRow(
                        source_recording_id=recording_id,
                        target_recording_id=replacement,
                        change_set_id=change.change_set_id,
                        reason="synthetic handoff race",
                    )
                )
            elif removed == "ref":
                ref.deleted_at = now
            else:
                entry.removed_at = now
            blocker.commit()
        finally:
            blocker.rollback()
        with pytest.raises(BulkDiscoveryError):
            pending.result(timeout=5)
    assert snapshot(admission, prepared) == initial


def test_live_sibling_still_owns_the_target_after_the_first_execution_exits(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    prepared = ready(admission, tmp_path)
    permit = admission.service.open_io(
        prepared.claim, prepared.ticket.permit.fence, prepared.claim.acquisition_id
    )
    ticket = ExecutionTicket(
        uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, prepared.claim.acquisition_id
    )
    admission.service.prepare_execution(prepared.claim, ticket)
    with pytest.raises(RetryableJobError, match="discovery_staging_busy"):
        prepared.handoff()
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
    )
    admission.service.close_io(permit)
    assert prepared.handoff().execution_id == prepared.ticket.execution_id


def test_cleanup_waits_for_atomic_handoff_and_preserves_the_transferred_file(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters.postgresql import provider_cleanup
    from autplay.adapters.postgresql.resource_limits import lock_resource_admission
    from sqlalchemy.orm import Session

    prepared = ready(admission, tmp_path)
    held, release, cleanup_entered = Event(), Event(), Event()
    validate = PostgresControlledDiscoveryRepository._validate_identity
    cleanup_lock = lock_resource_admission
    pids: list[int] = []

    def hold(
        session: Session, candidate: DiscoveryCandidateRow, attempt: AcquisitionAttemptRow
    ) -> None:
        validate(session, candidate, attempt)
        pids.append(present(session.scalar(select(func.pg_backend_pid()))))
        held.set()
        assert release.wait(8)

    def observe(session: Session) -> None:
        cleanup_entered.set()
        cleanup_lock(session)

    monkeypatch.setattr(
        PostgresControlledDiscoveryRepository, "_validate_identity", staticmethod(hold)
    )
    monkeypatch.setattr(provider_cleanup, "lock_resource_admission", observe)

    def cleanup() -> object:
        return PostgresProviderCleanupRepository(admission.sessions).claim(
            prepared.ticket.execution_id
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = pool.submit(prepared.handoff)
        try:
            assert held.wait(5)
            pending = pool.submit(cleanup)
            assert cleanup_entered.wait(5)
            with admission.sessions() as observer:
                # Wait for the actual admission-lock dependency, not a thread event alone.
                from time import monotonic, sleep

                deadline = monotonic() + 5
                while not _blocked_by(observer, pids[0]):
                    assert not pending.done() and monotonic() < deadline
                    sleep(0.005)
            assert snapshot(admission, prepared)[0] == "ACQUIRING"
        finally:
            release.set()
        result = published.result(timeout=5)
        assert pending.result(timeout=5) is None
    assert (tmp_path / "staging" / f"provider-{result.execution_id.hex}").read_bytes() == PAYLOAD
