"""Operator-reviewed measured budgets; no browser/device authority is inferred."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_measurements import InitializeResourceBudget

from .models.audit import AuditEventRow
from .models.profile_pairing import ServerInstanceRow
from .models.resource_admission import ResourceQuotaPolicyRow
from .models.types import JsonValue
from .resource_limits import lock_resource_admission

_ACTION = "resource_quota.measured_budget_initialized"


class SqlAlchemyResourceBudgetInitializer:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def initialize(self, command: InitializeResourceBudget) -> dict[str, object]:
        report = command.report
        with self._sessions.begin() as session:
            identity = session.get(
                ServerInstanceRow, report.server_instance_id, with_for_update=True
            )
            if identity is None or identity.identity_epoch != report.identity_epoch:
                raise ResourceAdmissionError("resource_measurement_mismatch")
            lock_resource_admission(session)
            policy = session.get(ResourceQuotaPolicyRow, 1, with_for_update=True)
            if policy is None:
                raise ResourceAdmissionError("resource_budget_unconfigured")
            receipt = session.get(AuditEventRow, command.operation_id)
            if receipt is not None:
                receipt_metadata = receipt.metadata_sanitized
                if (
                    receipt.action != _ACTION
                    or receipt.actor_type != "SYSTEM"
                    or receipt.target_type != "RESOURCE_QUOTA_POLICY"
                    or receipt.target_id != report.server_instance_id
                    or not isinstance(receipt_metadata, dict)
                    or receipt_metadata.get("schema_version") != 1
                    or receipt_metadata.get("request_sha256") != command.digest
                    or receipt_metadata.get("report_sha256") != report.report_sha256
                    or not isinstance(receipt_metadata.get("result"), dict)
                ):
                    raise ResourceAdmissionError("resource_operation_conflict")
                return cast(dict[str, object], receipt_metadata["result"])
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ResourceAdmissionError()
            if report.measured_at > now:
                raise ResourceAdmissionError("resource_measurement_invalid")
            if policy.revision != command.expected_revision:
                raise ResourceAdmissionError("resource_revision_stale")
            if any(
                value is not None
                for value in (
                    policy.global_playbacks,
                    policy.global_transfers,
                    policy.playback_ceiling,
                    policy.transfer_ceiling,
                    policy.budget_evidence,
                )
            ):
                raise ResourceAdmissionError("resource_budget_already_configured")
            policy.global_playbacks, policy.global_transfers = (
                command.limits.playbacks,
                command.limits.transfers,
            )
            policy.playback_ceiling, policy.transfer_ceiling = (
                command.ceilings.playbacks,
                command.ceilings.transfers,
            )
            policy.budget_evidence = "sha256:" + report.report_sha256
            policy.revision += 1
            policy.updated_at = now
            result: dict[str, object] = {
                "operation_id": str(command.operation_id),
                "outcome": "INITIALIZED",
                "server_instance_id": str(report.server_instance_id),
                "identity_epoch": report.identity_epoch,
                "global_revision": policy.revision,
                "global_playbacks": policy.global_playbacks,
                "global_transfers": policy.global_transfers,
                "playback_ceiling": policy.playback_ceiling,
                "transfer_ceiling": policy.transfer_ceiling,
                "report_sha256": report.report_sha256,
                "applied_at": now.isoformat().replace("+00:00", "Z"),
            }
            metadata: dict[str, object] = {
                "schema_version": 1,
                "request_sha256": command.digest,
                "report_sha256": report.report_sha256,
                "measurement_id": str(report.measurement_id),
                "environment_sha256": report.environment_sha256,
                "workload_sha256": report.workload_sha256,
                "measured_at": report.measured_at.isoformat().replace("+00:00", "Z"),
                "duration_seconds": report.duration_seconds,
                "sample_count": report.sample_count,
                "simultaneous_playbacks": report.simultaneous.playbacks,
                "simultaneous_transfers": report.simultaneous.transfers,
                "metrics": dict(report.metrics),
                "acceptance_maxima": dict(report.acceptance_maxima),
                "acceptance_minima": dict(report.acceptance_minima),
                "workload_paths": list(report.workload_paths),
                "result": result,
            }
            session.add(
                AuditEventRow(
                    audit_event_id=command.operation_id,
                    occurred_at=now,
                    actor_type="SYSTEM",
                    action=_ACTION,
                    target_type="RESOURCE_QUOTA_POLICY",
                    target_id=report.server_instance_id,
                    request_id=command.operation_id,
                    reason_code="LOCAL_OPERATOR_REVIEWED_MEASUREMENT",
                    metadata_sanitized=cast(JsonValue, metadata),
                )
            )
            session.flush()
            return result
