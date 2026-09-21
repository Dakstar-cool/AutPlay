"""A dead parent never makes its still-unconfirmed upload safe to mutate or clean."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.models import UploadSessionRow
from autplay.adapters.postgresql.resource_commit_guard import require_device_upload_commit
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.vault_reconciliation import ReconcileMode
from autplay.application.vault_uploads import VaultPrincipal
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest
from autplay.entrypoints.vault_reconciliation import build_vault_reconciliation_service

from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_execution import shift_clock, upload_ticket

__all__ = ["admission"]


@pytest.mark.parametrize("state", ["PREPARED", "RUNNING", "STOPPING", "ORPHANED"])
def test_no_mutation_may_assume_unclosed_upload_writer_has_exited(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    if state != "PREPARED":
        admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
    if state == "STOPPING":
        admission.service.stop_execution(ticket)
    if state == "ORPHANED":
        with monkeypatch.context() as patch:
            shift_clock(patch, 16)
            admission.service.sweep()
        assert admission.service.inspect_execution(ticket).state == ExecutionState.ORPHANED
    principal = VaultPrincipal(actor.user_id, actor.device_id)
    for action in ("staging", "chunk", "seal", "expire", "cancel"):
        with (
            pytest.raises(ResourceAdmissionError, match="resource_execution_busy"),
            admission.sessions.begin() as session,
        ):
            repo = PostgresVaultRuntime(session)
            match action:
                case "staging":
                    repo.staging_key_for_owned(principal, ticket.actual_target_id)
                case "chunk":
                    repo.record_chunk(
                        principal,
                        ticket.actual_target_id,
                        offset=0,
                        chunk_index=0,
                        byte_size=1,
                        sha256=Sha256Digest(b"h" * 32),
                    )
                case "seal":
                    repo.seal_and_enqueue(principal, ticket.actual_target_id)
                case "expire":
                    repo.expire_open(principal, ticket.actual_target_id)
                case "cancel":
                    repo.cancel(principal, ticket.actual_target_id)
    with admission.sessions() as session:
        row = present(session.get(UploadSessionRow, ticket.actual_target_id))
        assert row.state == "OPEN" and row.received_size == row.chunk_count == 0


@pytest.mark.parametrize("file_present", [False, True])
@pytest.mark.usefixtures("internal_io_budget")
def test_reconciliation_preserves_upload_before_and_after_exact_writer_exit(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file_present: bool,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    identity = ProcessIdentity(12345, b"p" * 32)
    admission.service.start_execution(actor, ticket, identity)
    with monkeypatch.context() as patch:
        shift_clock(patch, 16)
        admission.service.sweep()
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey(ticket.actual_target_id.hex)
    if file_present:
        storage.create_staging(key)
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, ticket.actual_target_id))
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, limit=10
    )
    assert report.quarantined == report.missing == 0
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, ticket.actual_target_id)).state == "OPEN"
    assert (key in storage.inventory().staging_keys) == file_present
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.SUPERVISOR_EXIT, b"e" * 32, 0, identity)
    )
    report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, limit=10
    )
    assert report.quarantined == report.missing == 0
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, ticket.actual_target_id)).state == "OPEN"
    assert (key in storage.inventory().staging_keys) == file_present


@pytest.mark.parametrize("change", ["stop", "orphan", "identity", "owner", "permit"])
def test_final_commit_requires_current_exact_running_execution(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    registered = admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
    if change == "stop":
        admission.service.stop_execution(ticket)
    elif change == "orphan":
        with monkeypatch.context() as patch:
            shift_clock(patch, 16)
            admission.service.sweep()
    elif change == "identity":
        registered = replace(registered, child=ProcessIdentity(12345, b"q" * 32))
    elif change == "owner":
        registered = replace(registered, ticket=replace(ticket, owner_run_id=uuid4()))
    else:
        registered = replace(
            registered, ticket=replace(ticket, permit=replace(ticket.permit, permit_id=uuid4()))
        )
    with (
        pytest.raises(ResourceAdmissionError, match="resource_io_stale"),
        admission.sessions.begin() as session,
    ):
        row = present(session.get(UploadSessionRow, ticket.actual_target_id, with_for_update=True))
        row.received_size = 1
        require_device_upload_commit(
            session,
            actor,
            ticket.actual_target_id,
            ticket.permit,
            stopped=lambda: False,
            execution=registered,
        )
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, ticket.actual_target_id)).received_size == 0


def test_successful_permit_renewal_preserves_exact_execution_commit_authority(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    registered = admission.service.start_execution(actor, ticket, ProcessIdentity(12345, b"p" * 32))
    renewed = admission.service.renew_io(actor, ticket.permit)
    assert renewed.expires_at > ticket.permit.expires_at
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, ticket.actual_target_id, with_for_update=True))
        row.received_size = 1
        require_device_upload_commit(
            session,
            actor,
            ticket.actual_target_id,
            renewed,
            stopped=lambda: False,
            execution=registered,
        )


@pytest.mark.usefixtures("internal_io_budget")
def test_missing_inventory_does_not_cancel_either_owned_upload(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    other_upload = admission.upload(actor)
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, ticket.actual_target_id))
        row.created_at = datetime.now(UTC) - timedelta(hours=1)
    storage = FilesystemVaultStorage(tmp_path)
    report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, limit=1
    )
    assert report.quarantined == report.missing == 0
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, ticket.actual_target_id)).state == "OPEN"
        assert present(session.get(UploadSessionRow, other_upload)).state == "OPEN"
    assert not storage.inventory().staging_keys


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.usefixtures("internal_io_budget")
def test_locked_upload_is_skipped_without_treating_its_staging_file_as_orphan(
    admission: AdmissionHarness, tmp_path: Path, registered: bool
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    if registered:
        admission.service.prepare_execution(actor, ticket)
    other_upload = admission.upload(actor)
    protected_key = OpaqueStorageKey(ticket.actual_target_id.hex)
    other_key = OpaqueStorageKey(other_upload.hex)
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(protected_key)
    storage.create_staging(other_key)
    with admission.sessions.begin() as session:
        for identifier in (ticket.actual_target_id, other_upload):
            row = present(session.get(UploadSessionRow, identifier))
            row.created_at = datetime.now(UTC) - timedelta(hours=2)
            row.expires_at = datetime.now(UTC) - timedelta(hours=1)

    def reconcile() -> None:
        report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
            mode=ReconcileMode.APPLY, limit=10
        )
        assert report.quarantined == report.missing == 0

    # Release the lock before joining the pool even if the assertion fails.
    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions.begin() as blocked:
        blocked.get(UploadSessionRow, ticket.actual_target_id, with_for_update=True)
        future = pool.submit(reconcile)
        future.result(timeout=3)
    assert set(storage.inventory().staging_keys) == {protected_key, other_key}
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, ticket.actual_target_id)).state == "OPEN"
        assert present(session.get(UploadSessionRow, other_upload)).state == "OPEN"
