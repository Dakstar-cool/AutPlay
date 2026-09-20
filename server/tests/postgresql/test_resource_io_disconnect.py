"""Actual child/process charge across disconnect in the complete HTTP application."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.postgresql.models import UploadChunkRow, UploadSessionRow
from autplay.application.auth import AuthService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import ActivationFence, ResourceKind, ResourceRequest
from autplay.domain.vault import OpaqueStorageKey
from autplay.entrypoints.composition import build_stream_lookup
from autplay.entrypoints.resource_stream_http import ProcessStreamBody, ProcessStreamGateway
from autplay.entrypoints.stream import create_stream_app
from autplay.runtime.resource_io_scope import TRANSPORT_EXTENSION
from autplay.runtime.settings import StreamSettings
from autplay.runtime.vault_io import VaultIoCoordinator
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from .resource_io_support import InProcessResourceTransport
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, play, present
from .test_resource_stream_http import Authentication, idle, seed_variant
from .test_resource_upload_http import PAYLOAD, application, chunk_headers
from .test_vault_io_coordinator import assert_closed, eventually

__all__ = ["admission"]


def stream_app(
    harness: AdmissionHarness, actor: Principal, root: Path
) -> tuple[FastAPI, VaultIoCoordinator]:
    coordinator = VaultIoCoordinator(harness.service, maximum=2)
    app = create_stream_app(
        StreamSettings(
            database_url=SecretStr("postgresql+psycopg://test:test@127.0.0.1:1/test"),
            auth_signing_secret=SecretStr("synthetic-stream-secret-at-least-32"),
        ),
        lookup=build_stream_lookup(harness.engine),
        auth_service=cast(AuthService, Authentication(actor)),
        process_gateway=ProcessStreamGateway(coordinator, root=root),
    )
    return app, coordinator


def headers(active: ActivationFence, kind: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer synthetic-access",
        "AutPlay-Resource-Type": kind,
        "AutPlay-Operation-ID": str(active.operation_id),
        "AutPlay-Activation-ID": str(active.activation_id),
        "AutPlay-Generation": str(active.generation),
    }


def request_scope(path: str, method: str, version: str, values: dict[str, str]) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": version},
        "http_version": "1.1",
        "scheme": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in values.items()],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "extensions": {TRANSPORT_EXTENSION: {"abort": lambda: None}},
    }


@pytest.mark.parametrize("version", ["2.0", "2.4"])
@pytest.mark.parametrize("phase", ["open", "read", "upload"])
def test_disconnect_retains_real_execution_and_upload_lock_until_worker_settles(
    admission: AdmissionHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    phase: str,
) -> None:
    admission.budget()
    actor = admission.actor()
    entered, release = threading.Event(), threading.Event()
    control = None
    upload_id: UUID | None = None
    if phase == "upload":
        upload_id = admission.upload(actor)
        FilesystemVaultStorage(tmp_path).create_staging(OpaqueStorageKey(upload_id.hex))
        active = fence(
            admission.service.acquire(
                actor,
                ResourceRequest(
                    uuid4(),
                    ResourceKind.TRANSFER,
                    "UPLOAD_INTENT",
                    upload_id,
                    upload_id,
                ),
            )
        )
        app, coordinator, control = application(admission, actor, tmp_path)
        scope = request_scope(
            f"/api/v1/vault/uploads/{upload_id}",
            "PATCH",
            version,
            {**chunk_headers(), **headers(active, "UPLOAD_INTENT")},
        )
        original_result = RetainedVaultProcess.read_result

        def held_result(child: RetainedVaultProcess) -> tuple[bytes, bytes]:
            entered.set()
            assert release.wait(10)
            return original_result(child)

        monkeypatch.setattr(RetainedVaultProcess, "read_result", held_result)
    else:
        variant, recording = seed_variant(admission, actor, tmp_path)
        active = fence(admission.service.acquire(actor, play()))
        admission.service.attach(actor, active, 0, recording, None)
        app, coordinator = stream_app(admission, actor, tmp_path)
        scope = request_scope(
            f"/api/v1/stream/audio-variants/{variant}",
            "GET",
            version,
            headers(active, "PLAY_INSTANCE"),
        )
        if phase == "open":
            original_open = ProcessStreamBody.open_in_worker

            def held_open(body: ProcessStreamBody) -> None:
                entered.set()
                assert release.wait(10)
                original_open(body)

            monkeypatch.setattr(ProcessStreamBody, "open_in_worker", held_open)
        else:
            original_next = ProcessStreamBody._next_in_worker

            def held_next(body: ProcessStreamBody) -> bytes | None:
                entered.set()
                assert release.wait(10)
                return original_next(body)

            monkeypatch.setattr(ProcessStreamBody, "_next_in_worker", held_next)

    async def scenario() -> None:
        disconnected = asyncio.Event()
        sent: list[Message] = []
        receives = concurrent = maximum = 0

        async def receive() -> Message:
            nonlocal receives, concurrent, maximum
            receives += 1
            concurrent += 1
            maximum = max(maximum, concurrent)
            try:
                if receives == 1:
                    return {
                        "type": "http.request",
                        "body": PAYLOAD if phase == "upload" else b"",
                        "more_body": False,
                    }
                await disconnected.wait()
                return {"type": "http.disconnect"}
            finally:
                concurrent -= 1

        async def send(message: Message) -> None:
            sent.append(message)

        async with app.router.lifespan_context(app):
            running = asyncio.create_task(app(scope, receive, send))
            try:
                await eventually(entered.is_set)
                owned = coordinator.pending()
                assert len(owned) == 1 and owned[0].registered.child is not None
                disconnected.set()
                await asyncio.wait_for(running, 1)
                assert owned[0].deadline.stopped() and coordinator.pending()
                admission.service.release(actor, active)
                assert admission.service.poll(actor, active.operation_id).usage.server == 1
                assert maximum == 1
                if phase == "read":
                    assert [
                        item["status"] for item in sent if item["type"] == "http.response.start"
                    ] == [200]
                else:
                    assert not sent
                if upload_id is not None:
                    with pytest.raises(DBAPIError), admission.sessions.begin() as contender:
                        contender.get(UploadSessionRow, upload_id, with_for_update={"nowait": True})
            finally:
                disconnected.set()
                release.set()
                await asyncio.wait_for(running, 2)

    try:
        asyncio.run(scenario())
        assert_closed(admission, coordinator)
        if upload_id is not None:
            with admission.sessions() as session:
                assert present(session.get(UploadSessionRow, upload_id)).received_size == 0
                assert session.scalar(select(func.count()).select_from(UploadChunkRow)) == 0
    finally:
        release.set()
        if control is not None:
            control.dispose()


def test_child_exit_before_opened_returns_stable_retryable_503(
    admission: AdmissionHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission.budget()
    actor = admission.actor()
    variant, recording = seed_variant(admission, actor, tmp_path)
    active = fence(admission.service.acquire(actor, play()))
    admission.service.attach(actor, active, 0, recording, None)
    app, coordinator = stream_app(admission, actor, tmp_path)
    app.add_middleware(InProcessResourceTransport)
    original = RetainedVaultProcess.read_result

    def exited_before_open(child: RetainedVaultProcess) -> tuple[bytes, bytes]:
        assert child._process is not None
        child._process.kill()
        # Consume any raced OPENED frame, then actual pipe EOF from the killed child.
        while True:
            original(child)

    monkeypatch.setattr(RetainedVaultProcess, "read_result", exited_before_open)
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/stream/audio-variants/{variant}", headers=headers(active, "PLAY_INSTANCE")
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "vault_stream_unavailable"
        assert response.json()["error"]["retryable"] is True
        idle(coordinator)
    assert_closed(admission, coordinator)
