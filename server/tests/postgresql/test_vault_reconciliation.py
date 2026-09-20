"""Live scan progress and restart-safe drain against real PostgreSQL and children."""

import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.postgresql.models import OrphanObjectClaimRow, ProviderMaintenanceRow
from autplay.adapters.postgresql.orphan_object_retirement import PostgresOrphanObjectRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.vault_inventory import PostgresVaultInventoryRepository
from autplay.application.orphan_object_retirement import OrphanObjectClaim
from autplay.application.vault_inventory import InventoryObservation, InventoryPage
from autplay.application.vault_reconciliation import ReconcileMode, VaultReconciliationService
from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import OpaqueStorageKey
from autplay.entrypoints.admin import run_vault_reconcile
from autplay.entrypoints.vault_reconciliation import build_vault_reconciliation_service
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from autplay.runtime.vault_inventory import ProcessVaultInventoryCursor
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_vault_runtime import _publish

__all__ = ["admission"]


@dataclass
class Composition:
    service: VaultReconciliationService
    claims: PostgresOrphanObjectRepository
    inventory: PostgresVaultInventoryRepository
    cursor: ProcessVaultInventoryCursor
    storage: ProcessProviderMaintenanceStorage


def compose(harness: AdmissionHarness, root: Path) -> Composition:
    maintenance = PostgresProviderMaintenanceRepository(harness.sessions)
    claims = PostgresOrphanObjectRepository(harness.sessions)
    inventory = PostgresVaultInventoryRepository(harness.sessions)
    cursor = ProcessVaultInventoryCursor(maintenance, root)
    storage = ProcessProviderMaintenanceStorage(maintenance, root)
    return Composition(
        VaultReconciliationService(
            cursor=cursor, inventory=inventory, claims=claims, storage=storage
        ),
        claims,
        inventory,
        cursor,
        storage,
    )


def populate(root: Path, count: int = 3) -> tuple[OpaqueStorageKey, ...]:
    storage = FilesystemVaultStorage(root)
    return tuple(_publish(storage, f"stage-{i}", f"orphan-{i}".encode()) for i in range(count))


def test_one_live_scanner_advances_tiny_pages_and_exits_before_any_retirement(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = populate(tmp_path)
    for number in range(20):
        (tmp_path / "objects" / f"{number:02x}").mkdir(exist_ok=True)
    composed = compose(admission, tmp_path)
    original = RetainedVaultProcess.go
    actions: list[MaintenanceAction] = []

    def go(child: RetainedVaultProcess[MaintenanceTicket], command: dict[str, object]) -> None:
        assert isinstance(admission.engine.pool, QueuePool)
        assert admission.engine.pool.checkedout() == 0
        actions.append(child.ticket.action)
        if child.ticket.action != MaintenanceAction.INVENTORY:
            with admission.sessions() as session:
                scanner = present(
                    session.scalar(
                        select(ProviderMaintenanceRow).where(
                            ProviderMaintenanceRow.action == "INVENTORY"
                        )
                    )
                )
                assert scanner.state == "CLOSED" and scanner.exit_code == 0
                assert scanner.closure_kind == "PROCESS_EXIT"
        original(child, command)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("filesystem operation in parent")

    monkeypatch.setattr(RetainedVaultProcess, "go", go)
    for method in ("inventory", "quarantine_object", "verify_object"):
        monkeypatch.setattr(FilesystemVaultStorage, method, forbidden)
    reports = list(composed.service.steps(mode=ReconcileMode.APPLY, limit=1))
    assert len(reports) > 50
    assert all(b.work_units - a.work_units <= 1 for a, b in pairwise(reports))
    assert all(report.quarantined == 0 for report in reports if not report.scan_complete)
    final = reports[-1]
    assert final.pass_complete and final.scan_complete and not final.pending_claims
    assert final.claimed == final.quarantined == len(keys)
    assert final.missing == final.deferred == 0
    assert actions == [MaintenanceAction.INVENTORY, *[MaintenanceAction.ORPHAN_OBJECT] * 3]
    assert not composed.cursor.pending() and not composed.storage.pending()


def test_dry_run_only_observes_and_reports_limited_coverage(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    keys = populate(tmp_path)
    composed = compose(admission, tmp_path)
    composed.claims.claim(keys[0], uuid4())
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_vault_reconcile(
        composed.service, ["vault-reconcile", "--limit", "1"], stdout=stdout, stderr=stderr
    )
    report = json.loads(stdout.getvalue())
    assert code == 5 and not stderr.getvalue()
    assert report["scope"] == "unregistered_cas" and report["tracked_reconciliation_pending"]
    assert report["scan_complete"] and report["pass_complete"] and report["pending_claims"]
    assert report["orphan_candidates"] == 3
    assert report["claimed"] == report["quarantined"] == report["missing"] == 0
    assert all(key.value not in stdout.getvalue() for key in keys)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OrphanObjectClaimRow)) == 1
        assert list(session.scalars(select(ProviderMaintenanceRow.action))) == ["INVENTORY"]
    assert len(FilesystemVaultStorage(tmp_path).inventory().object_keys) == 3


