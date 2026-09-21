"""Real metadata admission, revocation, publication rollback and retained provider ownership."""

import hashlib
import io
import math
import os
import struct
import wave
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.child_process import metadata_child_launch
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.internal_io import PostgresInternalIoBudget, internal_io_usage
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.metadata_execution import (
    MetadataStatus,
    PostgresMetadataExecutionRepository,
)
from autplay.adapters.postgresql.models import (
    JobRow,
    LibraryEntryRow,
    UploadSessionRow,
    UserAccountRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.metadata_execution import (
    MetadataExecutionRow,
    MetadataProviderGateRow,
)
from autplay.adapters.postgresql.models.track_metadata import (
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost, JobResourceWait
from autplay.application.music_library import MusicLibraryService
from autplay.application.track_metadata import METADATA_JOB, TrackMetadataService
from autplay.domain.auth import Principal
from autplay.domain.jobs import JobLease
from autplay.domain.metadata_execution import MetadataExecutionTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.track_metadata import FieldEvidence
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits, VerifiedStagedFile
from autplay.entrypoints import metadata_worker
from autplay.runtime.metadata_io import MetadataProcessCoordinator, MetadataWork
from autplay.runtime.settings import WorkerSettings
from process_tree_support import process_tree_factory, wait_marker
from pydantic import SecretStr
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_ingest_cleanup import IDENTITY, finalized
from .test_internal_io import inventory_ticket
from .test_resource_admission_runtime import present
from .test_track_metadata import setup
from .test_vault_ingest_fence import IngestFixture, ingest

__all__ = ["ingest"]


@pytest.fixture
def metadata_budget(
    internal_io_budget: None, database_harness: DatabaseHarness, database_name: str
) -> None:
    """Explicit synthetic nine-path coverage, never a measured deployment report."""
    with database_harness.connect(database_name) as connection:
        connection.execute("UPDATE account.internal_io_policy SET workload_version=2")
        connection.commit()


@dataclass
class MetadataFixture:
    sessions: sessionmaker[Session]
    principal: Principal
    ref_id: UUID
    service: TrackMetadataService
    lease: JobLease
    context: JobExecutionContext
    repository: PostgresMetadataExecutionRepository
    ticket: MetadataExecutionTicket


@pytest.fixture
def metadata_work(database_url: str) -> Iterator[MetadataFixture]:
    engine, sessions, principal, ref_id, service = setup(database_url)
    try:
        assert service.enqueue_missing() == 1
        with sessions.begin() as session:
            lease = PostgresJobRepository(session).claim(
                worker_id="metadata-proof",
                supported=(METADATA_JOB,),
                lease_interval=timedelta(seconds=60),
                limit=1,
            )[0]
        context = JobExecutionContext(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            fence=lease.fence,
            lease_interval=timedelta(seconds=60),
        )
        repository = PostgresMetadataExecutionRepository(sessions)
        generation = lease.payload["generation"]
        assert isinstance(generation, int)
        ticket = repository.plan(ref_id, generation, lease.fence, uuid4())
        yield MetadataFixture(
            sessions, principal, ref_id, service, lease, context, repository, ticket
        )
    finally:
        engine.dispose()


@pytest.mark.usefixtures("internal_io_budget")
def test_old_eight_path_measurement_cannot_enable_metadata(metadata_work: MetadataFixture) -> None:
    item = metadata_work
    with pytest.raises(ResourceAdmissionError, match="metadata_budget_unconfigured"):
        item.repository.prepare(item.ticket)
    with pytest.raises(JobResourceWait):
        item.repository.defer(item.ticket)
    with item.sessions() as session:
        assert internal_io_usage(session) == 0
        job = present(session.get(JobRow, item.lease.fence.job_id))
        assert job.resource_waiting and job.resource_wait_count == 1


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
@pytest.mark.usefixtures("metadata_budget")
def test_offline_restore_drain_closes_metadata_and_releases_provider_gate(
    metadata_work: MetadataFixture,
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    running = item.repository.start(
        item.ticket,
        ProcessIdentity(4_000_000_000, b"o" * 32),
    )
    request_id = uuid4()
    assert item.repository.provider_begin(running, request_id)

    report = PostgresOfflineExecutionDrain(
        item.sessions,
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()

    assert report.metadata_executions == report.checked_pids == 1
    status = item.repository.status(item.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with item.sessions() as session:
        row = present(session.get(MetadataExecutionRow, item.ticket.execution_id))
        gate = present(session.get(MetadataProviderGateRow, 1))
        assert row.closure_kind == "SUPERVISOR_EXIT" and row.exit_code == 137
        assert gate.execution_id is None and gate.request_id is None


@pytest.mark.usefixtures("metadata_budget")
def test_metadata_and_maintenance_race_for_one_shared_slot(metadata_work: MetadataFixture) -> None:
    item, barrier = metadata_work, Barrier(2)
    PostgresInternalIoBudget(item.sessions).set_limit(uuid4(), 2, 1)
    maintenance = PostgresProviderMaintenanceRepository(item.sessions)
    ticket = inventory_ticket()

    def enter(kind: str) -> str:
        barrier.wait(timeout=5)
        try:
            if kind == "metadata":
                item.repository.prepare(item.ticket)
            else:
                maintenance.prepare(ticket)
            return "ADMITTED"
        except ResourceAdmissionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(enter, ("metadata", "maintenance"))) == [
            "ADMITTED",
            "internal_io_busy",
        ]
    with item.sessions() as session:
        assert internal_io_usage(session) == 1


@pytest.mark.usefixtures("metadata_budget")
def test_first_start_renew_immutable_snapshot_and_limit_lowering(
    metadata_work: MetadataFixture,
) -> None:
    item = metadata_work
    prepared = item.repository.prepare(item.ticket)
    assert prepared.state == ExecutionState.PREPARED and item.ticket.audio is None
    running = item.repository.start(item.ticket, IDENTITY)
    assert running.state == ExecutionState.RUNNING and 0 < running.authorized_seconds <= 5
    scanner = inventory_ticket()
    maintenance = PostgresProviderMaintenanceRepository(item.sessions)
    maintenance.prepare(scanner)
    PostgresInternalIoBudget(item.sessions).set_limit(uuid4(), 2, 1)
    assert item.repository.renew(item.ticket, IDENTITY).state == ExecutionState.RUNNING
    with pytest.raises(DBAPIError), item.sessions.begin() as session:
        present(session.get(MetadataExecutionRow, item.ticket.execution_id)).generation += 1
    with item.sessions() as session:
        assert internal_io_usage(session) == 2
    proof = ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY)
    assert item.repository.confirm(item.ticket, proof).state == ExecutionState.CLOSED
    assert item.repository.confirm(item.ticket, proof).state == ExecutionState.CLOSED
    maintenance.confirm(scanner, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))


@pytest.mark.usefixtures("metadata_budget")
@pytest.mark.parametrize("change", ["account", "refresh", "library"])
def test_revocation_prevents_renewal_and_result_publication(
    metadata_work: MetadataFixture, change: str
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    if change == "refresh":
        current = item.service.get(item.principal, item.ref_id)
        item.service.command(
            item.principal,
            item.ref_id,
            operation_id=uuid4(),
            expected_revision=current["revision"],
            action="REFRESH",
        )
    else:
        with item.sessions.begin() as session:
            if change == "account":
                present(
                    session.get(UserAccountRow, item.principal.user_id)
                ).authority_generation += 1
            else:
                session.execute(
                    text(
                        "UPDATE library.library_entry SET removed_at=clock_timestamp() "
                        "WHERE user_track_ref_id=:ref"
                    ),
                    {"ref": item.ref_id},
                )
    with pytest.raises(JobLeaseLost):
        item.repository.renew(item.ticket, IDENTITY)
    with pytest.raises(JobLeaseLost):
        TrackMetadataService(item.sessions, execution=running).apply_worker(
            item.ref_id, item.ticket.generation, item.context, state="READY"
        )
    item.repository.confirm(
        item.ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY)
    )


@pytest.mark.usefixtures("metadata_budget")
def test_expiry_during_final_publication_rolls_back_all_metadata(
    metadata_work: MetadataFixture,
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    with item.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE library.metadata_execution "
                "SET io_deadline_at=clock_timestamp()+interval '500 milliseconds'"
            )
        )
    delayed = False

    def hold_revision(session: Session, context: object) -> None:
        del context
        nonlocal delayed
        if not delayed and any(isinstance(row, TrackMetadataRevisionRow) for row in session.new):
            delayed = True
            sleep(0.6)

    event.listen(Session, "after_flush", hold_revision)
    try:
        with pytest.raises(DBAPIError, match="metadata_execution_stale"):
            TrackMetadataService(item.sessions, execution=running).apply_worker(
                item.ref_id,
                item.ticket.generation,
                item.context,
                state="READY",
                fields={"album": "Late"},
                evidence=FieldEvidence("EMBEDDED", "test", "2026-09-18"),
            )
    finally:
        event.remove(Session, "after_flush", hold_revision)
    assert delayed
    assert item.service.get(item.principal, item.ref_id)["fields"] == {}
    with pytest.raises(ResourceAdmissionError, match="metadata_execution_stale"):
        item.repository.renew(item.ticket, IDENTITY)
    item.repository.confirm(
        item.ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY)
    )


