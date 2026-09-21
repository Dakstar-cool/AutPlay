"""Writer exclusion after locking an upload row, without an admission lock inversion."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select, true
from sqlalchemy.orm import Session

from autplay.domain.ingest_execution import IngestExecutionStatus
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionKind, ExecutionState, ExecutionStatus

from .ingest_execution_guard import require_ingest_execution
from .models.ingest_cleanup import IngestCleanupExecutionRow
from .models.ingest_execution import IngestExecutionRow
from .models.resource_admission import ResourceIoExecutionRow


def matches_upload_execution(row: ResourceIoExecutionRow, expected: ExecutionStatus) -> bool:
    ticket, child = expected.ticket, expected.child
    return (
        expected.state == ExecutionState.RUNNING
        and child is not None
        and ticket.kind == ExecutionKind.VAULT_UPLOAD
        and row.state == "RUNNING"
        and row.closed_at is None
        and (
            row.execution_id,
            row.owner_run_id,
            row.permit_id,
            row.operation_id,
            row.activation_id,
            row.generation,
            row.target_id,
            row.kind,
            row.actual_target_id,
            row.child_pid,
            row.child_identity_sha256,
        )
        == (
            ticket.execution_id,
            ticket.owner_run_id,
            ticket.permit.permit_id,
            ticket.permit.fence.operation_id,
            ticket.permit.fence.activation_id,
            ticket.permit.fence.generation,
            ticket.permit.target_id,
            ticket.kind,
            ticket.actual_target_id,
            child.pid,
            child.identity_sha256,
        )
    )


def upload_has_unclosed_writer(
    session: Session, upload_id: UUID, *, exclude_cleanup: UUID | None = None
) -> bool:
    return (
        session.scalar(
            select(ResourceIoExecutionRow.execution_id)
            .where(
                ResourceIoExecutionRow.kind == "VAULT_UPLOAD",
                ResourceIoExecutionRow.actual_target_id == upload_id,
                ResourceIoExecutionRow.closed_at.is_(None),
            )
            .limit(1)
        )
        is not None
        or session.scalar(
            select(IngestCleanupExecutionRow.execution_id)
            .where(
                IngestCleanupExecutionRow.claim_id == upload_id,
                IngestCleanupExecutionRow.closed_at.is_(None),
                IngestCleanupExecutionRow.execution_id != exclude_cleanup
                if exclude_cleanup is not None
                else true(),
            )
            .limit(1)
        )
        is not None
        or session.scalar(
            select(IngestExecutionRow.execution_id)
            .where(
                IngestExecutionRow.upload_session_id == upload_id,
                IngestExecutionRow.closed_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def unclosed_upload_targets() -> Select[tuple[UUID]]:
    return select(ResourceIoExecutionRow.actual_target_id).where(
        ResourceIoExecutionRow.kind == "VAULT_UPLOAD",
        ResourceIoExecutionRow.closed_at.is_(None),
    )


def require_upload_writer(
    session: Session,
    upload_id: UUID,
    *,
    expected: ExecutionStatus | None = None,
    ingest: IngestExecutionStatus | None = None,
) -> None:
    """Call after upload FOR UPDATE and before any staging mutation or child GO.

    A concurrently prepared child still needs this upload lock before GO and must
    recheck its state. Do not hold an execution row lock across filesystem work:
    the independent renewal/stop coordinator must remain able to update that row.
    A missing heartbeat, expired permit or terminal admission never proves exit.
    """
    require_ingest_execution(session, upload_id, ingest)
    with session.no_autoflush:
        if (
            session.scalar(
                select(IngestCleanupExecutionRow.execution_id)
                .where(
                    IngestCleanupExecutionRow.claim_id == upload_id,
                    IngestCleanupExecutionRow.closed_at.is_(None),
                )
                .limit(1)
            )
            is not None
        ):
            raise ResourceAdmissionError("ingest_cleanup_busy")
        row = session.scalar(
            select(ResourceIoExecutionRow)
            .where(
                ResourceIoExecutionRow.kind == "VAULT_UPLOAD",
                ResourceIoExecutionRow.actual_target_id == upload_id,
                ResourceIoExecutionRow.closed_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if expected is None:
            if row is not None:
                raise ResourceAdmissionError("resource_execution_busy")
        elif row is None or not matches_upload_execution(row, expected):
            raise ResourceAdmissionError("resource_execution_stale")
