"""Read ownership under the existing admission/upload lock order; never infer exit."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autplay.domain.ingest_execution import IngestExecutionStatus, IngestExecutionTicket
from autplay.domain.jobs import LeaseFence
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey

from .models.ingest_execution import IngestExecutionRow


def ingest_status(row: IngestExecutionRow) -> IngestExecutionStatus:
    child = None
    if row.child_pid is not None and row.child_identity_sha256 is not None:
        child = ProcessIdentity(row.child_pid, row.child_identity_sha256)
    return IngestExecutionStatus(
        IngestExecutionTicket(
            row.execution_id,
            row.owner_run_id,
            row.upload_session_id,
            OpaqueStorageKey(row.staging_key),
            LeaseFence(row.job_id, row.worker_id, row.attempt_no),
        ),
        ExecutionState(row.state),
        child,
        row.io_deadline_at,
        row.heartbeat_at,
    )


def require_ingest_execution(
    session: Session,
    upload_id: UUID,
    expected: IngestExecutionStatus | None = None,
) -> None:
    with session.no_autoflush:
        row = session.scalar(
            select(IngestExecutionRow)
            .where(
                IngestExecutionRow.upload_session_id == upload_id,
                IngestExecutionRow.closed_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if expected is None:
            if row is not None:
                raise ResourceAdmissionError("ingest_execution_busy")
            return
        now = session.scalar(select(func.clock_timestamp()))
        if (
            row is None
            or expected.state != ExecutionState.RUNNING
            or expected.child is None
            or row.state != "RUNNING"
            or ingest_status(row).ticket != expected.ticket
            or ingest_status(row).child != expected.child
            or not isinstance(now, datetime)
            or row.io_deadline_at is None
            or row.io_deadline_at <= now
        ):
            raise ResourceAdmissionError("ingest_execution_stale")