@pytest.mark.usefixtures("metadata_budget")
def test_provider_gate_retains_expired_request_until_exact_exit(
    metadata_work: MetadataFixture,
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    request = uuid4()
    assert item.repository.provider_begin(running, request)
    assert item.repository.provider_begin(running, request)
    assert not item.repository.provider_begin(running, uuid4())
    with item.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE library.metadata_execution "
                "SET io_deadline_at=clock_timestamp()+interval '50 milliseconds'"
            )
        )
    sleep(0.08)
    with pytest.raises(ResourceAdmissionError):
        item.repository.provider_end(running, request)
    with item.sessions() as session:
        assert (
            present(session.get(MetadataProviderGateRow, 1)).execution_id
            == item.ticket.execution_id
        )
        assert internal_io_usage(session) == 1
    item.repository.confirm(
        item.ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY)
    )
    with item.sessions() as session:
        gate = present(session.get(MetadataProviderGateRow, 1))
        assert gate.execution_id is None
        now = session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        assert gate.next_request_at > now
        assert internal_io_usage(session) == 0


def fixture_launch(*, blocked: Path | None = None) -> tuple[list[str], dict[str, str]]:
    args, env = metadata_child_launch()
    if blocked is not None:
        env["AUTPLAY_TEST_METADATA_BLOCK"] = str(blocked)
    return [
        args[0],
        "-I",
        str(Path(__file__).parents[1] / "fixtures" / "metadata_provider_child.py"),
    ], env


