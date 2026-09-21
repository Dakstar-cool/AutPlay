"""Measured-budget initialization needs actual atomic PostgreSQL and exact replay."""

from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.resource_admission import ResourceQuotaPolicyRow
from autplay.adapters.postgresql.resource_measurements import SqlAlchemyResourceBudgetInitializer
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_measurements import (
    MEASUREMENT_PATHS,
    InitializeResourceBudget,
    ResourceMeasurement,
)
from autplay.domain.resource_policy import GlobalResourceLimits, QuotaChange
from autplay.entrypoints.admin import build_parser
from autplay.entrypoints.resource_measurements import run_resource_budget_initialize
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from .test_resource_admission_runtime import present
from .test_resource_policy import PolicyHarness, policy

__all__ = ["policy"]


def document(server: UUID) -> dict[str, Any]:
    metrics = {
        "cpu_peak_percent": 35,
        "disk_busy_peak_percent": 20,
        "network_busy_peak_percent": 20,
        "disk_read_mib_per_second": 5,
        "disk_write_mib_per_second": 5,
        "network_receive_mib_per_second": 5,
        "network_send_mib_per_second": 5,
        "database_p95_ms": 10,
        "queue_wait_p95_ms": 10,
        "queue_depth_max": 1,
        "playback_mib_per_second": 1,
        "transfer_mib_per_second": 5,
        "successful_operations": 10,
        "failed_operations": 0,
        "timed_out_operations": 0,
    }
    return {
        "version": 1,
        "measurement_id": str(uuid4()),
        "server_instance_id": str(server),
        "identity_epoch": 1,
        "environment_sha256": "a" * 64,
        "workload_sha256": "b" * 64,
        "measured_at": (datetime.now(UTC) - timedelta(minutes=2))
        .isoformat()
        .replace("+00:00", "Z"),
        "duration_seconds": 120,
        "sample_count": 20,
        "simultaneous_playbacks": 4,
        "simultaneous_transfers": 4,
        "metrics": metrics,
        "acceptance_maxima": {
            name: 0 if name in {"failed_operations", "timed_out_operations"} else 100
            for name in metrics
        },
        "acceptance_minima": {
            "duration_seconds": 60,
            "sample_count": 10,
            "playback_mib_per_second": 0.1,
            "transfer_mib_per_second": 0.1,
            "successful_operations": 10,
        },
        "workload_paths": sorted(MEASUREMENT_PATHS),
    }


def command(harness: PolicyHarness, data: dict[str, Any] | None = None) -> InitializeResourceBudget:
    payload = json.dumps(data or document(harness.actor.server_instance_id)).encode()
    report = ResourceMeasurement.parse(payload)
    return InitializeResourceBudget(
        uuid4(),
        1,
        report,
        hashlib.sha256(payload).hexdigest(),
        "a" * 64,
        GlobalResourceLimits(2, 2),
        GlobalResourceLimits(3, 3),
    )


@pytest.mark.parametrize(
    "failure", ["nan", "boolean", "unknown", "failed", "idle", "coverage", "duplicate", "huge"]
)
def test_report_rejects_invalid_or_unsuccessful_measurement(failure: str) -> None:
    data = document(uuid4())
    if failure == "nan":
        data["metrics"]["cpu_peak_percent"] = float("nan")
    elif failure == "boolean":
        data["simultaneous_playbacks"] = True
    elif failure == "unknown":
        data["private_path"] = "must not enter audit"
    elif failure == "failed":
        data["metrics"]["failed_operations"] = 1
    elif failure == "idle":
        data["metrics"]["playback_mib_per_second"] = 0
    elif failure == "coverage":
        data["workload_paths"].remove("UPLOAD")
    payload = json.dumps(data).encode()
    if failure == "duplicate":
        payload = payload.replace(b'"version": 1', b'"version": 1, "version": 1')
    if failure == "huge":
        payload += b" " * 65536
    with pytest.raises(ResourceAdmissionError):
        ResourceMeasurement.parse(payload)


def test_initializer_records_evidence_once_and_replays_after_later_web_edit(
    policy: PolicyHarness,
) -> None:
    request = command(policy)
    initializer = SqlAlchemyResourceBudgetInitializer(policy.sessions)
    result = initializer.initialize(request)
    assert result["global_revision"] == 2
    with policy.sessions() as session:
        row = present(session.get(ResourceQuotaPolicyRow, 1))
        assert (row.default_devices, row.default_playbacks, row.default_transfers) == (5, 2, 2)
        audit = present(session.get(AuditEventRow, request.operation_id))
        evidence = audit.metadata_sanitized
        assert isinstance(evidence, dict)
        assert evidence["report_sha256"] == request.report.report_sha256
        assert evidence["workload_paths"] == list(request.report.workload_paths)
        assert evidence["metrics"] == dict(request.report.metrics)
    policy.service.apply(policy.actor, QuotaChange(uuid4(), 2, GlobalResourceLimits(1, 1)))
    assert initializer.initialize(request) == result
    with policy.sessions() as session:
        row = present(session.get(ResourceQuotaPolicyRow, 1))
        assert row.revision == 3 and row.global_playbacks == 1


