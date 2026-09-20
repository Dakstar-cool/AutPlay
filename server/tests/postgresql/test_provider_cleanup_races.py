"""Real lock contention and duplicate cleanup converge without surrendering provider bytes."""

from __future__ import annotations

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql import provider_cleanup
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    ProviderStagingRow,
    UploadSessionRow,
)
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.provider_cleanup import ProviderCleanupClaim, ProviderCleanupService
from autplay.domain.resource_admission import AcquisitionClaim
from sqlalchemy import text
from sqlalchemy.orm import Session

from .test_internet_handoff import ready
from .test_provider_cleanup import close
from .test_provider_staging import writer
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


def test_cleanup_waits_for_handoff_then_preserves_published_ownership(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff, owned, target, verified = ready(admission, tmp_path)
    entered, release, cleanup_entered = Event(), Event(), Event()
    transaction = handoff._transaction
    cleanup_lock = lock_resource_admission
    cleanup_pid: list[int] = []

    @contextmanager
    def held(claim: AcquisitionClaim) -> Iterator[tuple[Session, InternetAcquisitionRow]]:
        with transaction(claim) as source:
            entered.set()
            assert release.wait(8)
            yield source

    def observed(session: Session) -> None:
        cleanup_pid.append(present(session.scalar(text("SELECT pg_backend_pid()"))))
        cleanup_entered.set()
        cleanup_lock(session)

    monkeypatch.setattr(handoff, "_transaction", held)
    monkeypatch.setattr(provider_cleanup, "lock_resource_admission", observed)
    repository = PostgresProviderCleanupRepository(admission.sessions)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publish = pool.submit(
            handoff.handoff, owned.claim, target, owned.ticket.execution_id, verified
        )
        try:
            assert entered.wait(5)
            cleanup = pool.submit(repository.claim, owned.ticket.execution_id)
            assert cleanup_entered.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with admission.sessions() as session:
                    blocked = session.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))>0"),
                        {"pid": cleanup_pid[0]},
                    )
                if blocked:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("cleanup did not wait behind the actual handoff transaction")
            assert not cleanup.done()
            assert (tmp_path / "staging" / owned.key.value).exists()
        finally:
            release.set()
        receipt = publish.result(timeout=5)
        assert cleanup.result(timeout=5) is None
    with admission.sessions() as session:
        assert present(session.get(ProviderStagingRow, receipt.execution_id)).state == "HANDED_OFF"
        assert session.get(UploadSessionRow, receipt.upload_id) is not None


def test_two_database_cleanup_claimants_preserve_exact_scratch_once(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = writer(admission, started=False)
    close(admission, owned)
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    workspace = storage.create_workspace(owned.ticket.execution_id)
    (workspace / "audio.part").write_bytes(b"owned partial")
    (tmp_path / "staging" / owned.key.value).write_bytes(b"owned final")
    repository = PostgresProviderCleanupRepository(admission.sessions)
    service = ProviderCleanupService(repository, storage)
    gate = Barrier(2)
    retire = storage.retire

    def together(claim: ProviderCleanupClaim) -> None:
        gate.wait(timeout=5)
        retire(claim)

    monkeypatch.setattr(storage, "retire", together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.cleanup, owned.ticket.execution_id) for _ in range(2)]
        assert all(future.result(timeout=8) for future in futures)
    claim = present(repository.claim(owned.ticket.execution_id))
    assert claim.completed
    assert (
        tmp_path / "provider-retired" / claim.claim_id.hex / "audio.part"
    ).read_bytes() == b"owned partial"
    assert (tmp_path / "quarantine" / claim.quarantine_key.value).read_bytes() == b"owned final"


def test_any_upload_binding_protects_key_even_without_provider_handoff_receipt(
    admission: AdmissionHarness,
) -> None:
    owned = writer(admission, started=False)
    close(admission, owned)
    actor = admission.actor()
    upload_id = admission.upload(actor)
    with admission.sessions.begin() as session:
        # A partial/foreign legacy binding is not permission to remove its bytes.
        present(session.get(UploadSessionRow, upload_id)).staging_key = owned.key.value
    assert (
        PostgresProviderCleanupRepository(admission.sessions).claim(owned.ticket.execution_id)
        is None
    )
    with admission.sessions() as session:
        assert present(session.get(ProviderStagingRow, owned.ticket.execution_id)).state == "EXITED"