@pytest.mark.usefixtures("metadata_budget")
def test_actual_child_paces_each_request_and_retains_lost_exit_ack(
    metadata_work: MetadataFixture, tmp_path: Path
) -> None:
    item, allow = metadata_work, Event()

    class Uncertain(PostgresMetadataExecutionRepository):
        def confirm(
            self, ticket: MetadataExecutionTicket, proof: ProcessExitEvidence
        ) -> MetadataStatus:
            if not allow.is_set():
                raise SQLAlchemyError("synthetic lost acknowledgement")
            return super().confirm(ticket, proof)

    coordinator = MetadataProcessCoordinator(
        Uncertain(item.sessions),
        IngestChildSettings(tmp_path),
        tree_factory=process_tree_factory(),
        launch=fixture_launch,
    )
    completed = Event()

    def action(work: MetadataWork) -> None:
        work.provider.http.get("https://musicbrainz.org/ws/2/recording?fmt=json")
        start = monotonic()
        work.provider.http.get("https://musicbrainz.org/ws/2/recording?fmt=json")
        assert monotonic() - start >= 1.05
        completed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.run, item.ticket, action)
        try:
            assert completed.wait(8)
            assert coordinator.pending() == (item.ticket.execution_id,)
            with item.sessions() as session:
                assert internal_io_usage(session) == 1
        finally:
            allow.set()
        future.result(timeout=4)
    assert not coordinator.pending()


