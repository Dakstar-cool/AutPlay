"""Actual API composition admits before receive and commits chunks in retained workers."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.models import DeviceRow, UploadChunkRow, UploadSessionRow
from autplay.application.auth import AuthService
from autplay.domain.auth import Principal
from autplay.domain.vault import OpaqueStorageKey
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_vault_http_service
from autplay.entrypoints.resource_composition import ResourceIoRuntime
from autplay.runtime.settings import ApiSettings
from autplay.runtime.vault_io import VaultIoCoordinator
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from .resource_io_support import InProcessResourceTransport
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_stream_http import Authentication, idle

__all__ = ["admission"]
PAYLOAD = b"a" * 100


def application(
    harness: AdmissionHarness, actor: Principal, root: Path, *, minimum_free_bytes: int = 0
) -> tuple[FastAPI, VaultIoCoordinator, Engine]:
    settings = ApiSettings(
        database_url=SecretStr(harness.engine.url.render_as_string(hide_password=False)),
        auth_signing_secret=SecretStr("synthetic-upload-signing-secret-at-least-32"),
        public_access_source_hmac_secret=SecretStr("synthetic-upload-source-secret-at-least-32"),
        vault_root=root,
        vault_low_disk_bytes=minimum_free_bytes,
    )
    # Control transactions have their own connection pool even if every upload
    # worker retains a data transaction while waiting for child process exit.
    runtime = ResourceIoRuntime(settings, maximum=2)
    app = create_app(
        settings,
        auth_service=cast(AuthService, Authentication(actor)),
        upload_service=build_vault_http_service(settings, harness.engine),
        resource_runtime=runtime,
    )
    app.add_middleware(InProcessResourceTransport)
    return app, runtime.coordinator, runtime.engine


def chunk_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer synthetic-access",
        "Content-Type": "application/offset+octet-stream",
        "Content-Length": str(len(PAYLOAD)),
        "Upload-Offset": "0",
        "Upload-Chunk-Index": "0",
        "X-Chunk-Sha256": hashlib.sha256(PAYLOAD).hexdigest(),
    }


def acquire(client: TestClient, upload_id: UUID) -> dict[str, str]:
    response = client.post(
        "/api/v1/account/resource-admissions",
        headers={"Authorization": "Bearer synthetic-access"},
        json={
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "kind": "TRANSFER",
            "resource_type": "UPLOAD_INTENT",
            "resource_id": str(upload_id),
            "target_id": str(upload_id),
        },
    )
    assert response.status_code == 200
    status = response.json()
    assert status["state"] == "ACTIVE"
    return {
        "AutPlay-Resource-Type": "UPLOAD_INTENT",
        "AutPlay-Operation-ID": status["operation_id"],
        "AutPlay-Activation-ID": status["activation_id"],
        "AutPlay-Generation": str(status["generation"]),
    }


def test_real_api_chunk_and_exact_retry_commit_one_receipt_with_separate_control_pool(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget()
    actor = admission.actor()
    upload_id = admission.upload(actor)
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey(upload_id.hex)
    app, coordinator, control = application(admission, actor, tmp_path)
    try:
        with TestClient(app) as client:
            headers = {**chunk_headers(), **acquire(client, upload_id)}
            for _ in range(2):
                response = client.patch(
                    f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=PAYLOAD
                )
                assert response.status_code == 204
                assert response.headers["upload-offset"] == "100"
                idle(coordinator)
            with admission.sessions() as session:
                row = present(session.get(UploadSessionRow, upload_id))
                assert row.received_size == 100 and row.chunk_count == 1
                assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 1
            assert storage.staging_path_for_media(key).read_bytes() == PAYLOAD
        assert not app.state.unconfirmed_resource_io
    finally:
        control.dispose()


@pytest.mark.parametrize("failure", ["missing", "revoked", "wrong_target"])
def test_rejected_upload_does_not_receive_body_or_start_child(
    admission: AdmissionHarness, tmp_path: Path, failure: str
) -> None:
    admission.budget()
    actor = admission.actor()
    upload_id = admission.upload(actor)
    app, coordinator, control = application(admission, actor, tmp_path)
    try:
        with TestClient(app) as client:
            headers = chunk_headers()
            if failure != "missing":
                headers.update(acquire(client, upload_id))
            if failure == "wrong_target":
                upload_id = admission.upload(actor)
            if failure == "revoked":
                with admission.sessions.begin() as session:
                    present(session.get(DeviceRow, actor.device_id)).revoked_at = session.scalar(
                        select(func.clock_timestamp())
                    )

            async def request() -> None:
                receives = 0
                sent: list[Message] = []

                async def receive() -> Message:
                    nonlocal receives
                    receives += 1
                    return {"type": "http.request", "body": PAYLOAD, "more_body": False}

                async def send(message: Message) -> None:
                    sent.append(message)

                path = f"/api/v1/vault/uploads/{upload_id}"
                scope: Scope = {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "http_version": "1.1",
                    "scheme": "http",
                    "method": "PATCH",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "root_path": "",
                    "headers": [
                        (name.lower().encode(), value.encode()) for name, value in headers.items()
                    ],
                    "server": ("testserver", 80),
                    "client": ("127.0.0.1", 12345),
                }
                await app(scope, receive, send)
                assert receives == 0
                assert sent[0]["type"] == "http.response.start"
                assert sent[0]["status"] == (409 if failure == "missing" else 403)

            asyncio.run(request())
            idle(coordinator)
            assert not coordinator.supervisor.snapshot()
    finally:
        control.dispose()


@pytest.mark.parametrize("failure", ["hash", "storage"])
def test_admitted_upload_error_retains_only_bounded_diagnostic_time(
    admission: AdmissionHarness,
    tmp_path: Path,
    failure: str,
) -> None:
    admission.budget()
    actor = admission.actor()
    upload_id = admission.upload(actor)
    # A directory at the exact staging leaf triggers a storage error after GO.
    if failure == "storage":
        (tmp_path / "staging" / upload_id.hex).mkdir(parents=True)
    app, coordinator, control = application(admission, actor, tmp_path)
    try:
        with TestClient(app) as client:
            headers = {**chunk_headers(), **acquire(client, upload_id)}
            if failure == "hash":
                headers["X-Chunk-Sha256"] = "0" * 64
            response = client.patch(
                f"/api/v1/vault/uploads/{upload_id}", headers=headers, content=PAYLOAD
            )
            assert response.status_code == (422 if failure == "hash" else 503)
            expected = "upload_chunk_invalid" if failure == "hash" else "vault_storage_unavailable"
            assert response.json()["error"]["code"] == expected
            idle(coordinator)
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 0
    finally:
        control.dispose()
