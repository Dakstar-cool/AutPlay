"""Shared internal capacity is charged by durable execution rows, never a lease TTL."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.internal_io import InitializeInternalIoBudget
from autplay.domain.resource_admission import ResourceAdmissionError, positive_limit

from .models.audit import AuditEventRow
from .models.ingest_cleanup import IngestCleanupExecutionRow
from .models.ingest_execution import IngestExecutionRow
from .models.internal_io import InternalIoPolicyRow
from .models.metadata_execution import MetadataExecutionRow
from .models.profile_pairing import ServerInstanceRow
from .models.provider_maintenance import ProviderMaintenanceRow
from .models.resource_admission import ResourceQuotaPolicyRow
from .models.training_work import TrainingExecutionRow
from .models.types import JsonValue
from .resource_limits import lock_resource_admission


def internal_io_usage(session: Session) -> int:
    occupied = union_all(
        *(
            select(model.execution_id).where(model.closed_at.is_(None))
            for model in (
                IngestExecutionRow,
                IngestCleanupExecutionRow,
                ProviderMaintenanceRow,
                MetadataExecutionRow,
                TrainingExecutionRow,
            )
        )
    ).subquery()
    return int(session.scalar(select(func.count()).select_from(occupied)) or 0)


def internal_io_policy(session: Session) -> InternalIoPolicyRow:
    policy = session.get(InternalIoPolicyRow, 1, populate_existing=True)
    if policy is None or policy.active_limit is None or policy.server_instance_id is None:
        raise ResourceAdmissionError("internal_io_budget_unconfigured")
    # Plain reads preserve identity-before-admission lock order. Identity changes
    # also take admission; a fresh statement observes the serialized current epoch.
    identity = session.get(ServerInstanceRow, policy.server_instance_id, populate_existing=True)
    audio = session.get(ResourceQuotaPolicyRow, 1, populate_existing=True)
    if (
        identity is None
        or identity.identity_epoch != policy.identity_epoch
        or audio is None
        or audio.playback_ceiling is None
        or audio.transfer_ceiling is None
        or policy.playback_ceiling is None
        or policy.transfer_ceiling is None
        or audio.playback_ceiling > policy.playback_ceiling
        or audio.transfer_ceiling > policy.transfer_ceiling
    ):
        raise ResourceAdmissionError("internal_io_budget_mismatch")
    return policy


def require_internal_io_capacity(session: Session) -> None:
    """Caller holds the common admission lock through the PREPARED insertion."""
    policy = internal_io_policy(session)
    assert policy.active_limit is not None
    if internal_io_usage(session) >= policy.active_limit:
        raise ResourceAdmissionError("internal_io_busy")


def internal_io_wait_required(session: Session) -> bool:
    try:
        require_internal_io_capacity(session)
    except ResourceAdmissionError:
        return True
    return False


class PostgresInternalIoBudget:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        value = session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise ResourceAdmissionError("internal_io_unavailable")
        return value

    @staticmethod
    def _replay(
        session: Session, operation: UUID, action: str, digest: str
    ) -> dict[str, object] | None:
        receipt = session.get(AuditEventRow, operation)
        if receipt is None:
            return None
        data = receipt.metadata_sanitized
        if (
            receipt.action != action
            or receipt.actor_type != "SYSTEM"
            or receipt.target_type != "INTERNAL_IO_POLICY"
            or not isinstance(data, dict)
            or data.get("request_sha256") != digest
            or not isinstance(data.get("result"), dict)
        ):
            raise ResourceAdmissionError("resource_operation_conflict")
        return cast(dict[str, object], data["result"])

    @staticmethod
    def _record(
        session: Session,
        policy: InternalIoPolicyRow,
        operation: UUID,
        action: str,
        digest: str,
        details: dict[str, object],
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "operation_id": str(operation),
            "revision": policy.revision,
            "active_limit": policy.active_limit,
            "measured_ceiling": policy.measured_ceiling,
            "report_sha256": policy.report_sha256,
            "outcome": "APPLIED",
        }
        session.add(
            AuditEventRow(
                audit_event_id=operation,
                occurred_at=policy.updated_at,
                actor_type="SYSTEM",
                action=action,
                target_type="INTERNAL_IO_POLICY",
                target_id=policy.server_instance_id,
                request_id=operation,
                reason_code="LOCAL_OPERATOR_REVIEWED_MEASUREMENT",
                metadata_sanitized=cast(
                    JsonValue,
                    {"schema_version": 1, "request_sha256": digest, **details, "result": result},
                ),
            )
        )
        session.flush()
        return result

    def initialize(self, command: InitializeInternalIoBudget) -> dict[str, object]:
        """Apply/review new evidence atomically; exact replay never overwrites later edits."""
        report, resources = command.report, command.report.resources
        action = "internal_io.measured_budget_applied"
        with self._sessions.begin() as session:
            identity = session.get(
                ServerInstanceRow, resources.server_instance_id, with_for_update=True
            )
            if identity is None or identity.identity_epoch != resources.identity_epoch:
                raise ResourceAdmissionError("internal_io_measurement_mismatch")
            lock_resource_admission(session)
            policy = session.get(InternalIoPolicyRow, 1, with_for_update=True)
            if policy is None:
                raise ResourceAdmissionError("internal_io_budget_unconfigured")
            replay = self._replay(session, command.operation_id, action, command.digest)
            if replay is not None:
                return replay
            if policy.revision != command.expected_revision:
                raise ResourceAdmissionError("resource_revision_stale")
            if report.version < policy.workload_version:
                raise ResourceAdmissionError("internal_io_workload_downgrade")
            audio = session.get(ResourceQuotaPolicyRow, 1)
            if audio is None or audio.playback_ceiling is None or audio.transfer_ceiling is None:
                raise ResourceAdmissionError("resource_budget_unconfigured")
            if (
                audio.playback_ceiling > resources.simultaneous.playbacks
                or audio.transfer_ceiling > resources.simultaneous.transfers
            ):
                raise ResourceAdmissionError("internal_io_joint_budget_exceeded")
            now = self._now(session)
            if resources.measured_at > now:
                raise ResourceAdmissionError("internal_io_measurement_invalid")
            policy.active_limit, policy.measured_ceiling = command.limit, command.ceiling
            policy.server_instance_id, policy.identity_epoch = (
                resources.server_instance_id,
                resources.identity_epoch,
            )
            policy.environment_sha256, policy.workload_sha256 = (
                resources.environment_sha256,
                resources.workload_sha256,
            )
            policy.report_sha256 = report.report_sha256
            policy.workload_version = report.version
            policy.playback_ceiling, policy.transfer_ceiling = (
                resources.simultaneous.playbacks,
                resources.simultaneous.transfers,
            )
            policy.initialized_at, policy.updated_at = now, now
            policy.revision += 1
            return self._record(
                session,
                policy,
                command.operation_id,
                action,
                command.digest,
                {
                    "measurement_id": str(resources.measurement_id),
                    "identity_epoch": resources.identity_epoch,
                    "environment_sha256": resources.environment_sha256,
                    "workload_sha256": resources.workload_sha256,
                    "report_sha256": report.report_sha256,
                    "measured_at": resources.measured_at.isoformat(),
                    "duration_seconds": resources.duration_seconds,
                    "sample_count": resources.sample_count,
                    "simultaneous_playbacks": resources.simultaneous.playbacks,
                    "simultaneous_transfers": resources.simultaneous.transfers,
                    "simultaneous_internal_io": report.simultaneous,
                    "worst_permitted_mix": True,
                    "workload_version": report.version,
                    "metrics": dict(resources.metrics),
                    "acceptance_maxima": dict(resources.acceptance_maxima),
                    "acceptance_minima": dict(resources.acceptance_minima),
                    "audio_workload_paths": list(resources.workload_paths),
                    "internal_workload_paths": list(report.workload_paths),
                    "internal_mib_per_second": report.mib_per_second,
                    "minimum_internal_mib_per_second": report.minimum_mib_per_second,
                    "successful_internal_operations": report.successful_operations,
                    "minimum_successful_internal_operations": report.minimum_successful_operations,
                },
            )

    def set_limit(self, operation: UUID, expected_revision: int, limit: int) -> dict[str, object]:
        import hashlib
        import json

        positive_limit(limit)
        if type(expected_revision) is not int or not 1 <= expected_revision <= 2**53 - 1:
            raise ResourceAdmissionError("resource_revision_stale")
        digest = hashlib.sha256(
            json.dumps([str(operation), expected_revision, limit]).encode()
        ).hexdigest()
        action = "internal_io.limit_changed"
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            policy = internal_io_policy(session)
            replay = self._replay(session, operation, action, digest)
            if replay is not None:
                return replay
            if policy.revision != expected_revision:
                raise ResourceAdmissionError("resource_revision_stale")
            if policy.measured_ceiling is None or limit > policy.measured_ceiling:
                raise ResourceAdmissionError("internal_io_budget_exceeded")
            policy.active_limit, policy.revision = limit, policy.revision + 1
            policy.updated_at = self._now(session)
            return self._record(session, policy, operation, action, digest, {})
