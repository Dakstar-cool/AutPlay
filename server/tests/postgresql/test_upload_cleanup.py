"""Terminal upload claims exclude every retained writer and require exact cleanup exit."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import ProviderMaintenanceRow, UploadSessionRow
from autplay.adapters.postgresql.models.upload_cleanup import UploadCleanupClaimRow
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.upload_cleanup import (
    PostgresUploadCleanupRepository,
    queue_upload_cleanup,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.upload_cleanup import UploadCleanupClaim, UploadCleanupService
from autplay.application.vault_uploads import UploadStateError, VaultPrincipal
from autplay.domain.jobs import JobKey
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from starlette.testclient import TestClient

from .conftest import DatabaseHarness, prepare_adjacent_downgrade
from .test_discovery_authority_clock import _blocked_by
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_execution import upload_ticket
from .test_resource_upload_http import application
from .test_upload_staging_creation import AUTH, forbid_parent_storage

__all__ = ["admission"]


def cancel(harness: AdmissionHarness, upload_id: UUID) -> None:
    with harness.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload_id))
        PostgresVaultRuntime(session).cancel(
            VaultPrincipal(row.user_id, present(row.device_id)), upload_id
        )


def service(
    harness: AdmissionHarness, root: Path
) -> tuple[UploadCleanupService, ProcessProviderMaintenanceStorage]:
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(harness.sessions), root
    )
    return UploadCleanupService(PostgresUploadCleanupRepository(harness.sessions), storage), storage


@pytest.mark.parametrize("operation", ["cancel", "head", "status", "complete"])
def test_http_commits_intent_without_parent_bytes_then_actual_child_retires(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    actor = admission.actor()
    upload_id = admission.upload(actor)
    FilesystemVaultStorage(tmp_path).create_staging(OpaqueStorageKey(upload_id.hex))
    source = tmp_path / "staging" / upload_id.hex
    source.write_bytes(b"partial")
    if operation != "cancel":
        with admission.sessions.begin() as session:
            upload = present(session.get(UploadSessionRow, upload_id))
            upload.created_at = datetime.now(UTC) - timedelta(hours=2)
            upload.expires_at = datetime.now(UTC) - timedelta(hours=1)
    forbid_parent_storage(monkeypatch)
    app, _, control = application(admission, actor, tmp_path)
    try:
        with TestClient(app) as client:
            url = f"/api/v1/vault/uploads/{upload_id}"
            match operation:
                case "cancel":
                    response = client.delete(url, headers=AUTH)
                case "head":
                    response = client.head(url, headers=AUTH)
                case "status":
                    response = client.get(url, headers=AUTH)
                case _:
                    response = client.post(url + "/complete", headers=AUTH)
            assert (
                response.status_code
                == {"cancel": 204, "head": 204, "status": 200, "complete": 409}[operation]
            )
            assert source.read_bytes() == b"partial"
        with admission.sessions() as session:
            row = present(session.get(UploadCleanupClaimRow, upload_id))
            assert row.terminal_state == ("CANCELLED" if operation == "cancel" else "EXPIRED")
            assert row.completed_at is None
        cleanup, storage = service(admission, tmp_path)
        original_go = RetainedVaultProcess.go

        def checked_go(
            child: RetainedVaultProcess[MaintenanceTicket],
            document: dict[str, object],
            payload: bytes | None = None,
        ) -> None:
            with admission.sessions() as session:
                claim = present(session.get(UploadCleanupClaimRow, upload_id))
                run = present(session.get(ProviderMaintenanceRow, child.ticket.execution_id))
                assert run.state == "RUNNING" and run.upload_claim_id == claim.claim_id
            assert admission.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
            original_go(child, document, payload)

        monkeypatch.setattr(RetainedVaultProcess, "go", checked_go)
        try:
            assert cleanup.cleanup(upload_id)
            assert cleanup.cleanup(upload_id)
            assert not source.exists()
            with admission.sessions() as session:
                claim = present(session.get(UploadCleanupClaimRow, upload_id))
                run = present(session.get(ProviderMaintenanceRow, claim.completed_execution_id))
                assert run.state == "CLOSED" and run.closure_kind == "PROCESS_EXIT"
                assert run.exit_code == 0 and run.child_pid is not None
                assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 1
            target = (
                tmp_path
                / "quarantine"
                / UploadCleanupClaim(
                    upload_id, OpaqueStorageKey(upload_id.hex)
                ).quarantine_key.value
            )
            assert target.read_bytes() == b"partial"
        finally:
            assert not storage.shutdown()
    finally:
        control.dispose()


@pytest.mark.parametrize("operation", ["cancel", "expire"])
def test_terminal_transition_and_claim_rollback_together(
    admission: AdmissionHarness, operation: str
) -> None:
    actor = admission.actor()
    upload_id = admission.upload(actor)
    with admission.sessions() as session:
        repo = PostgresVaultRuntime(session)
        principal = VaultPrincipal(actor.user_id, actor.device_id)
        if operation == "cancel":
            repo.cancel(principal, upload_id)
        else:
            repo.expire_open(principal, upload_id)
        assert session.get(UploadCleanupClaimRow, upload_id) is not None
        session.rollback()
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload_id)).state == "OPEN"
        assert session.get(UploadCleanupClaimRow, upload_id) is None


@pytest.mark.parametrize("committed", [False, True])
def test_lost_completion_reply_replays_exact_claim_without_replacing_quarantine(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    actor = admission.actor()
    upload_id = admission.upload(actor)
    FilesystemVaultStorage(tmp_path).create_staging(OpaqueStorageKey(upload_id.hex))
    (tmp_path / "staging" / upload_id.hex).write_bytes(b"partial")
    cancel(admission, upload_id)
    cleanup, storage = service(admission, tmp_path)
    original_complete = PostgresUploadCleanupRepository.complete

    def lost(
        repo: PostgresUploadCleanupRepository, claim: UploadCleanupClaim, execution_id: UUID
    ) -> None:
        if committed:
            original_complete(repo, claim, execution_id)
        raise SQLAlchemyError("synthetic completion reply loss")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(PostgresUploadCleanupRepository, "complete", lost)
            with pytest.raises(SQLAlchemyError):
                cleanup.cleanup(upload_id)
        assert cleanup.cleanup(upload_id)
        with admission.sessions() as session:
            assert present(session.get(UploadCleanupClaimRow, upload_id)).completed_at is not None
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == (
                1 if committed else 2
            )
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("closure", ["PREPARED", "NOT_STARTED", "FAILED"])
def test_completion_requires_successful_exact_process_exit_in_repository_and_sql(
    admission: AdmissionHarness, closure: str
) -> None:
    upload_id = admission.upload(admission.actor())
    cancel(admission, upload_id)
    claims = PostgresUploadCleanupRepository(admission.sessions)
    claim = present(claims.claim(upload_id))
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.UPLOAD_CLEANUP, claim.storage_key
    )
    maintenance = PostgresProviderMaintenanceRepository(admission.sessions)
    maintenance.prepare(ticket)
    if closure == "NOT_STARTED":
        maintenance.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    elif closure == "FAILED":
        identity = ProcessIdentity(12345, b"i" * 32)
        maintenance.start(ticket, identity)
        maintenance.confirm(
            ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 1, identity)
        )
    with pytest.raises(ResourceAdmissionError, match="upload_cleanup_execution_unconfirmed"):
        claims.complete(claim, ticket.execution_id)
    with (
        pytest.raises(IntegrityError, match="Upload cleanup exit is unconfirmed"),
        admission.sessions.begin() as session,
    ):
        row = present(session.get(UploadCleanupClaimRow, upload_id))
        row.completed_at = datetime.now(UTC)
        row.completed_execution_id = ticket.execution_id
        session.flush()


def test_prepared_writer_racing_terminal_intent_blocks_cleanup_until_acknowledged(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    admission.budget()
    actor = admission.actor()
    ticket = upload_ticket(admission, actor)
    admission.service.prepare_execution(actor, ticket)
    # Models prepare committing after cancel checked writer absence but before its
    # terminal commit. The prepared child still must lock/recheck before GO.
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, ticket.actual_target_id, with_for_update=True))
        row.state = "CANCELLED"
        row.completed_at = datetime.now(UTC)
        row.error_code = "upload_cancelled"
        queue_upload_cleanup(session, row)
    claims = PostgresUploadCleanupRepository(admission.sessions)
    assert claims.claim(ticket.actual_target_id) is None
    raw = UploadCleanupClaim(ticket.actual_target_id, OpaqueStorageKey(ticket.actual_target_id.hex))
    maintenance = PostgresProviderMaintenanceRepository(admission.sessions)
    with pytest.raises(ResourceAdmissionError, match="upload_cleanup_conflict"):
        maintenance.prepare(
            MaintenanceTicket(
                uuid4(),
                uuid4(),
                None,
                raw.claim_id,
                MaintenanceAction.UPLOAD_CLEANUP,
                raw.storage_key,
            )
        )
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
    )
    admission.service.close_io(ticket.permit)
    FilesystemVaultStorage(tmp_path)
    cleanup, storage = service(admission, tmp_path)
    try:
        assert cleanup.cleanup(ticket.actual_target_id)
    finally:
        assert not storage.shutdown()


@pytest.mark.parametrize("ingest_wins", [False, True])
def test_sealed_cancel_and_ingest_have_one_writer_owner(
    admission: AdmissionHarness, ingest_wins: bool
) -> None:
    actor = admission.actor()
    upload_id = admission.upload(actor)
    principal = VaultPrincipal(actor.user_id, actor.device_id)
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload_id))
        row.received_size = row.expected_size
        sealed = PostgresVaultRuntime(session).seal_and_enqueue(principal, upload_id)
        job_id = present(sealed.job_id)
    with admission.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="cleanup-race",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]

    def begin_ingest() -> object | None:
        with admission.sessions.begin() as session:
            cached = present(session.get(UploadSessionRow, upload_id))
            assert cached.state == "SEALED"
            return PostgresVaultRuntime(session).start_ingest(upload_id, job_id, fence=lease.fence)

    def contender() -> object | None:
        if ingest_wins:
            cancel(admission, upload_id)
            return None
        return begin_ingest()

    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions() as blocker:
        repo = PostgresVaultRuntime(blocker)
        if ingest_wins:
            assert repo.start_ingest(upload_id, job_id, fence=lease.fence) is not None
        else:
            repo.cancel(principal, upload_id)
        future = pool.submit(contender)
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        try:
            with admission.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert not future.done() and monotonic() < until
                    sleep(0.005)
            blocker.commit()
        finally:
            blocker.rollback()
        if ingest_wins:
            with pytest.raises(UploadStateError):
                future.result(timeout=5)
        else:
            assert future.result(timeout=5) is None
    claims = PostgresUploadCleanupRepository(admission.sessions)
    assert (claims.claim(upload_id) is None) == ingest_wins
    if ingest_wins:
        with (
            pytest.raises(IntegrityError, match="cannot rewind"),
            admission.sessions.begin() as session,
        ):
            present(session.get(UploadSessionRow, upload_id)).state = "SEALED"


def test_claimed_upload_cannot_be_reopened_rekeyed_or_deleted(admission: AdmissionHarness) -> None:
    upload_id = admission.upload(admission.actor())
    cancel(admission, upload_id)
    for change in ("state", "key", "delete"):
        with pytest.raises(IntegrityError), admission.sessions.begin() as session:
            row = present(session.get(UploadSessionRow, upload_id))
            if change == "state":
                row.state = "OPEN"
            elif change == "key":
                row.staging_key = "replacement"
            else:
                session.delete(row)
            session.flush()


def test_missing_namespace_and_poison_claim_do_not_starve_later_claims(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    ids = sorted(admission.upload(admission.actor()) for _ in range(2))
    cancel(admission, ids[0])
    # Historical terminal metadata may predate the durable claim migration.
    with admission.sessions.begin() as session:
        historical = present(session.get(UploadSessionRow, ids[1]))
        historical.state = "EXPIRED"
        historical.completed_at = datetime.now(UTC)
        historical.error_code = "upload_session_expired"
        assert session.get(UploadCleanupClaimRow, ids[1]) is None
    cleanup, _ = service(admission, tmp_path / "missing")
    report = cleanup.run(limit=1)
    assert report.completed == 0 and report.deferred == 2 and report.pending
    FilesystemVaultStorage(tmp_path)
    first = present(PostgresUploadCleanupRepository(admission.sessions).claim(ids[0]))
    (tmp_path / "staging" / first.storage_key.value).write_bytes(b"source")
    (tmp_path / "quarantine" / first.quarantine_key.value).write_bytes(b"collision")
    cleanup, _ = service(admission, tmp_path)
    report = cleanup.run(limit=1)
    assert report.completed == 1 and report.deferred == 1 and report.pending


def test_second_unconfirmed_maintenance_prevents_claim_completion(
    admission: AdmissionHarness,
) -> None:
    upload_id = admission.upload(admission.actor())
    cancel(admission, upload_id)
    claims = PostgresUploadCleanupRepository(admission.sessions)
    claim = present(claims.claim(upload_id))
    maintenance = PostgresProviderMaintenanceRepository(admission.sessions)
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.UPLOAD_CLEANUP, claim.storage_key
    )
    identity = ProcessIdentity(12345, b"i" * 32)
    maintenance.prepare(ticket)
    maintenance.start(ticket, identity)
    maintenance.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 0, identity))
    later = replace(ticket, execution_id=uuid4(), owner_run_id=uuid4())
    maintenance.prepare(later)
    with pytest.raises(ResourceAdmissionError, match="upload_cleanup_execution_unconfirmed"):
        claims.complete(claim, ticket.execution_id)
    maintenance.confirm(later, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    claims.complete(claim, ticket.execution_id)


def test_lost_exit_acknowledgement_retains_singleton_and_pending_claim(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload_id = admission.upload(admission.actor())
    cancel(admission, upload_id)
    FilesystemVaultStorage(tmp_path)
    entered, release = Event(), Event()
    maintenance = PostgresProviderMaintenanceRepository(admission.sessions)
    original_confirm = maintenance.confirm

    def delayed(ticket: MaintenanceTicket, proof: ProcessExitEvidence) -> MaintenanceStatus:
        entered.set()
        if not release.wait(timeout=5):
            raise SQLAlchemyError("synthetic acknowledgement unavailable")
        return original_confirm(ticket, proof)

    monkeypatch.setattr(maintenance, "confirm", delayed)
    storage = ProcessProviderMaintenanceStorage(maintenance, tmp_path)
    cleanup = UploadCleanupService(PostgresUploadCleanupRepository(admission.sessions), storage)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cleanup.cleanup, upload_id)
            try:
                assert entered.wait(timeout=5)
                assert not future.done() and storage.pending()
                with admission.sessions() as session:
                    assert (
                        present(session.get(UploadCleanupClaimRow, upload_id)).completed_at is None
                    )
                    run = present(session.scalar(select(ProviderMaintenanceRow)))
                    assert run.closed_at is None
                    other = MaintenanceTicket(
                        uuid4(),
                        uuid4(),
                        None,
                        upload_id,
                        MaintenanceAction.UPLOAD_CLEANUP,
                        OpaqueStorageKey(upload_id.hex),
                    )
                with pytest.raises(ResourceAdmissionError, match="maintenance_busy"):
                    maintenance.prepare(other)
            finally:
                release.set()
            assert future.result(timeout=5)
    finally:
        release.set()
        assert not storage.shutdown()


def test_actual_cli_process_drains_claims_and_redacts_paths(
    admission: AdmissionHarness, database_url: str, tmp_path: Path
) -> None:
    upload_id = admission.upload(admission.actor())
    cancel(admission, upload_id)
    FilesystemVaultStorage(tmp_path).create_staging(OpaqueStorageKey(upload_id.hex))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        AUTPLAY_DATABASE_URL=database_url, AUTPLAY_PROFILE="test", AUTPLAY_VAULT_ROOT=str(tmp_path)
    )
    result = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.admin", "vault-upload-cleanup", "--limit", "1"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"completed": 1, "deferred": 0, "pending": False}
    assert str(tmp_path) not in result.stdout and upload_id.hex not in result.stdout
    assert not result.stderr


def test_downgrade_waits_for_claim_commit_and_refuses_to_discard_it(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    upload_id = admission.upload(admission.actor())
    prepare_adjacent_downgrade(database_harness, database_name, "0046_upload_cleanup")
    with ThreadPoolExecutor(max_workers=1) as pool, admission.sessions() as blocker:
        staging_key = blocker.scalar(
            text("SELECT staging_key FROM vault.upload_session WHERE upload_session_id=:upload"),
            {"upload": upload_id},
        )
        assert isinstance(staging_key, str)
        blocker.execute(
            text(
                """UPDATE vault.upload_session SET state='CANCELLED',
                error_code='upload_cancelled',completed_at=clock_timestamp(),
                updated_at=clock_timestamp(),row_version=row_version+1
                WHERE upload_session_id=:upload"""
            ),
            {"upload": upload_id},
        )
        blocker.execute(
            text(
                """INSERT INTO vault.upload_cleanup_claim(
                claim_id,storage_key,terminal_state,created_at)
                VALUES (:upload,:storage,'CANCELLED',clock_timestamp())"""
            ),
            {"upload": upload_id, "storage": staging_key},
        )
        pid = present(blocker.scalar(select(func.pg_backend_pid())))
        future = pool.submit(database_harness.downgrade, database_name, "0045_orphan_missing")
        try:
            with admission.sessions() as observer:
                until = monotonic() + 5
                while not _blocked_by(observer, pid):
                    assert not future.done() and monotonic() < until
                    sleep(0.005)
            blocker.commit()
        finally:
            blocker.rollback()
        with pytest.raises(DBAPIError, match="Refusing to discard"):
            future.result(timeout=10)
    with database_harness.connect(database_name) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0046_upload_cleanup",
        )


def processing_upload(harness: AdmissionHarness) -> UUID:
    actor = harness.actor()
    upload_id = harness.upload(actor)
    with harness.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload_id))
        row.received_size = row.expected_size
        sealed = PostgresVaultRuntime(session).seal_and_enqueue(
            VaultPrincipal(actor.user_id, actor.device_id), upload_id
        )
        job_id = present(sealed.job_id)
    with harness.sessions.begin() as session:
        assert PostgresVaultRuntime(session).start_ingest(upload_id, job_id) is not None
    return upload_id


def test_actor_change_cannot_bypass_writer_rewind_guard(admission: AdmissionHarness) -> None:
    upload_id = processing_upload(admission)
    with (
        pytest.raises(IntegrityError, match="Upload actor identity is immutable"),
        admission.sessions.begin() as session,
    ):
        session.execute(
            text(
                "UPDATE vault.upload_session SET actor_kind='PROVIDER',device_id=NULL,"
                "source_candidate_id=:candidate,source_acquisition_attempt_id=:attempt "
                "WHERE upload_session_id=:upload"
            ),
            {"candidate": uuid4(), "attempt": uuid4(), "upload": upload_id},
        )
    assert PostgresUploadCleanupRepository(admission.sessions).claim(upload_id) is None


def test_processing_upload_is_excluded_from_cleanup_queue(
    admission: AdmissionHarness,
) -> None:
    upload_id = processing_upload(admission)
    claims = PostgresUploadCleanupRepository(admission.sessions)
    assert claims.claim(upload_id) is None and not claims.pending(maximum=1)


pytestmark = pytest.mark.usefixtures("internal_io_budget")
