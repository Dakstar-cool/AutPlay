"""Real database authorization and isolated range bytes through the gated HTTP app."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from starlette.testclient import TestClient

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.models import AudioVariantRow, VaultObjectRow, VaultReplicaRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoPermitRow
from autplay.application.auth import AuthService
from autplay.domain.auth import InvalidAccessTokenError, Principal
from autplay.domain.resource_admission import ResourceKind, ResourceRequest
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.entrypoints.composition import build_stream_lookup
from autplay.entrypoints.resource_stream_http import ProcessStreamGateway
from autplay.entrypoints.stream import create_stream_app
from autplay.runtime.settings import StreamSettings
from autplay.runtime.vault_io import VaultIoCoordinator

from .test_resource_admission_runtime import AdmissionHarness, admission, fence, play

__all__ = ["admission"]
PAYLOAD = b"0123456789" * 10000


@dataclass
class Authentication:
    actor: Principal

    def authenticate_access(self, token: str) -> Principal:
        if token != "synthetic-access":
            raise InvalidAccessTokenError()
        return self.actor


def seed_variant(harness: AdmissionHarness, actor: Principal, root: Path) -> tuple[UUID, UUID]:
    recording = harness.recording(actor)
    storage = FilesystemVaultStorage(root)
    staging = OpaqueStorageKey(uuid4().hex)
    storage.create_staging(staging)
    storage.write_chunk(
        staging,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    verified = storage.verify_staging(staging)
    committed = storage.commit_staging(staging, verified)
    with harness.sessions.begin() as session:
        obj = VaultObjectRow(
            sha256=verified.sha256.value,
            byte_size=verified.byte_size,
            detected_mime_type="audio/flac",
            commit_status="COMMITTED",
            committed_at=datetime.now(UTC),
        )
        session.add(obj)
        session.flush()
        session.add(
            VaultReplicaRow(
                vault_object_id=obj.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=committed.storage_key.value,
                replica_status="AVAILABLE",
                verified_at=datetime.now(UTC),
            )
        )
        variant = AudioVariantRow(
            recording_id=recording,
            vault_object_id=obj.vault_object_id,
            codec="flac",
            container="flac",
            sample_rate_hz=48000,
            channels=2,
            duration_ms=1000,
        )
        session.add(variant)
        session.flush()
        return variant.audio_variant_id, recording


def idle(coordinator: VaultIoCoordinator) -> None:
    until = monotonic() + 5
    while coordinator.pending():
        assert monotonic() < until, "owned stream did not release its exact process"
        sleep(0.02)


@pytest.mark.parametrize("resource_type", ["PLAY_INSTANCE", "DOWNLOAD_INTENT"])
def test_gated_http_preserves_ranges_and_heads_without_unadmitted_file_reads(
    admission: AdmissionHarness, tmp_path: Path, resource_type: str
) -> None:
    admission.budget()
    actor = admission.actor()
    variant_id, recording_id = seed_variant(admission, actor, tmp_path)
    request = (
        play()
        if resource_type == "PLAY_INSTANCE"
        else ResourceRequest(uuid4(), ResourceKind.TRANSFER, resource_type, uuid4(), variant_id)
    )
    activation = fence(admission.service.acquire(actor, request))
    if resource_type == "PLAY_INSTANCE":
        admission.service.attach(actor, activation, 0, recording_id, None)
    coordinator = VaultIoCoordinator(admission.service, maximum=2)
    gateway = ProcessStreamGateway(
        coordinator, root=tmp_path, limits=VaultLimits(io_block_bytes=32768)
    )
    settings = StreamSettings(
        database_url=SecretStr("postgresql+psycopg://test:test@127.0.0.1:1/test"),
        auth_signing_secret=SecretStr("synthetic-stream-signing-secret-at-least-32"),
    )
    app = create_stream_app(
        settings,
        lookup=build_stream_lookup(admission.engine),
        auth_service=cast(AuthService, Authentication(actor)),
        process_gateway=gateway,
    )
    address = f"/api/v1/stream/audio-variants/{variant_id}"
    auth = {"Authorization": "Bearer synthetic-access"}
    headers = {
        **auth,
        "AutPlay-Resource-Type": resource_type,
        "AutPlay-Operation-ID": str(activation.operation_id),
        "AutPlay-Activation-ID": str(activation.activation_id),
        "AutPlay-Generation": str(activation.generation),
    }
    with TestClient(app) as client:
        assert client.get(address).status_code == 401
        assert client.head(address, headers=auth).status_code == 200
        assert client.get(address, headers={**auth, "Range": "bytes=200000-"}).status_code == 416
        missing = client.get(address, headers=auth)
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "resource_admission_required"
        assert not coordinator.pending() and not coordinator.supervisor.snapshot()
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0
        full = client.get(address, headers=headers)
        assert full.status_code == 200 and full.content == PAYLOAD
        assert full.headers["cache-control"] == "private, no-store"
        idle(coordinator)
        partial = client.get(address, headers={**headers, "Range": "bytes=17-90000"})
        assert partial.status_code == 206 and partial.content == PAYLOAD[17:90001]
        assert partial.headers["content-range"] == f"bytes 17-90000/{len(PAYLOAD)}"
        idle(coordinator)
        wrong = client.get(address, headers={**headers, "AutPlay-Resource-Type": "UPLOAD_INTENT"})
        assert wrong.status_code == 409
    assert not coordinator.pending() and not coordinator.supervisor.snapshot()
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0
