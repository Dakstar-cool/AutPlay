"""Committed metadata and exact admitted writers own lazy upload staging creation."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.filesystem.vault_process_upload import ProcessVaultChunkWriter
from autplay.adapters.postgresql.models import UploadChunkRow, UploadSessionRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoExecutionRow
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWork
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.resource_execution import ExecutionStatus, ExecutionTicket, ProcessExitEvidence
from autplay.domain.vault import VaultLimits
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.testclient import TestClient

from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_stream_http import idle
from .test_resource_upload_http import PAYLOAD, acquire, application, chunk_headers

__all__ = ["admission"]
AUTH = {"Authorization": "Bearer synthetic-access"}


def forbid_parent_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("HTTP parent touched Vault storage")

    monkeypatch.setattr(FilesystemVaultStorage, "__init__", forbidden)


def create_upload(client: TestClient, recording_id: UUID) -> UUID:
    response = client.post(
        "/api/v1/vault/uploads",
        headers={**AUTH, "Idempotency-Key": "lazy-staging"},
        json={"recording_id": str(recording_id), "expected_size": len(PAYLOAD)},
    )
    assert response.status_code in {200, 201}, response.text
    return UUID(response.json()["upload_id"])


def test_post_replay_head_and_complete_do_not_touch_storage_and_go_has_durable_owner(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission.budget()
    actor = admission.actor()
    recording = admission.recording(actor)
    root = tmp_path / "never-initialized-in-parent"
    forbid_parent_storage(monkeypatch)
    original_go = RetainedVaultProcess.go
    original_confirm = ResourceAdmissionService.confirm_execution_exit
    owned: list[UUID] = []
    closed: list[UUID] = []

    def checked_confirm(
        service: ResourceAdmissionService, ticket: ExecutionTicket, evidence: ProcessExitEvidence
    ) -> ExecutionStatus:
        result = original_confirm(service, ticket, evidence)
        with admission.sessions() as session:
            execution = present(session.get(ResourceIoExecutionRow, ticket.execution_id))
            assert execution.state == "CLOSED" and execution.closure_kind == "PROCESS_EXIT"
            assert execution.exit_code == 0
        closed.append(ticket.actual_target_id)
        return result

    def checked_go(
        child: RetainedVaultProcess, document: dict[str, object], payload: bytes | None = None
    ) -> None:
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, child.ticket.actual_target_id))
            execution = present(session.get(ResourceIoExecutionRow, child.ticket.execution_id))
            assert row.state == "OPEN" and row.received_size == 0
            assert document["key"] == row.staging_key
            assert document["expected_size"] == row.expected_size
            assert execution.state == "RUNNING" and execution.child_pid is not None
            assert execution.actual_target_id == row.upload_session_id
            owned.append(row.upload_session_id)
        assert not root.exists()
        original_go(child, document, payload)

    monkeypatch.setattr(RetainedVaultProcess, "go", checked_go)
    monkeypatch.setattr(ResourceAdmissionService, "confirm_execution_exit", checked_confirm)
    app, coordinator, control = application(admission, actor, root)
    try:
        with TestClient(app) as client:
            upload_id = create_upload(client, recording)
            assert create_upload(client, recording) == upload_id
            assert (
                client.head(f"/api/v1/vault/uploads/{upload_id}", headers=AUTH).status_code == 204
            )
            assert not root.exists()
            headers = {**chunk_headers(), **acquire(client, upload_id)}
            assert (
                client.patch(
                    f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=PAYLOAD
                ).status_code
                == 204
            )
            idle(coordinator)
            assert owned == closed == [upload_id]
            response = client.post(f"/api/v1/vault/uploads/{upload_id}/complete", headers=AUTH)
            assert response.status_code == 202 and response.json()["state"] == "SEALED"
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, upload_id))
            assert (root / "staging" / row.staging_key).read_bytes() == PAYLOAD
    finally:
        control.dispose()


@pytest.mark.parametrize("committed", [False, True])
def test_create_commit_failure_or_lost_reply_leaves_no_file(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    actor = admission.actor()
    recording = admission.recording(actor)
    root = tmp_path / "metadata-only"
    forbid_parent_storage(monkeypatch)
    original_commit = SqlAlchemyVaultUnitOfWork.commit

    def lost_commit(unit: SqlAlchemyVaultUnitOfWork) -> None:
        if committed:
            original_commit(unit)
        raise SQLAlchemyError("synthetic commit reply failure")

    app, _, control = application(admission, actor, root)
    try:
        with TestClient(app) as client:
            with monkeypatch.context() as patch:
                patch.setattr(SqlAlchemyVaultUnitOfWork, "commit", lost_commit)
                response = client.post(
                    "/api/v1/vault/uploads",
                    headers={**AUTH, "Idempotency-Key": "lazy-staging"},
                    json={"recording_id": str(recording), "expected_size": len(PAYLOAD)},
                )
                assert response.status_code == 503
            with admission.sessions() as session:
                assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == int(
                    committed
                )
            upload_id = create_upload(client, recording)
            assert create_upload(client, recording) == upload_id
            assert not root.exists()
    finally:
        control.dispose()


@pytest.mark.parametrize("committed", [False, True])
def test_first_chunk_commit_failure_reconciles_suffix_or_replays_receipt(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    admission.budget()
    actor = admission.actor()
    upload_id = admission.upload(actor)
    root = tmp_path / "first-chunk"
    forbid_parent_storage(monkeypatch)
    original_commit = SqlAlchemyVaultUnitOfWork.commit_admitted_upload

    def lost_commit(unit: SqlAlchemyVaultUnitOfWork, *args: object, **kwargs: object) -> None:
        if committed:
            original_commit(unit, *args, **kwargs)  # type: ignore[arg-type]
        raise SQLAlchemyError("synthetic chunk commit reply failure")

    app, coordinator, control = application(admission, actor, root)
    try:
        with TestClient(app) as client:
            headers = {**chunk_headers(), **acquire(client, upload_id)}
            with monkeypatch.context() as patch:
                patch.setattr(SqlAlchemyVaultUnitOfWork, "commit_admitted_upload", lost_commit)
                response = client.patch(
                    f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=PAYLOAD
                )
                assert response.status_code == 503
                idle(coordinator)
            path = root / "staging" / upload_id.hex
            assert path.read_bytes() == PAYLOAD
            with admission.sessions() as session:
                row = present(session.get(UploadSessionRow, upload_id))
                assert row.received_size == (len(PAYLOAD) if committed else 0)
            response = client.patch(
                f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=PAYLOAD
            )
            assert response.status_code == 204
            idle(coordinator)
            assert path.read_bytes() == PAYLOAD
            with admission.sessions() as session:
                assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 1
    finally:
        control.dispose()


def test_missing_acknowledged_prefix_fails_without_reset_or_recreation(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget()
    actor = admission.actor()
    recording = admission.recording(actor)
    app, coordinator, control = application(admission, actor, tmp_path)
    try:
        with TestClient(app) as client:
            upload_id = create_upload(client, recording)
            half = PAYLOAD[:50]
            headers = {
                **chunk_headers(),
                **acquire(client, upload_id),
                "Content-Length": "50",
                "X-Chunk-Sha256": hashlib.sha256(half).hexdigest(),
            }
            assert (
                client.patch(
                    f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=half
                ).status_code
                == 204
            )
            idle(coordinator)
            with admission.sessions() as session:
                key = present(session.get(UploadSessionRow, upload_id)).staging_key
            path = tmp_path / "staging" / key
            path.unlink()  # Simulated loss of this test's disposable acknowledged prefix.
            headers.update({"Upload-Offset": "50", "Upload-Chunk-Index": "1"})
            response = client.patch(
                f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=half
            )
            assert response.status_code == 503
            idle(coordinator)
            assert not path.exists()
            with admission.sessions() as session:
                row = present(session.get(UploadSessionRow, upload_id))
                assert row.received_size == 50 and row.chunk_count == 1 and row.state == "OPEN"
    finally:
        control.dispose()


def test_low_capacity_is_507_in_admitted_child_and_receipt_replay_needs_no_space(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission.budget()
    actor = admission.actor()
    recording = admission.recording(actor)
    root = tmp_path / "capacity"
    assert shutil.disk_usage(tmp_path).free < 1024**4
    reserve = 1024**4
    original_writer = ProcessVaultChunkWriter.__init__

    def with_reserve(
        writer: ProcessVaultChunkWriter,
        child: RetainedVaultProcess,
        registered: ExecutionStatus,
        *,
        root: Path,
        limits: VaultLimits,
        minimum_free_bytes: int = 0,
    ) -> None:
        del minimum_free_bytes
        original_writer(
            writer, child, registered, root=root, limits=limits, minimum_free_bytes=reserve
        )

    monkeypatch.setattr(ProcessVaultChunkWriter, "__init__", with_reserve)
    forbid_parent_storage(monkeypatch)
    app, coordinator, control = application(admission, actor, root)
    try:
        with TestClient(app) as client:
            upload_id = create_upload(client, recording)
            headers = {**chunk_headers(), **acquire(client, upload_id)}
            url = f"/api/v1/vault/uploads/{upload_id}"
            response = client.patch(url, headers=headers, content=PAYLOAD)
            assert response.status_code == 507
            assert response.json()["error"]["code"] == "vault_capacity_low"
            idle(coordinator)
            with admission.sessions() as session:
                row = present(session.get(UploadSessionRow, upload_id))
                assert row.received_size == row.chunk_count == 0
                path = root / "staging" / row.staging_key
            assert not path.exists()
            reserve = 0
            assert client.patch(url, headers=headers, content=PAYLOAD).status_code == 204
            idle(coordinator)
            reserve = 1024**4
            assert client.patch(url, headers=headers, content=PAYLOAD).status_code == 204
            idle(coordinator)
            assert path.read_bytes() == PAYLOAD
            assert client.post(url + "/complete", headers=AUTH).status_code == 202
            with admission.sessions() as session:
                assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 1
    finally:
        control.dispose()
