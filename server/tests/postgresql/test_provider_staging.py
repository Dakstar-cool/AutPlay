"""Provider staging ownership and exit proof survive quota accounting cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql import resource_execution
from autplay.adapters.postgresql.models import ProviderStagingRow, UserAccountRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoExecutionRow
from autplay.application.vault_reconciliation import ReconcileMode
from autplay.domain.resource_admission import AcquisitionClaim, ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey
from autplay.entrypoints.vault_reconciliation import build_vault_reconciliation_service
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .conftest import DatabaseHarness
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_execution import shift_clock
from .test_resource_worker_admission import internet, successor

__all__ = ["admission"]


@dataclass(frozen=True)
class Writer:
    claim: AcquisitionClaim
    ticket: ExecutionTicket
    proof: ProcessExitEvidence
    key: OpaqueStorageKey


def writer(harness: AdmissionHarness, *, started: bool = True) -> Writer:
    harness.budget()
    _actor, claim = internet(harness)
    active = harness.service.acquire_worker(claim)
    permit = harness.service.open_io(claim, fence(active), claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    harness.service.prepare_execution(claim, ticket)
    if started:
        child = ProcessIdentity(34567, b"c" * 32)
        harness.service.start_execution(claim, ticket, child)
        proof = ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    else:
        proof = ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
    return Writer(claim, ticket, proof, OpaqueStorageKey(f"provider-{ticket.execution_id.hex}"))


@pytest.mark.parametrize("cleanup", ["close", "sweep", "rebind"])
@pytest.mark.usefixtures("internal_io_budget")
def test_stage_and_exact_exit_survive_accounting_cleanup(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    owned = writer(admission)
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(owned.key)

    def reconcile() -> None:
        report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
            mode=ReconcileMode.APPLY
        )
        assert report.quarantined == report.missing == report.claimed == 0
        assert storage.inventory().staging_keys == (owned.key,)

    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert row.state == "OWNED" and row.closed_at is None
        assert row.job_id == owned.claim.fence.job_id
        assert row.job_attempt == owned.claim.fence.attempt_no
        assert row.staging_key == owned.key.value
        assert row.acquisition_id == owned.claim.acquisition_id
    reconcile()
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    match cleanup:
        case "close":
            admission.service.close_io(owned.ticket.permit)
        case "sweep":
            shift_clock(monkeypatch, 10)
            admission.service.sweep()
        case _:
            shift_clock(monkeypatch, 10)
            rebound = admission.service.acquire_worker(successor(admission, owned.claim))
            assert fence(rebound).generation == owned.ticket.permit.fence.generation + 1
            # A new activation cannot remove durable staging owned by the old execution.
    with admission.sessions() as session:
        assert session.get(ResourceIoExecutionRow, owned.ticket.execution_id) is None
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert row.state == "EXITED" and row.closed_at is not None
        assert row.closure_kind == "PROCESS_EXIT" and row.exit_code == 0
        assert row.closure_evidence_sha256 == owned.proof.evidence_sha256
        assert row.child_pid == present(owned.proof.child).pid
        assert row.child_identity_sha256 == present(owned.proof.child).identity_sha256
    reconcile()


def test_closure_and_durable_stage_receipt_rollback_together(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = writer(admission)

    def fail_copy(*args: object) -> None:
        raise RuntimeError("test.abort_exit_receipt")

    with monkeypatch.context() as patch:
        patch.setattr(resource_execution, "preserve_provider_exit", fail_copy)
        with pytest.raises(RuntimeError, match=r"test\.abort_exit_receipt"):
            admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    with admission.sessions() as session:
        execution = present(session.get(ResourceIoExecutionRow, owned.ticket.execution_id))
        receipt = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert execution.state == "RUNNING" and execution.closed_at is None
        assert receipt.state == "OWNED" and receipt.closed_at is None
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    with admission.sessions() as session:
        assert present(session.get(ProviderStagingRow, owned.ticket.execution_id)).state == "EXITED"


@pytest.mark.parametrize("started", [False, True])
def test_source_revocation_does_not_prevent_durable_exit_receipt(
    admission: AdmissionHarness, started: bool
) -> None:
    owned = writer(admission, started=started)
    with admission.sessions.begin() as session:
        receipt = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        present(session.get(UserAccountRow, receipt.user_id)).authority_generation += 1
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    admission.service.close_io(owned.ticket.permit)
    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert row.state == "EXITED" and row.closure_kind == owned.proof.kind


def test_provider_receipt_identity_proof_and_transitions_are_immutable(
    admission: AdmissionHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    owned = writer(admission)
    changes = {
        "user_id": uuid4(),
        "acquisition_id": uuid4(),
        "execution_id": uuid4(),
        "job_worker_id": "other-worker",
        "job_attempt": 99,
        "operation_id": uuid4(),
        "staging_key": "other-stage",
    }
    for column, value in changes.items():
        with (
            pytest.raises(IntegrityError, match="Provider staging identity"),
            admission.sessions.begin() as session,
        ):
            session.execute(
                text(
                    f"UPDATE vault.provider_staging SET {column}=:value "
                    "WHERE execution_id=:execution"
                ),
                {"value": value, "execution": owned.ticket.execution_id},
            )
    with (
        pytest.raises(IntegrityError, match="Provider staging transition"),
        admission.sessions.begin() as session,
    ):
        session.execute(
            text("UPDATE vault.provider_staging SET state='CLEANED' WHERE execution_id=:execution"),
            {"execution": owned.ticket.execution_id},
        )
    admission.service.confirm_execution_exit(owned.ticket, owned.proof)
    with (
        pytest.raises(IntegrityError, match="Provider staging identity"),
        admission.sessions.begin() as session,
    ):
        session.execute(
            text(
                "UPDATE vault.provider_staging SET closure_evidence_sha256=:value "
                "WHERE execution_id=:execution"
            ),
            {"value": b"x" * 32, "execution": owned.ticket.execution_id},
        )
    with pytest.raises(DBAPIError, match="Refusing to discard provider staging ownership"):
        database_harness.downgrade(database_name, "0039_internet_ingest_lineage")


def test_legacy_provider_execution_without_staging_receipt_can_only_drain(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    _actor, claim = internet(admission)
    active = admission.service.acquire_worker(claim)
    permit = admission.service.open_io(claim, fence(active), claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    # Exact pre-0040 row shape; no persistent staging namespace was registered.
    with admission.sessions.begin() as session:
        now = datetime.now(UTC)
        session.add(
            ResourceIoExecutionRow(
                execution_id=ticket.execution_id,
                permit_id=permit.permit_id,
                operation_id=permit.fence.operation_id,
                activation_id=permit.fence.activation_id,
                generation=permit.fence.generation,
                target_id=permit.target_id,
                owner_run_id=ticket.owner_run_id,
                kind="PROVIDER",
                actual_target_id=claim.acquisition_id,
                state="PREPARED",
                created_at=now,
                heartbeat_at=now,
            )
        )
    with pytest.raises(ResourceAdmissionError, match="resource_provider_staging_unavailable"):
        admission.service.prepare_execution(claim, ticket)
    with pytest.raises(ResourceAdmissionError, match="resource_provider_staging_unavailable"):
        admission.service.start_execution(claim, ticket, ProcessIdentity(34567, b"c" * 32))
    admission.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
    )
    admission.service.close_io(permit)
    with admission.sessions() as session:
        assert session.get(ResourceIoExecutionRow, ticket.execution_id) is None
        assert session.get(ProviderStagingRow, ticket.execution_id) is None


@pytest.mark.usefixtures("internal_io_budget")
def test_orphaned_writer_staging_is_never_an_orphan_file(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = writer(admission)
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(owned.key)
    shift_clock(monkeypatch, 60)
    admission.service.sweep()
    with admission.sessions.begin() as session:
        assert (
            present(session.get(ResourceIoExecutionRow, owned.ticket.execution_id)).state
            == "ORPHANED"
        )
        assert present(session.get(ProviderStagingRow, owned.ticket.execution_id)).state == "OWNED"
    report = build_vault_reconciliation_service(admission.sessions, tmp_path).run(
        mode=ReconcileMode.APPLY
    )
    assert report.quarantined == report.claimed == 0
    assert storage.inventory().staging_keys == (owned.key,)
