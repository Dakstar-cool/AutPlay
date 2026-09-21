"""Atomically retire restored process reservations after actual host-empty proof."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import rfc8785
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.models.ingest_cleanup import IngestCleanupExecutionRow
from autplay.adapters.postgresql.models.ingest_execution import IngestExecutionRow
from autplay.adapters.postgresql.models.metadata_execution import (
    MetadataExecutionRow,
    MetadataProviderGateRow,
)
from autplay.adapters.postgresql.models.provider_maintenance import ProviderMaintenanceRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoExecutionRow
from autplay.adapters.postgresql.models.training_work import TrainingExecutionRow
from autplay.adapters.postgresql.provider_staging_runtime import preserve_provider_exit
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.domain.recommendations import JsonValue


@dataclass(frozen=True, slots=True)
class OfflineExecutionDrainReport:
    resource_executions: int
    ingest_executions: int
    ingest_cleanup_executions: int
    metadata_executions: int
    maintenance_executions: int
    training_stopping: int
    checked_pids: int
    checked_cgroups: int


class PostgresOfflineExecutionDrain:
    """Trusted offline boundary; callers must keep all normal supervisors stopped."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        process_evidence: OfflineProcessEvidenceProbe,
    ) -> None:
        self._sessions, self._process_evidence = sessions, process_evidence

    def close_restored_reservations(self) -> OfflineExecutionDrainReport:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            resources = list(
                session.scalars(
                    select(ResourceIoExecutionRow)
                    .where(ResourceIoExecutionRow.state != "CLOSED")
                    .order_by(ResourceIoExecutionRow.execution_id)
                    .with_for_update()
                )
            )
            ingests = list(
                session.scalars(
                    select(IngestExecutionRow)
                    .where(IngestExecutionRow.state != "CLOSED")
                    .order_by(IngestExecutionRow.execution_id)
                    .with_for_update()
                )
            )
            ingest_cleanups = list(
                session.scalars(
                    select(IngestCleanupExecutionRow)
                    .where(IngestCleanupExecutionRow.state != "CLOSED")
                    .order_by(IngestCleanupExecutionRow.execution_id)
                    .with_for_update()
                )
            )
            metadata = list(
                session.scalars(
                    select(MetadataExecutionRow)
                    .where(MetadataExecutionRow.state != "CLOSED")
                    .order_by(MetadataExecutionRow.execution_id)
                    .with_for_update()
                )
            )
            maintenance = list(
                session.scalars(
                    select(ProviderMaintenanceRow)
                    .where(ProviderMaintenanceRow.state != "CLOSED")
                    .order_by(ProviderMaintenanceRow.execution_id)
                    .with_for_update()
                )
            )
            training = list(
                session.scalars(
                    select(TrainingExecutionRow)
                    .where(TrainingExecutionRow.state != "CLOSED")
                    .order_by(TrainingExecutionRow.execution_id)
                    .with_for_update()
                )
            )
            observed_pids = (
                *(item.child_pid for item in resources),
                *(item.child_pid for item in ingests),
                *(item.child_pid for item in ingest_cleanups),
                *(item.child_pid for item in metadata),
                *(item.child_pid for item in maintenance),
                *(item.child_pid for item in training),
            )
            evidence = self._process_evidence.verify(
                tuple(pid for pid in observed_pids if isinstance(pid, int))
            )
            now = session.scalar(select(func.clock_timestamp()))
            if now is None:
                raise RuntimeError("database clock unavailable")

            for resource in resources:
                digest = self._row_evidence(
                    evidence.evidence_sha256, "resource", resource.execution_id
                )
                resource.state, resource.closed_at = "CLOSED", now
                resource.closure_kind = "SUPERVISOR_EXIT"
                resource.closure_evidence_sha256 = digest
                resource.exit_code = 137
                preserve_provider_exit(session, resource, now)
            for kind, rows in (
                ("ingest", ingests),
                ("ingest-cleanup", ingest_cleanups),
                ("maintenance", maintenance),
            ):
                for execution in rows:
                    digest = self._row_evidence(
                        evidence.evidence_sha256, kind, execution.execution_id
                    )
                    execution.state, execution.closed_at = "CLOSED", now
                    execution.closure_kind = "SUPERVISOR_EXIT"
                    execution.closure_evidence_sha256 = digest
                    execution.exit_code = 137
            gate = session.get(MetadataProviderGateRow, 1, with_for_update=True)
            for metadata_execution in metadata:
                digest = self._row_evidence(
                    evidence.evidence_sha256, "metadata", metadata_execution.execution_id
                )
                metadata_execution.state, metadata_execution.closed_at = "CLOSED", now
                metadata_execution.closure_kind = "SUPERVISOR_EXIT"
                metadata_execution.closure_evidence_sha256 = digest
                metadata_execution.exit_code = 137
                if gate is not None and gate.execution_id == metadata_execution.execution_id:
                    gate.execution_id = gate.request_id = None
                    gate.next_request_at = now
            for training_execution in training:
                if training_execution.state in {"PREPARED", "RUNNING"}:
                    session.execute(
                        text(
                            "SELECT app_private.authorize_training_execution_transition("
                            ":execution,:expected,'STOPPING')"
                        ),
                        {
                            "execution": training_execution.execution_id,
                            "expected": training_execution.state,
                        },
                    )
                    digest = self._row_evidence(
                        evidence.evidence_sha256,
                        "training",
                        training_execution.execution_id,
                    )
                    training_execution.state = "STOPPING"
                    training_execution.cleanup_started_at = now
                    training_execution.closure_kind = "SUPERVISOR_EXIT"
                    training_execution.closure_evidence_sha256 = digest
                    training_execution.exit_code = 137
                    training_execution.retain_checkpoint = False
            session.flush()
            return OfflineExecutionDrainReport(
                len(resources),
                len(ingests),
                len(ingest_cleanups),
                len(metadata),
                len(maintenance),
                len(training),
                len(evidence.checked_pids),
                len(evidence.checked_cgroups),
            )

    @staticmethod
    def _row_evidence(base: bytes, kind: str, execution_id: object) -> bytes:
        document: dict[str, JsonValue] = {
            "schema_version": 1,
            "kind": kind,
            "execution_id": str(execution_id),
            "host_evidence_sha256": base.hex(),
            "result": "SUPERVISOR_EXIT",
        }
        return sha256(rfc8785.dumps(document)).digest()


__all__ = ("OfflineExecutionDrainReport", "PostgresOfflineExecutionDrain")
