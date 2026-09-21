"""Real database and filesystem proof for receipt publication and replay."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    DeviceRow,
    LibraryEntryRow,
    SyncEventRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoExecutionRow,
    ResourceIoPermitRow,
)
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.imports import ImportJobHandler
from autplay.application.job_worker import JobHandlerRegistry, JobWorker
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.jobs import JobKey
from autplay.domain.vault import AudioTechnicalMetadata, ChromaprintEvidence
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

_PATH = Path(__file__).resolve().parents[3] / "tools/acquisition_vault_bridge.py"
_SPEC = importlib.util.spec_from_file_location("acquisition_vault_bridge", _PATH)
assert _SPEC and _SPEC.loader
bridge = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bridge
_SPEC.loader.exec_module(bridge)


@pytest.mark.usefixtures("internal_io_budget")
def test_receipt_replay_publishes_one_playable_entry(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTPLAY_DATABASE_URL", database_url)
    monkeypatch.setenv("AUTPLAY_AUTH_SIGNING_SECRET", "test-only-auth-" * 4)
    monkeypatch.setenv("AUTPLAY_PUBLIC_ACCESS_SOURCE_HMAC_SECRET", "test-only-source-" * 4)
    monkeypatch.setenv("AUTPLAY_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("AUTPLAY_VAULT_LOW_DISK_BYTES", "0")
    owner = uuid4()
    engine = create_engine(database_url)
    with Session(engine) as session:
        session.add(UserAccountRow(user_id=owner, display_name="owner", role="OWNER"))
        session.commit()
    audio_dir = tmp_path / "music/tracks/key/fixture"
    audio_dir.mkdir(parents=True)
    payload = b"immutable bridge test bytes"
    (audio_dir / "sample.flac").write_bytes(payload)
    receipt_path = audio_dir.parent / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "key": "key",
                "provider": "fixture",
                "filename": "sample.flac",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "duration_seconds": 2,
                "item": {"title": "Song", "artist": "Artist", "album": None},
            }
        )
    )
    receipt = bridge.read_receipt(receipt_path, tmp_path / "music")
    backend = bridge.Backend(owner, provision=True)

    class Media:
        def inspect(self, path: Path) -> AudioTechnicalMetadata:
            assert path.read_bytes() == payload
            return AudioTechnicalMetadata("flac", "flac", 48000, 2, 2000, None, 16)

    class Fingerprints:
        def fingerprint(self, path: Path) -> ChromaprintEvidence:
            assert path.read_bytes() == payload
            return ChromaprintEvidence("chromaprint", "1.6.1", 2000, b"fixture")

    ingest = VaultIngestHandler(
        repository=TransactionalIngestRepository(
            SqlAlchemyVaultUnitOfWorkFactory(backend.sessions)
        ),
        storage=FilesystemVaultStorage(tmp_path / "vault"),
        media=Media(),
        fingerprints=Fingerprints(),
    )
    worker = JobWorker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(backend.sessions),
        worker_id="bridge-test",
        registry=JobHandlerRegistry(
            {
                JobKey("library.import", 1): ImportJobHandler(backend.sessions),
                JobKey("vault.ingest", 1): ingest,
            }
        ),
    )
    checkpoint: dict[str, Any] = {
        "state": "DISCOVERED",
        "signature": list(receipt.signature),
    }
    try:
        for _ in range(20):
            previous = dict(checkpoint)
            checkpoint = bridge.advance_checkpoint(backend, receipt, previous)
            # Simulate process death between the external commit and checkpoint persistence.
            replay = bridge.advance_checkpoint(backend, receipt, previous)
            for key in ("import_id", "recording_id", "ref_id", "upload_id", "variant_id"):
                if key in checkpoint:
                    assert replay[key] == checkpoint[key]
            worker.run_once()
            if checkpoint["state"] == "PUBLISHED":
                break
        assert checkpoint["state"] == "PUBLISHED"
        with backend.sessions() as session:
            assert session.scalar(select(func.count()).select_from(LibraryEntryRow)) == 1
            assert session.scalar(select(func.count()).select_from(UserSessionRow)) == 0
            admissions = session.scalars(select(ResourceAdmissionRow)).all()
            assert admissions
            assert {row.authority_kind for row in admissions} == {"LOCAL_BRIDGE"}
            assert {row.resource_type for row in admissions} == {"UPLOAD_INTENT"}
            assert {row.state for row in admissions} == {"RELEASED"}
            assert session.scalar(select(func.count()).select_from(ResourceIoPermitRow)) == 0
            assert session.scalar(select(func.count()).select_from(ResourceIoExecutionRow)) == 0
            published = session.scalars(
                select(SyncEventRow).where(SyncEventRow.event_type == "LIBRARY_ENTRY_UPSERTED")
            ).all()
            assert len(published) == 1
            assert published[0].payload["server_user_track_ref_id"] == checkpoint["ref_id"]
            assert published[0].payload["availability_status"] == "VAULT"
        # Tampered completed bytes never enter import or upload.
        (audio_dir / "sample.flac").write_bytes(b"x" * len(payload))
        with pytest.raises(bridge.ReceiptError, match="integrity_mismatch"):
            bridge.verify_audio(receipt)
        with backend.sessions.begin() as session:
            device = session.get(DeviceRow, backend.device)
            assert device is not None
            device.revoked_at = session.scalar(select(func.clock_timestamp()))
        with pytest.raises(bridge.ReceiptError, match="bridge_device_not_active"):
            backend.authorize()
    finally:
        backend.close()
        engine.dispose()


def test_new_completion_preempts_full_backfill(tmp_path: Path) -> None:
    receipts = {}
    for i in range(4):
        path = tmp_path / str(i)
        path.write_text("receipt")
        os.utime(path, (i + 1, i + 1))
        receipts[str(i)] = bridge.Receipt(path, path, str(i), "", 0, b"", (0, 0))
    checkpoints = {str(i): {"state": "INGESTING"} for i in range(3)}
    assert bridge.select_work(receipts, checkpoints, 3) == ["3", "2", "1"]
    assert checkpoints["0"]["state"] == "INGESTING"


def test_ready_publication_does_not_wait_for_unrelated_upload() -> None:
    slow_upload: Future[dict[str, Any]] = Future()
    ready_publication: Future[dict[str, Any]] = Future()
    ready_publication.set_result({"state": "PUBLISHED"})
    in_flight = {"slow": slow_upload, "ready": ready_publication}
    assert bridge.completed_work(in_flight) == {"ready": {"state": "PUBLISHED"}}
    assert set(in_flight) == {"slow"}
    assert not slow_upload.done()
    assert bridge.completed_work(in_flight) == {}


@pytest.mark.parametrize("document", [[], {"schema_version": 1, "key": "key", "item": None}])
def test_malformed_receipt_is_isolated(tmp_path: Path, document: object) -> None:
    directory = tmp_path / "tracks/key/provider"
    directory.mkdir(parents=True)
    (directory / "audio").write_bytes(b"x")
    if isinstance(document, dict):
        document.update(provider="provider", filename="audio", bytes=1, sha256="0" * 64)
    path = directory.parent / "receipt.json"
    path.write_text(json.dumps(document))
    with pytest.raises(bridge.ReceiptError):
        bridge.read_receipt(path, tmp_path)
