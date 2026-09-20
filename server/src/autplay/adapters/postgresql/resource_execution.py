"""Process execution accounting. Callers hold the common admission advisory lock."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    EXECUTION_HEARTBEAT_TTL,
    ExecutionState,
    ExecutionStatus,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)

from .models.resource_admission import ResourceIoExecutionRow, ResourceIoPermitRow
from .provider_staging_runtime import (
    preserve_provider_exit,
    register_provider_staging,
    require_provider_staging,
)


class SqlAlchemyResourceExecutionRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def _find(self, ticket: ExecutionTicket) -> ResourceIoExecutionRow:
        row = self._s.get(ResourceIoExecutionRow, ticket.execution_id, populate_existing=True)
        if row is None or (
            row.permit_id,
            row.operation_id,
            row.activation_id,
            row.generation,
            row.target_id,
            row.owner_run_id,
            row.kind,
            row.actual_target_id,
        ) != (
            ticket.permit.permit_id,
            ticket.permit.fence.operation_id,
            ticket.permit.fence.activation_id,
            ticket.permit.fence.generation,
            ticket.permit.target_id,
            ticket.owner_run_id,
            ticket.kind,
            ticket.actual_target_id,
        ):
            raise ResourceAdmissionError("resource_execution_stale")
        return row

    @staticmethod
    def _status(row: ResourceIoExecutionRow, ticket: ExecutionTicket) -> ExecutionStatus:
        child = None
        if row.child_pid is not None and row.child_identity_sha256 is not None:
            child = ProcessIdentity(row.child_pid, row.child_identity_sha256)
        return ExecutionStatus(
            ticket, ExecutionState(row.state), row.heartbeat_at, child, row.closed_at
        )

    def inspect(self, ticket: ExecutionTicket) -> ExecutionStatus:
        if self._absent(ticket):
            return ExecutionStatus(ticket, ExecutionState.ABSENT, None, None, None)
        return self._status(self._find(ticket), ticket)

    def _absent(self, ticket: ExecutionTicket) -> bool:
        """No remaining accounting row; never infer child death or grant I/O authority."""
        return (
            self._s.get(ResourceIoExecutionRow, ticket.execution_id, populate_existing=True) is None
            and self._s.get(ResourceIoPermitRow, ticket.permit.permit_id, populate_existing=True)
            is None
        )

    def _live_permit(self, ticket: ExecutionTicket, now: datetime) -> None:
        row = self._s.get(ResourceIoPermitRow, ticket.permit.permit_id, populate_existing=True)
        if (
            row is None
            or (
                row.operation_id,
                row.activation_id,
                row.generation,
                row.target_id,
            )
            != (
                ticket.permit.fence.operation_id,
                ticket.permit.fence.activation_id,
                ticket.permit.fence.generation,
                ticket.permit.target_id,
            )
            or row.expires_at <= now
        ):
            raise ResourceAdmissionError("resource_io_stale")

    def prepare(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus:
        self._live_permit(ticket, now)
        if self._s.get(ResourceIoExecutionRow, ticket.execution_id) is not None:
            require_provider_staging(self._s, self._find(ticket))
            return self.inspect(ticket)
        if (
            self._s.scalar(
                select(ResourceIoExecutionRow.execution_id).where(
                    ResourceIoExecutionRow.permit_id == ticket.permit.permit_id,
                )
            )
            is not None
        ):
            raise ResourceAdmissionError("resource_execution_conflict")
        if (
            ticket.kind != "VAULT_STREAM"
            and self._s.scalar(
                select(ResourceIoExecutionRow.execution_id).where(
                    ResourceIoExecutionRow.kind == ticket.kind,
                    ResourceIoExecutionRow.actual_target_id == ticket.actual_target_id,
                    ResourceIoExecutionRow.closed_at.is_(None),
                )
            )
            is not None
        ):
            raise ResourceAdmissionError("resource_execution_busy")
        row = ResourceIoExecutionRow(
            execution_id=ticket.execution_id,
            permit_id=ticket.permit.permit_id,
            operation_id=ticket.permit.fence.operation_id,
            activation_id=ticket.permit.fence.activation_id,
            generation=ticket.permit.fence.generation,
            target_id=ticket.permit.target_id,
            owner_run_id=ticket.owner_run_id,
            kind=ticket.kind,
            actual_target_id=ticket.actual_target_id,
            state="PREPARED",
            created_at=now,
            heartbeat_at=now,
        )
        self._s.add(row)
        register_provider_staging(self._s, row)
        self._s.flush()
        return self._status(row, ticket)

    def started(
        self, ticket: ExecutionTicket, child: ProcessIdentity, now: datetime
    ) -> ExecutionStatus:
        row = self._find(ticket)
        self._live_permit(ticket, now)
        require_provider_staging(self._s, row)
        status = self._status(row, ticket)
        if status.state == ExecutionState.RUNNING and status.child == child:
            return status
        if status.state != ExecutionState.PREPARED:
            raise ResourceAdmissionError("resource_execution_stale")
        row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
        row.started_at, row.heartbeat_at, row.state = now, now, "RUNNING"
        self._s.flush()
        return self._status(row, ticket)

    def heartbeat(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus:
        row = self._find(ticket)
        if row.state not in {"PREPARED", "RUNNING"}:
            raise ResourceAdmissionError("resource_execution_stale")
        row.heartbeat_at = now
        self._s.flush()
        return self._status(row, ticket)

    def stop(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus:
        row = self._find(ticket)
        if row.state != "CLOSED":
            row.stop_requested_at = row.stop_requested_at or now
            if row.state != "ORPHANED":
                row.state = "STOPPING"
            self._s.flush()
        return self._status(row, ticket)

    def orphan_stale(self, now: datetime, maximum: int) -> int:
        rows = list(
            self._s.scalars(
                select(ResourceIoExecutionRow)
                .where(
                    ResourceIoExecutionRow.state.in_(("PREPARED", "RUNNING", "STOPPING")),
                    ResourceIoExecutionRow.heartbeat_at <= now - EXECUTION_HEARTBEAT_TTL,
                )
                .order_by(ResourceIoExecutionRow.heartbeat_at, ResourceIoExecutionRow.execution_id)
                .limit(maximum)
            )
        )
        for row in rows:
            row.state = "ORPHANED"
        self._s.flush()
        return len(rows)

    def confirm_exit(
        self, ticket: ExecutionTicket, proof: ProcessExitEvidence, now: datetime
    ) -> ExecutionStatus:
        if self._absent(ticket):
            return ExecutionStatus(ticket, ExecutionState.ABSENT, None, None, None)
        row = self._find(ticket)
        status = self._status(row, ticket)
        if proof.child != status.child:
            raise ResourceAdmissionError("resource_execution_stale")
        if row.state == "CLOSED":
            if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != (
                proof.kind,
                proof.evidence_sha256,
                proof.exit_code,
            ):
                raise ResourceAdmissionError("resource_execution_conflict")
            return status
        if proof.kind == ExitKind.NOT_STARTED and row.state not in {"PREPARED", "STOPPING"}:
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        row.state, row.closed_at = "CLOSED", now
        row.closure_kind, row.closure_evidence_sha256, row.exit_code = (
            proof.kind,
            proof.evidence_sha256,
            proof.exit_code,
        )
        preserve_provider_exit(self._s, row, now)
        self._s.flush()
        return self._status(row, ticket)
