"""Real retained process/tree evidence with deliberately blocked control transactions."""

import io
import math
import os
import struct
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import ingest_child_launch
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.domain.ingest_execution import IngestExecutionStatus, IngestExecutionTicket
from autplay.domain.jobs import LeaseFence
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest
from autplay.runtime.ingest_io import IngestProcessCoordinator, IngestWork
from process_tree_support import process_tree_factory, wait_marker
from sqlalchemy.exc import SQLAlchemyError
from test_ingest_child import KEY, setup_storage


class Repository:
    def __init__(self, *, seconds: float = 5, fault: str = "") -> None:
        self.seconds, self.fault = seconds, fault
        self.current: IngestExecutionStatus | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.proofs: list[ProcessExitEvidence] = []
        self.renewals = 0

    def prepare(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus:
        self.current = IngestExecutionStatus(ticket, ExecutionState.PREPARED, None, None)
        if self.fault == "prepare_lost":
            raise SQLAlchemyError("synthetic lost commit reply")
        return self.current

    def start(self, ticket: IngestExecutionTicket, child: ProcessIdentity) -> IngestExecutionStatus:
        now = datetime.now(UTC)
        self.current = IngestExecutionStatus(
            ticket, ExecutionState.RUNNING, child, now + timedelta(seconds=self.seconds), now
        )
        if self.fault == "start_lost":
            raise SQLAlchemyError("synthetic lost commit reply")
        return self.current

    def renew(self, ticket: IngestExecutionTicket, child: ProcessIdentity) -> IngestExecutionStatus:
        self.renewals += 1
        if self.fault == "renew_blocked":
            self.entered.set()
            assert self.release.wait(10)
        return self.start(ticket, child)

    def status(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus | None:
        assert self.current is None or self.current.ticket == ticket
        return self.current

    def reconcile(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus | None:
        return self.status(ticket)

    def confirm(
        self, ticket: IngestExecutionTicket, proof: ProcessExitEvidence
    ) -> IngestExecutionStatus:
        assert self.current is not None and self.current.ticket == ticket
        assert self.current.child == proof.child
        if self.proofs:
            assert self.proofs[-1] == proof
        self.proofs.append(proof)
        self.current = replace(self.current, state=ExecutionState.CLOSED)
        if self.fault == "confirm_lost" and not self.release.is_set():
            self.entered.set()
            raise SQLAlchemyError("synthetic lost commit reply")
        return self.current


def ticket() -> IngestExecutionTicket:
    return IngestExecutionTicket(
        uuid4(), uuid4(), uuid4(), KEY, LeaseFence(uuid4(), "ingest-test", 1)
    )


def wait_empty(coordinator: IngestProcessCoordinator) -> None:
    until = monotonic() + 5
    while coordinator.pending():
        assert monotonic() < until
        sleep(0.01)


def stuck_launch() -> tuple[list[str], dict[str, str]]:
    arguments, environment = ingest_child_launch()
    return [
        arguments[0],
        "-I",
        str(Path(__file__).parents[1] / "fixtures" / "ingest_tree_child.py"),
    ], environment


def test_actual_work_and_freeze_retain_metadata_phase_until_exit(tmp_path: Path) -> None:
    _, expected = setup_storage(tmp_path)
    repository = Repository()
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )

    def action(work: IngestWork) -> int:
        assert work.storage.available_bytes() > 0
        assert work.storage.verify_staging(KEY) == expected
        work.freeze_renewals()
        assert repository.current is not None and repository.current.state == ExecutionState.RUNNING
        assert not repository.proofs
        return expected.byte_size

    assert coordinator.run(ticket(), action) == expected.byte_size
    assert not coordinator.pending()
    assert repository.proofs[-1].kind == ExitKind.PROCESS_EXIT
    assert repository.proofs[-1].exit_code == 0
    assert (tmp_path / "staging" / KEY.value).exists()


@pytest.mark.parametrize("fault", ["prepare_lost", "start_lost"])
def test_uncertain_start_reconciles_exact_ticket_without_go(tmp_path: Path, fault: str) -> None:
    root = tmp_path / "never-mounted"
    repository = Repository(fault=fault)
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(root), tree_factory=process_tree_factory()
    )
    called: list[bool] = []
    with pytest.raises(ResourceAdmissionError, match="ingest_execution_unavailable"):
        coordinator.run(ticket(), lambda work: called.append(True))
    assert not root.exists() and not called and not coordinator.pending()
    assert repository.proofs[-1].kind == (
        ExitKind.NOT_STARTED if fault == "prepare_lost" else ExitKind.PROCESS_EXIT
    )


def test_lost_exit_ack_keeps_capacity_until_same_proof_is_acknowledged(tmp_path: Path) -> None:
    setup_storage(tmp_path)
    repository = Repository(fault="confirm_lost")
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    first = ticket()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.run, first, lambda work: work.storage.verify_staging(KEY))
        try:
            assert repository.entered.wait(5)
            assert coordinator.pending() == (first.execution_id,)
            with pytest.raises(ResourceAdmissionError, match="ingest_execution_busy"):
                coordinator.run(ticket(), lambda work: None)
            assert len(repository.proofs) >= 1
        finally:
            repository.release.set()
        assert future.result(timeout=5).byte_size > 0
    assert not coordinator.pending()


