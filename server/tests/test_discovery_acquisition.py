"""Provider-call ordering evidence for fail-closed A1B acquisition."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.discovery_runtime import (
    AcquisitionTarget,
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.application.discovery_acquisition import DiscoveryAcquisitionHandler
from autplay.application.job_worker import JobExecutionContext
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.domain.discovery import StagedAcquisition
from autplay.domain.jobs import JobKey, JobLease, LeaseFence, TerminalJobError
from autplay.domain.vault import ChunkWriteResult, Sha256Digest, VaultLimits, VerifiedStagedFile
from autplay.ports.vault import VaultStorage
from sqlalchemy.orm import Session


class _Session:
    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def commit(self) -> None:
        return None


class _Discovery:
    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self, *_: object, **__: object) -> object:
        self.acquire_calls += 1
        return None


class _Context:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[object, int | None, int | None]] = []

    def checkpoint(
        self,
        value: object,
        *,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> None:
        self.checkpoints.append((value, progress_current, progress_total))

    def raise_if_cancelled(self) -> None:
        return None


def test_repository_authorization_failure_prevents_every_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_id = uuid4()
    owner_id = uuid4()
    discovery = _Discovery()

    def reject_claim(self: PostgresBulkDiscoveryRepository, **_: object) -> None:
        del self
        raise BulkDiscoveryError("source_authorization_unavailable")

    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "claim_acquisition", reject_claim)
    handler = DiscoveryAcquisitionHandler(
        lambda: cast(Session, _Session()),
        discovery=cast(ManualDiscoveryService, discovery),
        storage=cast(VaultStorage, object()),
        limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=256),
    )
    monkeypatch.setattr(handler, "_record_failure", lambda *_args, **_kwargs: None)
    lease = JobLease(
        fence=LeaseFence(uuid4(), "a1b-test-worker", 1),
        key=JobKey("discovery.acquire", 1),
        user_id=owner_id,
        priority=3,
        payload={"candidate_id": str(candidate_id)},
        checkpoint=None,
        lease_deadline=datetime.now(UTC) + timedelta(minutes=1),
        cancel_requested_at=None,
    )

    with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
        handler(cast(JobExecutionContext, object()), lease)

    assert discovery.acquire_calls == 0


def test_operator_gate_at_claim_blocks_automatic_acquisition_without_provider_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_id, owner_id = uuid4(), uuid4()
    discovery = _Discovery()

    def reject_claim(self: PostgresBulkDiscoveryRepository, **_: object) -> None:
        del self
        raise BulkDiscoveryError("automation_not_active")

    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "claim_acquisition", reject_claim)
    handler = DiscoveryAcquisitionHandler(
        lambda: cast(Session, _Session()),
        discovery=cast(ManualDiscoveryService, discovery),
        storage=cast(VaultStorage, object()),
        limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=256),
        automatic_enabled=lambda: False,
    )
    monkeypatch.setattr(handler, "_record_failure", lambda *_args, **_kwargs: None)
    lease = _lease(candidate_id, owner_id)

    with pytest.raises(TerminalJobError, match="automation_not_active"):
        handler(cast(JobExecutionContext, _Context()), lease)

    assert discovery.acquire_calls == 0


def test_old_automatic_attempt_is_rechecked_before_provider_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed old lease cannot call the provider after fresh manual lineage supersedes it."""

    candidate_id, owner_id, old_attempt_id = uuid4(), uuid4(), uuid4()
    discovery = _Discovery()

    def claim(self: PostgresBulkDiscoveryRepository, **_: object) -> AcquisitionTarget:
        del self
        return AcquisitionTarget(candidate_id, old_attempt_id, owner_id, "10", "AUTOMATIC")

    def reject_before_acquire(self: PostgresBulkDiscoveryRepository, **_: object) -> None:
        del self
        raise BulkDiscoveryError("source_authorization_unavailable")

    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "claim_acquisition", claim)
    monkeypatch.setattr(
        PostgresBulkDiscoveryRepository, "require_before_acquire", reject_before_acquire
    )
    handler = DiscoveryAcquisitionHandler(
        lambda: cast(Session, _Session()),
        discovery=cast(ManualDiscoveryService, discovery),
        storage=cast(VaultStorage, object()),
        limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=256),
        automatic_enabled=lambda: True,
    )
    monkeypatch.setattr(handler, "_record_failure", lambda *_args, **_kwargs: None)

    context = _Context()
    with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
        handler(cast(JobExecutionContext, context), _lease(candidate_id, owner_id))

    assert discovery.acquire_calls == 0
    assert context.checkpoints == [({"stage": "ACQUIRING"}, 0, 3)]


def test_successful_acquisition_persists_three_stage_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate_id, owner_id, attempt_id = uuid4(), uuid4(), uuid4()
    source = tmp_path / "staged.mp3"
    source.write_bytes(b"audio-bytes")
    staged = StagedAcquisition("operation", "10", "audio.mp3", "attribution.json", 11)

    class SuccessfulDiscovery(_Discovery):
        def acquire(self, *_: object, **__: object) -> StagedAcquisition:
            self.acquire_calls += 1
            return staged

        def staged_audio_path(self, *_: object) -> Path:
            return source

        def lookup_for_acquisition(self, *_: object) -> object:
            return object()

    class Storage:
        def create_staging(self, *_: object) -> None:
            return None

        def truncate_staging(self, *_: object) -> None:
            return None

        def write_chunk(self, _key: object, *, offset: int, payload: bytes, **_: object) -> object:
            return ChunkWriteResult(offset + len(payload), False)

        def verify_staging(self, *_: object) -> VerifiedStagedFile:
            return VerifiedStagedFile(11, Sha256Digest(b"v" * 32))

    def claim(self: PostgresBulkDiscoveryRepository, **_: object) -> AcquisitionTarget:
        del self
        return AcquisitionTarget(candidate_id, attempt_id, owner_id, "10", "MANUAL")

    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "claim_acquisition", claim)
    monkeypatch.setattr(
        PostgresBulkDiscoveryRepository, "require_before_acquire", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        PostgresBulkDiscoveryRepository, "prepare_ingest", lambda *_args, **_kwargs: None
    )
    discovery = SuccessfulDiscovery()
    handler = DiscoveryAcquisitionHandler(
        lambda: cast(Session, _Session()),
        discovery=cast(ManualDiscoveryService, discovery),
        storage=cast(VaultStorage, Storage()),
        limits=VaultLimits(max_object_bytes=64, max_chunk_bytes=4),
    )
    context = _Context()

    handler(cast(JobExecutionContext, context), _lease(candidate_id, owner_id))

    assert context.checkpoints == [
        ({"stage": "ACQUIRING"}, 0, 3),
        ({"stage": "DOWNLOADED"}, 1, 3),
        ({"stage": "VAULT_STAGED"}, 2, 3),
        ({"stage": "INGEST_QUEUED"}, 3, 3),
    ]


def _lease(candidate_id: UUID, owner_id: UUID) -> JobLease:
    return JobLease(
        fence=LeaseFence(uuid4(), "a1c-test-worker", 1),
        key=JobKey("discovery.acquire", 1),
        user_id=owner_id,
        priority=3,
        payload={"candidate_id": str(candidate_id)},
        checkpoint=None,
        lease_deadline=datetime.now(UTC) + timedelta(minutes=1),
        cancel_requested_at=None,
    )