def test_drain_only_resumes_claims_without_opening_scanner(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = populate(tmp_path, 1)[0]
    composed = compose(admission, tmp_path)
    composed.claims.claim(key, uuid4())

    def forbidden(*, maximum: int) -> InventoryPage:
        raise AssertionError("drain-only opened scanner")

    monkeypatch.setattr(composed.cursor, "next_page", forbidden)
    report = composed.service.run(mode=ReconcileMode.APPLY, limit=1, drain_only=True)
    assert report.quarantined == 1 and report.pass_complete
    assert not report.scan_complete and not report.pending_claims and report.work_units == 0
    with admission.sessions() as session:
        assert list(session.scalars(select(ProviderMaintenanceRow.action))) == ["ORPHAN_OBJECT"]


@pytest.mark.parametrize("boundary", ["claim", "completion"])
@pytest.mark.parametrize("committed", [False, True])
def test_restart_replays_durable_claims_after_lost_database_reply(
    admission: AdmissionHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    committed: bool,
) -> None:
    key = populate(tmp_path, 1)[0]
    composed = compose(admission, tmp_path)
    if boundary == "completion":
        canonical = uuid4()
        composed.claims.claim(key, canonical)
    claim, complete = composed.claims.claim, composed.claims.complete

    def lost_claim(key: OpaqueStorageKey, identifier: UUID) -> OrphanObjectClaim | None:
        if committed:
            claim(key, identifier)
        raise SQLAlchemyError("synthetic lost claim reply")

    def lost_complete(receipt: OrphanObjectClaim, execution_id: UUID) -> None:
        assert receipt.claim_id == canonical
        if committed:
            complete(receipt, execution_id)
        raise SQLAlchemyError("synthetic lost completion reply")

    with monkeypatch.context() as patch:
        patch.setattr(
            composed.claims,
            "claim" if boundary == "claim" else "complete",
            lost_claim if boundary == "claim" else lost_complete,
        )
        with pytest.raises(SQLAlchemyError):
            composed.service.run(mode=ReconcileMode.APPLY, limit=1)
    assert not composed.cursor.pending() and not composed.storage.pending()
    recovered = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, limit=1
    )
    assert recovered.pass_complete and not recovered.pending_claims
    assert recovered.quarantined == (0 if boundary == "completion" and committed else 1)
    with admission.sessions() as session:
        rows = list(session.scalars(select(OrphanObjectClaimRow)))
        assert len(rows) == 1 and rows[0].completed_at is not None
        if boundary == "completion":
            assert rows[0].claim_id == canonical


def test_poison_first_claim_does_not_starve_next_and_cli_reports_pending(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    keys = populate(tmp_path, 2)
    composed = compose(admission, tmp_path)
    poison = present(composed.claims.claim(keys[0], UUID(int=1)))
    composed.claims.claim(keys[1], UUID(int=2))
    (tmp_path / "quarantine" / poison.quarantine_key.value).write_bytes(b"collision")
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_vault_reconcile(
            composed.service,
            ["vault-reconcile", "--apply", "--drain-only", "--limit", "1"],
            stdout=stdout,
            stderr=stderr,
        )
        == 5
    )
    report = json.loads(stdout.getvalue())
    assert report["deferred"] == report["quarantined"] == 1 and report["missing"] == 0
    assert report["pending_claims"] and report["pass_complete"]
    assert composed.claims.pending() == (poison,)
    assert (tmp_path / "quarantine" / poison.quarantine_key.value).read_bytes() == b"collision"


def test_claim_inserted_behind_drain_cursor_is_reported_and_replayed_next_pass(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = populate(tmp_path, 2)
    composed = compose(admission, tmp_path)
    composed.claims.claim(keys[0], UUID(int=20))
    complete = composed.claims.complete

    def insert_behind(claim: OrphanObjectClaim, execution_id: UUID) -> None:
        complete(claim, execution_id)
        composed.claims.claim(keys[1], UUID(int=10))

    monkeypatch.setattr(composed.claims, "complete", insert_behind)
    report = composed.service.run(mode=ReconcileMode.APPLY, limit=1, drain_only=True)
    assert report.quarantined == 1 and report.pending_claims
    recovered = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, limit=1, drain_only=True
    )
    assert recovered.quarantined == 1 and not recovered.pending_claims