@pytest.mark.usefixtures("metadata_budget")
def test_stop_during_provider_request_confirms_tree_before_releasing_gate(
    metadata_work: MetadataFixture, tmp_path: Path
) -> None:
    item, marker = metadata_work, tmp_path / "request-active"
    coordinator = MetadataProcessCoordinator(
        item.repository,
        IngestChildSettings(tmp_path),
        tree_factory=process_tree_factory(),
        launch=lambda: fixture_launch(blocked=marker),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            coordinator.run,
            item.ticket,
            lambda work: work.provider.http.get("https://musicbrainz.org/ws/2/recording?fmt=json"),
        )
        try:
            wait_marker(marker)
            with item.sessions() as session:
                assert (
                    present(session.get(MetadataProviderGateRow, 1)).execution_id
                    == item.ticket.execution_id
                )
        finally:
            assert coordinator.shutdown(timeout=5) == ()
        with pytest.raises((ResourceAdmissionError, ChildProtocolError)):
            future.result(timeout=1)
    with item.sessions() as session:
        assert present(session.get(MetadataProviderGateRow, 1)).execution_id is None
        assert (
            present(session.get(MetadataExecutionRow, item.ticket.execution_id)).state == "CLOSED"
        )


@pytest.mark.usefixtures("metadata_budget")
def test_downgrade_refuses_metadata_history(
    metadata_work: MetadataFixture, database_harness: DatabaseHarness, database_name: str
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    item.repository.confirm(item.ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(DBAPIError, match="Refusing to discard metadata execution"):
        database_harness.downgrade(database_name, "0049_internal_io_budget")


@pytest.mark.usefixtures("metadata_budget")
@pytest.mark.skipif(
    os.name == "nt", reason="pinned ffprobe/ffmpeg/fpcalc are in the Linux proof image"
)
def test_actual_worker_once_reads_canonical_audio_inside_tree(
    ingest: IngestFixture, tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    process_tree_factory()
    source = io.BytesIO()
    with wave.open(source, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(16000 * math.sin(i * 440 * 2 * math.pi / 22050)))
                for i in range(22050 * 12)
            )
        )
    payload = source.getvalue()
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    with ingest.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, ingest.upload_id))
        upload.expected_size = upload.received_size = upload.chunk_size = len(payload)
        upload.declared_sha256 = digest.value
        key, user = upload.staging_key, upload.user_id
    (tmp_path / "staging" / key).write_bytes(payload)
    storage = FilesystemVaultStorage(tmp_path, limits=VaultLimits())
    with ingest.sessions.begin() as session:
        ref = UserTrackRefRow(
            user_id=user,
            raw_title="Proof",
            raw_artist="Artist",
            resolution_status="UNRESOLVED",
        )
        session.add(ref)
        session.flush()
        session.add(
            LibraryEntryRow(
                user_id=user,
                user_track_ref_id=ref.user_track_ref_id,
                source="IMPORT",
                availability_status="VAULT",
            )
        )
        ref_id = ref.user_track_ref_id
        session.flush()
        recording = MusicLibraryService.prepare_in_transaction(session, user, ref_id)
        present(session.get(UploadSessionRow, ingest.upload_id)).target_recording_id = recording
    finalized(replace(ingest, storage=storage, verified=VerifiedStagedFile(len(payload), digest)))
    with ingest.sessions.begin() as session:
        upload = present(session.get(UploadSessionRow, ingest.upload_id))
        MusicLibraryService._prepare_publication(
            session,
            user,
            ref_id,
            ingest.upload_id,
            upload.device_id,
        )
        MusicLibraryService._project_publication(session, user, ref_id, ingest.upload_id)
    settings = WorkerSettings(
        database_url=SecretStr(database_url),
        acoustid_client_key=SecretStr("fixture-key"),
        vault_root=tmp_path,
        worker_cgroup_root=Path(os.environ["AUTPLAY_TEST_CGROUP_ROOT"]),
    )
    monkeypatch.setattr(metadata_worker, "load_worker_settings", lambda: settings)
    monkeypatch.setattr(
        "autplay.runtime.metadata_io.metadata_child_launch", lambda **kw: fixture_launch()
    )
    assert metadata_worker.main(["--once"]) == 0
    with ingest.sessions() as session:
        row = present(session.get(TrackMetadataRow, ref_id))
        execution = present(session.scalar(select(MetadataExecutionRow)))
        assert row.state == "NOT_FOUND"
        assert row.document is not None
        assert execution.audio is not None
        assert row.document["audio_variant_id"] == execution.audio["audio_variant_id"]
        assert execution.state == "CLOSED" and execution.exit_code == 0
        assert internal_io_usage(session) == 0
    assert storage.verify_object(OpaqueStorageKey(digest.hex)).sha256 == digest