@pytest.mark.parametrize("fault", ["renew_blocked", "short_grant"])
def test_watchdog_stops_blocked_pipe_and_detached_tree_independently_of_db(
    tmp_path: Path, fault: str
) -> None:
    setup_storage(tmp_path)
    repository = Repository(seconds=1 if fault == "renew_blocked" else 0.4, fault=fault)
    coordinator = IngestProcessCoordinator(
        repository,
        IngestChildSettings(tmp_path),
        tree_factory=process_tree_factory(),
        launch=stuck_launch,
    )
    first = ticket()
    started = monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.run, first, lambda work: work.storage.verify_staging(KEY))
        try:
            wait_marker(tmp_path / "ingest-descendant")
            if fault == "renew_blocked":
                assert repository.entered.wait(3)
            with pytest.raises((ResourceAdmissionError, ValueError)):
                future.result(timeout=4)
            assert monotonic() - started < 4
            if fault == "renew_blocked":
                assert coordinator.pending() == (first.execution_id,)
                assert not repository.proofs
        finally:
            repository.release.set()
            coordinator.shutdown(timeout=5)
    wait_empty(coordinator)
    assert repository.proofs[-1].kind == ExitKind.PROCESS_EXIT
    assert repository.proofs[-1].exit_code != 0


def test_shutdown_waits_for_whole_metadata_callback_not_just_child_exit(tmp_path: Path) -> None:
    setup_storage(tmp_path)
    repository = Repository()
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )
    entered, release = threading.Event(), threading.Event()

    def action(work: IngestWork) -> None:
        work.storage.verify_staging(KEY)
        entered.set()
        assert release.wait(10)

    first = ticket()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.run, first, action)
        try:
            assert entered.wait(5)
            assert coordinator.shutdown(timeout=0.1) == (first.execution_id,)
            assert not repository.proofs
            with pytest.raises(ResourceAdmissionError):
                future.result(timeout=2)
        finally:
            release.set()
    wait_empty(coordinator)
    assert repository.proofs[-1].kind == ExitKind.PROCESS_EXIT


@pytest.mark.skipif(os.name == "nt", reason="pinned media executables are verified in Linux image")
def test_actual_pinned_media_tools_publish_cas_inside_owned_tree(tmp_path: Path) -> None:
    import hashlib

    from autplay.adapters.filesystem.vault import FilesystemVaultStorage
    from autplay.domain.vault import VaultLimits

    source = io.BytesIO()
    with wave.open(source, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(16000 * math.sin(index * 440 * 2 * math.pi / 22050)))
                for index in range(22050 * 12)
            )
        )
    payload = source.getvalue()
    storage = FilesystemVaultStorage(tmp_path, limits=VaultLimits())
    storage.create_staging(KEY)
    storage.write_chunk(
        KEY,
        offset=0,
        payload=payload,
        payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
    )
    repository = Repository()
    coordinator = IngestProcessCoordinator(
        repository, IngestChildSettings(tmp_path), tree_factory=process_tree_factory()
    )

    def action(work: IngestWork) -> OpaqueStorageKey:
        verified = work.storage.verify_staging(KEY)
        path = work.storage.staging_path_for_media(KEY)
        metadata = work.storage.inspect(path)
        evidence = work.storage.fingerprint(path)
        assert metadata.duration_ms == 12000 and metadata.sample_rate_hz == 22050
        assert (
            evidence.algorithm == "chromaprint"
            and evidence.algorithm_version == "1.6.1"
            and evidence.payload
        )
        published = work.storage.commit_staging(KEY, verified)
        work.freeze_renewals()
        return published.storage_key

    key = coordinator.run(ticket(), action)
    assert storage.verify_object(key) == storage.verify_staging(KEY)
    assert repository.proofs[-1].exit_code == 0 and not coordinator.pending()