@pytest.mark.parametrize("fault", ["classification", "close", "abandon"])
def test_incomplete_scan_never_drains_and_restart_can_resume_saved_claims(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    populate(tmp_path)
    composed = compose(admission, tmp_path)
    observe = composed.inventory.observe

    def fail_after_claim(page: InventoryPage) -> tuple[InventoryObservation, ...]:
        if composed.claims.pending():
            raise SQLAlchemyError("synthetic classification unavailable")
        return observe(page)

    if fault == "classification":
        monkeypatch.setattr(composed.inventory, "observe", fail_after_claim)
        with pytest.raises(SQLAlchemyError):
            composed.service.run(mode=ReconcileMode.APPLY, limit=1)
    else:
        steps = composed.service.steps(mode=ReconcileMode.APPLY, limit=1)
        while not next(steps).claimed:
            pass
        if fault == "close":
            shutdown = composed.cursor.shutdown
            calls = 0

            def pending_once(*, timeout: float = 5) -> tuple[UUID, ...]:
                nonlocal calls
                calls += 1
                return composed.cursor.pending() if calls == 1 else shutdown(timeout=timeout)

            monkeypatch.setattr(composed.cursor, "shutdown", pending_once)
            with pytest.raises(ResourceAdmissionError, match="maintenance_exit_unconfirmed"):
                steps.close()
        else:
            steps.close()
    assert not composed.cursor.pending() and not composed.storage.pending()
    assert composed.claims.pending()
    with admission.sessions() as session:
        assert list(session.scalars(select(ProviderMaintenanceRow.action))) == ["INVENTORY"]
    recovered = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY, drain_only=True
    )
    assert recovered.quarantined >= 1 and not recovered.pending_claims


def test_missing_pending_claim_is_resolved_by_separate_check_only_child(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    key = populate(tmp_path, 1)[0]
    composed = compose(admission, tmp_path)
    claim = present(composed.claims.claim(key, uuid4()))
    (tmp_path / "objects" / key.value[:2] / key.value[2:4] / key.value).unlink()
    report = composed.service.run(mode=ReconcileMode.APPLY, drain_only=True)
    assert report.missing == 1 and report.quarantined == report.deferred == 0
    assert present(composed.claims.claim(key, claim.claim_id)).outcome == "MISSING"
    with admission.sessions() as session:
        assert set(session.scalars(select(ProviderMaintenanceRow.action))) == {
            "ORPHAN_OBJECT",
            "ORPHAN_MISSING",
        }


@pytest.mark.parametrize(
    "code", ["maintenance_busy", "maintenance_deadline_expired", "maintenance_unavailable"]
)
def test_uncertain_retirement_never_launches_absence_probe(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    key = populate(tmp_path, 1)[0]
    composed = compose(admission, tmp_path)
    composed.claims.claim(key, uuid4())

    def failed(claim: OrphanObjectClaim) -> UUID:
        raise ResourceAdmissionError(code)

    def forbidden(claim: OrphanObjectClaim) -> UUID:
        raise AssertionError("uncertain failure started absence check")

    monkeypatch.setattr(composed.storage, "retire_orphan_object", failed)
    monkeypatch.setattr(composed.storage, "confirm_orphan_missing", forbidden)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_vault_reconcile(
            composed.service, ["vault-reconcile", "--apply"], stdout=stdout, stderr=stderr
        )
        == 5
    )
    assert not stdout.getvalue() and code in stderr.getvalue()
    assert len(composed.claims.pending()) == 1


def test_actual_cli_process_uses_retained_composition_and_redacted_output(
    admission: AdmissionHarness, database_url: str, tmp_path: Path
) -> None:
    key = populate(tmp_path, 1)[0]
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        AUTPLAY_DATABASE_URL=database_url,
        AUTPLAY_PROFILE="test",
        AUTPLAY_VAULT_ROOT=str(tmp_path),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autplay.entrypoints.admin",
            "vault-reconcile",
            "--apply",
            "--limit",
            "1",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["scope"] == "unregistered_cas" and report["quarantined"] == 1
    assert report["pass_complete"] and report["scan_complete"]
    assert not report["pending_claims"] and not result.stderr
    assert key.value not in result.stdout and str(tmp_path) not in result.stdout
    with admission.sessions() as session:
        runs = list(session.scalars(select(ProviderMaintenanceRow)))
        assert len(runs) == 2 and all(run.state == "CLOSED" and run.exit_code == 0 for run in runs)


def test_storage_failure_with_pending_exit_cannot_probe_missing_or_report_success(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = populate(tmp_path, 1)[0]
    composed = compose(admission, tmp_path)
    composed.claims.claim(key, uuid4())
    execution_id = uuid4()
    probes: list[UUID] = []

    def failed(claim: OrphanObjectClaim) -> UUID:
        raise ResourceAdmissionError("maintenance_storage_failed")

    def forbidden(claim: OrphanObjectClaim) -> UUID:
        probes.append(claim.claim_id)
        raise AssertionError("pending process started absence check")

    monkeypatch.setattr(composed.storage, "retire_orphan_object", failed)
    monkeypatch.setattr(composed.storage, "confirm_orphan_missing", forbidden)
    monkeypatch.setattr(composed.storage, "pending", lambda: (execution_id,))
    monkeypatch.setattr(composed.storage, "shutdown", lambda: (execution_id,))
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_vault_reconcile(
            composed.service, ["vault-reconcile", "--apply"], stdout=stdout, stderr=stderr
        )
        == 5
    )
    assert not stdout.getvalue() and "maintenance_exit_unconfirmed" in stderr.getvalue()
    assert not probes
    assert len(composed.claims.pending()) == 1


pytestmark = pytest.mark.usefixtures("internal_io_budget")