@pytest.mark.parametrize(
    "failure", ["report", "environment", "ceiling", "epoch", "future", "revision"]
)
def test_initializer_rejects_mismatched_evidence_without_mutation(
    policy: PolicyHarness, failure: str
) -> None:
    data = document(policy.actor.server_instance_id)
    if failure == "epoch":
        data["identity_epoch"] = 2
    elif failure == "future":
        data["measured_at"] = (
            (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        )
    request = command(policy, data)
    initializer = SqlAlchemyResourceBudgetInitializer(policy.sessions)
    with pytest.raises(ResourceAdmissionError):
        if failure == "report":
            request = replace(request, reviewed_report_sha256="f" * 64)
        elif failure == "environment":
            request = replace(request, expected_environment_sha256="f" * 64)
        elif failure == "ceiling":
            request = replace(request, ceilings=GlobalResourceLimits(5, 5))
        elif failure == "revision":
            request = replace(request, expected_revision=2)
        initializer.initialize(request)
    with policy.sessions() as session:
        row = present(session.get(ResourceQuotaPolicyRow, 1))
        assert row.revision == 1 and row.global_playbacks is None
        assert session.get(AuditEventRow, request.operation_id) is None


def test_changed_replay_and_new_initialization_cannot_overwrite_budget(
    policy: PolicyHarness,
) -> None:
    request = command(policy)
    initializer = SqlAlchemyResourceBudgetInitializer(policy.sessions)
    initializer.initialize(request)
    with pytest.raises(ResourceAdmissionError, match="resource_operation_conflict"):
        initializer.initialize(replace(request, limits=GlobalResourceLimits(1, 1)))
    with pytest.raises(ResourceAdmissionError, match="resource_budget_already_configured"):
        initializer.initialize(replace(request, operation_id=uuid4(), expected_revision=2))
    with policy.sessions.begin() as session:
        identity = present(session.get(ServerInstanceRow, policy.actor.server_instance_id))
        identity.identity_epoch += 1
    with pytest.raises(ResourceAdmissionError, match="resource_measurement_mismatch"):
        initializer.initialize(request)


def test_two_initializers_share_one_cas_and_one_audit_result(policy: PolicyHarness) -> None:
    first = command(policy)
    second = replace(first, operation_id=uuid4(), limits=GlobalResourceLimits(1, 1))
    initializer = SqlAlchemyResourceBudgetInitializer(policy.sessions)
    barrier = Barrier(2)

    def apply(request: InitializeResourceBudget) -> str:
        barrier.wait(timeout=5)
        try:
            initializer.initialize(request)
        except ResourceAdmissionError as error:
            return error.code
        return "INITIALIZED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(apply, (first, second)))
    assert sorted(outcomes) == ["INITIALIZED", "resource_revision_stale"]
    with policy.sessions() as session:
        assert (
            len(
                session.scalars(
                    select(AuditEventRow).where(
                        AuditEventRow.audit_event_id.in_([first.operation_id, second.operation_id])
                    )
                ).all()
            )
            == 1
        )
        assert present(session.get(ResourceQuotaPolicyRow, 1)).revision == 2


def test_audit_failure_rolls_back_budget(policy: PolicyHarness) -> None:
    request = command(policy)

    def fail_audit(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: Any,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO audit.audit_event"):
            raise DBAPIError(statement, parameters, RuntimeError("synthetic audit failure"))

    event.listen(policy.admissions.engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(DBAPIError):
            SqlAlchemyResourceBudgetInitializer(policy.sessions).initialize(request)
    finally:
        event.remove(policy.admissions.engine, "before_cursor_execute", fail_audit)
    with policy.sessions() as session:
        row = present(session.get(ResourceQuotaPolicyRow, 1))
        assert row.revision == 1 and row.global_playbacks is None
        assert session.get(AuditEventRow, request.operation_id) is None


def test_local_cli_applies_reviewed_report_and_redacts_missing_path(
    policy: PolicyHarness, tmp_path: Path
) -> None:
    payload = json.dumps(document(policy.actor.server_instance_id)).encode()
    path = tmp_path / "synthetic-measurement.json"
    path.write_bytes(payload)
    args = build_parser().parse_args(
        [
            "resource-budget-initialize",
            "--report",
            str(path),
            "--reviewed-report-sha256",
            hashlib.sha256(payload).hexdigest(),
            "--expected-environment-sha256",
            "a" * 64,
            "--operation-id",
            str(uuid4()),
            "--expected-revision",
            "1",
            "--playbacks",
            "2",
            "--transfers",
            "2",
            "--playback-ceiling",
            "3",
            "--transfer-ceiling",
            "3",
        ]
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    service = SqlAlchemyResourceBudgetInitializer(policy.sessions)
    assert run_resource_budget_initialize(service, args, stdout=stdout, stderr=stderr) == 0
    assert json.loads(stdout.getvalue())["outcome"] == "INITIALIZED"
    args.report = str(tmp_path / "private-missing-path")
    assert run_resource_budget_initialize(service, args, stdout=stdout, stderr=stderr) == 2
    assert "private-missing-path" not in stdout.getvalue() + stderr.getvalue()
