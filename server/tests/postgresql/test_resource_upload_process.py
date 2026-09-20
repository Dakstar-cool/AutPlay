"""Upload row/receipt authority across isolated filesystem work and late cancellation."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess, VaultProcessSupervisor
from autplay.adapters.filesystem.vault_process_upload import ProcessVaultChunkWriter
from autplay.adapters.postgresql.models import DeviceRow, UploadChunkRow, UploadSessionRow
from autplay.adapters.postgresql.resource_commit_guard import require_device_upload_commit
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.vault_uploads import VaultPrincipal, VaultUploadService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionStatus,
    ExecutionTicket,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_execution import upload_ticket
from .test_resource_process import wait_exit

__all__ = ["admission"]
PAYLOAD = b"x" * 100
DIGEST = Sha256Digest(hashlib.sha256(PAYLOAD).digest())


@dataclass
class Upload:
    actor: Principal
    ticket: ExecutionTicket
    child: RetainedVaultProcess
    registered: ExecutionStatus
    supervisor: VaultProcessSupervisor
    storage: FilesystemVaultStorage
    writer: ProcessVaultChunkWriter

    def cleanup(self, harness: AdmissionHarness) -> None:
        self.child.request_stop()
        proof = wait_exit(self.child, self.registered.child)
        closed = harness.service.confirm_execution_exit(self.ticket, proof)
        self.supervisor.forget(self.child, closed)
        harness.service.close_io(self.ticket.permit)


def prepare(harness: AdmissionHarness, root: Path) -> Upload:
    harness.budget()
    actor = harness.actor()
    ticket = upload_ticket(harness, actor)
    harness.service.prepare_execution(actor, ticket)
    supervisor = VaultProcessSupervisor(maximum=1)
    child = supervisor.retain(ticket, ResourceIoDeadline(monotonic()))
    identity = child.spawn()
    registered = harness.service.start_execution(actor, ticket, identity)
    storage = FilesystemVaultStorage(root)
    writer = ProcessVaultChunkWriter(child, registered, root=root, limits=VaultLimits())
    return Upload(actor, ticket, child, registered, supervisor, storage, writer)


def append(harness: AdmissionHarness, upload: Upload, *, revoke: bool = False) -> bool:
    with harness.sessions() as session:
        service = VaultUploadService(
            repository=PostgresVaultRuntime(session, upload_execution=upload.registered),
            chunk_writer=upload.writer,
        )
        result = service.append(
            VaultPrincipal(upload.actor.user_id, upload.actor.device_id),
            upload.ticket.actual_target_id,
            offset=0,
            chunk_index=0,
            payload=PAYLOAD,
            payload_sha256=DIGEST,
        )
        # Child has exited, but this original upload transaction is still active.
        assert upload.child.exit_evidence(upload.registered.child) is not None
        if revoke:
            with harness.sessions.begin() as authority:
                device = present(authority.get(DeviceRow, upload.actor.device_id))
                device.revoked_at = authority.scalar(select(func.clock_timestamp()))
        require_device_upload_commit(
            session,
            upload.actor,
            upload.ticket.actual_target_id,
            upload.ticket.permit,
            stopped=upload.child.deadline.stopped,
            execution=upload.registered,
        )
        session.commit()
        return result.idempotent


def test_real_chunk_commits_once_and_exact_receipt_replay_does_not_restart_child(
    admission: AdmissionHarness,
    tmp_path: Path,
) -> None:
    upload = prepare(admission, tmp_path)
    try:
        assert not append(admission, upload)
        # Reusing an already-used writer would fail if the replay touched filesystem.
        assert append(admission, upload)
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, upload.ticket.actual_target_id))
            assert row.received_size == len(PAYLOAD) and row.chunk_count == 1
            assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 1
        key = OpaqueStorageKey(upload.ticket.actual_target_id.hex)
        assert upload.storage.staging_path_for_media(key).read_bytes() == PAYLOAD
    finally:
        upload.cleanup(admission)


def test_authority_revoked_after_filesystem_work_rolls_back_chunk_receipt(
    admission: AdmissionHarness,
    tmp_path: Path,
) -> None:
    upload = prepare(admission, tmp_path)
    try:
        with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
            append(admission, upload, revoke=True)
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, upload.ticket.actual_target_id))
            assert row.received_size == row.chunk_count == 0
            assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 0
        # The existing retry path can truncate this uncommitted suffix later.
        key = OpaqueStorageKey(upload.ticket.actual_target_id.hex)
        assert upload.storage.staging_path_for_media(key).read_bytes() == PAYLOAD
    finally:
        upload.cleanup(admission)


def test_deadline_cannot_unwind_upload_row_while_child_exit_is_unconfirmed(
    admission: AdmissionHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = prepare(admission, tmp_path)
    entered = threading.Event()
    allow_confirmation = threading.Event()
    original_read = RetainedVaultProcess.read_result
    original_exit = RetainedVaultProcess.exit_evidence

    def stopped_after_result(child: RetainedVaultProcess) -> tuple[bytes, bytes]:
        result = original_read(child)
        entered.set()
        child.deadline.stop()
        return result

    def delayed_exit(
        child: RetainedVaultProcess,
        identity: ProcessIdentity | None,
    ) -> ProcessExitEvidence | None:
        if not allow_confirmation.is_set():
            return None
        return original_exit(child, identity)

    try:
        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
            patch.setattr(RetainedVaultProcess, "read_result", stopped_after_result)
            patch.setattr(RetainedVaultProcess, "exit_evidence", delayed_exit)
            future = pool.submit(append, admission, upload)
            try:
                assert entered.wait(timeout=5)
                assert not future.done()
                with pytest.raises(DBAPIError), admission.sessions.begin() as contender:
                    contender.get(
                        UploadSessionRow,
                        upload.ticket.actual_target_id,
                        with_for_update={"nowait": True},
                    )
                assert (
                    admission.service.poll(
                        upload.actor, upload.ticket.permit.fence.operation_id
                    ).usage.server
                    == 1
                )
            finally:
                allow_confirmation.set()
            with pytest.raises(IoStopped):
                future.result(timeout=5)
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, upload.ticket.actual_target_id))
            assert row.received_size == row.chunk_count == 0
    finally:
        allow_confirmation.set()
        upload.cleanup(admission)
