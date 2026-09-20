"""Short ingest ownership transactions; losing a job cannot release its writer."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.job_worker import JobLeaseLost, JobResourceWait
from autplay.domain.ingest_execution import (
    INGEST_IO_TTL,
    MAX_INGEST_COORDINATOR_ENTRIES,
    IngestExecutionStatus,
    IngestExecutionTicket,
)
from autplay.domain.jobs import JobAttemptOutcome, JobState, LeaseFence
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey

from .ingest_execution_guard import ingest_status, require_ingest_execution
from .internal_io import internal_io_wait_required, require_internal_io_capacity
from .internet_ingest_authority import lock_internet_ingest_scope
from .jobs_runtime import PostgresJobRepository
from .models.ingest_cleanup import IngestCleanupExecutionRow
from .models.ingest_execution import IngestExecutionRow
from .models.jobs import JobRow
from .models.vault import UploadSessionRow
from .resource_limits import lock_resource_admission
from .resource_upload_guard import upload_has_unclosed_writer
from .vault_runtime import PostgresVaultRuntime


class PostgresIngestExecutionRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def plan(self, upload_id: UUID, fence: LeaseFence, owner: UUID) -> IngestExecutionTicket | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            PostgresVaultRuntime(session)._require_ingest_fence(fence, upload_id)
            upload = session.get(
                UploadSessionRow, upload_id, with_for_update=True, populate_existing=True
            )
            if upload is None or upload.job_id != fence.job_id:
                raise ResourceAdmissionError("ingest_execution_stale")
            if upload.state in {"COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED"}:
                return None
            return IngestExecutionTicket(
                uuid4(), owner, upload_id, OpaqueStorageKey(upload.staging_key), fence
            )

    def defer(self, fence: LeaseFence, upload_id: UUID, blockers: tuple[UUID, ...]) -> None:
        """Yield to retained writers without consuming retry attempts or audio permits."""
        # WORK and CLEANUP each retain a separately bounded process registry.
        if len(blockers) > 2 * MAX_INGEST_COORDINATOR_ENTRIES:
            raise ValueError("ingest_wait_bound_invalid")
        cancelled = False
        deferred = False
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            jobs = PostgresJobRepository(session)
            cancellation = jobs._lock_active_lease(fence)
            if cancellation is None:
                raise JobLeaseLost
            if cancellation:
                jobs._finish(
                    fence, outcome=JobAttemptOutcome.CANCELLED, state=JobState.CANCELLED, error=None
                )
                cancelled = True
            else:
                PostgresVaultRuntime(session)._require_ingest_fence(fence, upload_id)
                session.get(UploadSessionRow, upload_id, with_for_update=True)
                deferred = (
                    internal_io_wait_required(session)
                    or upload_has_unclosed_writer(session, upload_id)
                    or bool(
                        session.scalar(
                            select(IngestExecutionRow.execution_id)
                            .where(
                                IngestExecutionRow.execution_id.in_(blockers),
                                IngestExecutionRow.closed_at.is_(None),
                            )
                            .limit(1)
                        )
                    )
                    or bool(
                        session.scalar(
                            select(IngestCleanupExecutionRow.execution_id)
                            .where(
                                IngestCleanupExecutionRow.execution_id.in_(blockers),
                                IngestCleanupExecutionRow.closed_at.is_(None),
                            )
                            .limit(1)
                        )
                    )
                )
                if deferred:
                    jobs._finish(
                        fence,
                        outcome=JobAttemptOutcome.RESOURCE_WAIT,
                        state=JobState.RETRY_WAIT,
                        error=None,
                        retry_delay=timedelta(seconds=2),
                    )
                    job = session.get(JobRow, fence.job_id, populate_existing=True)
                    if job is None:
                        raise JobLeaseLost
                    job.resource_wait_count += 1
                    job.resource_waiting = True
        if cancelled or deferred:
            raise JobResourceWait(cancelled=cancelled)

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("ingest_execution_unavailable")
        return now

    @staticmethod
    def _locked(session: Session, ticket: IngestExecutionTicket) -> IngestExecutionRow:
        row = session.get(
            IngestExecutionRow, ticket.execution_id, with_for_update=True, populate_existing=True
        )
        if row is None or ingest_status(row).ticket != ticket:
            raise ResourceAdmissionError("ingest_execution_stale")
        return row

    def prepare(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(IngestExecutionRow, ticket.execution_id)
            if row is not None:
                if ingest_status(row).ticket != ticket:
                    raise ResourceAdmissionError("ingest_execution_stale")
                # Inspection/replay cannot authorize spawn/GO. Start checks again.
                return ingest_status(row)
            ingest = PostgresVaultRuntime(session).start_ingest(
                ticket.upload_session_id,
                ticket.fence.job_id,
                fence=ticket.fence,
            )
            if ingest is None or ingest.staging_key != ticket.staging_key:
                raise ResourceAdmissionError("ingest_execution_stale")
            require_internal_io_capacity(session)
            row = IngestExecutionRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                upload_session_id=ticket.upload_session_id,
                staging_key=ticket.staging_key.value,
                job_id=ticket.fence.job_id,
                worker_id=ticket.fence.worker_id,
                attempt_no=ticket.fence.attempt_no,
                state="PREPARED",
                created_at=self._now(session),
            )
            session.add(row)
            session.flush()
            return ingest_status(row)

    def _authorize_locks(self, session: Session, ticket: IngestExecutionTicket) -> None:
        lock_resource_admission(session)
        PostgresVaultRuntime(session)._require_ingest_fence(ticket.fence, ticket.upload_session_id)
        lock_internet_ingest_scope(session, ticket.upload_session_id)
        upload = session.get(
            UploadSessionRow, ticket.upload_session_id, with_for_update=True, populate_existing=True
        )
        if upload is None or upload.staging_key != ticket.staging_key.value:
            raise ResourceAdmissionError("ingest_execution_stale")

    def _refresh(self, session: Session, row: IngestExecutionRow) -> IngestExecutionStatus:
        # A first start is still assembling the complete RUNNING tuple. Do not
        # let an identity-map miss flush it before deadline fields are assigned.
        with session.no_autoflush:
            now = self._now(session)
            job = session.get(JobRow, row.job_id)
        if row.io_deadline_at is not None and row.io_deadline_at <= now:
            raise ResourceAdmissionError("ingest_execution_stale")
        if job is None or job.lease_deadline is None:
            raise ResourceAdmissionError("ingest_execution_stale")
        if job.lease_deadline <= now:
            raise JobLeaseLost
        row.heartbeat_at, row.io_deadline_at = now, min(now + INGEST_IO_TTL, job.lease_deadline)
        try:
            session.flush()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "55000":
                raise ResourceAdmissionError("ingest_execution_stale") from error
            raise
        current = ingest_status(row)
        ingest = PostgresVaultRuntime(session).start_ingest(
            row.upload_session_id,
            row.job_id,
            fence=current.ticket.fence,
            execution=current,
        )
        if ingest is None or ingest.staging_key != current.ticket.staging_key:
            raise ResourceAdmissionError("ingest_execution_stale")
        return current

    def start(self, ticket: IngestExecutionTicket, child: ProcessIdentity) -> IngestExecutionStatus:
        with self._sessions.begin() as session:
            self._authorize_locks(session, ticket)
            row = self._locked(session, ticket)
            if row.state == "RUNNING" and ingest_status(row).child == child:
                require_ingest_execution(session, ticket.upload_session_id, ingest_status(row))
            elif row.state == "PREPARED":
                row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
                row.state, row.started_at = "RUNNING", self._now(session)
            else:
                raise ResourceAdmissionError("ingest_execution_stale")
            return self._refresh(session, row)

    def renew(self, ticket: IngestExecutionTicket, child: ProcessIdentity) -> IngestExecutionStatus:
        with self._sessions.begin() as session:
            self._authorize_locks(session, ticket)
            row = self._locked(session, ticket)
            current = ingest_status(row)
            if current.child != child:
                raise ResourceAdmissionError("ingest_execution_stale")
            require_ingest_execution(session, ticket.upload_session_id, current)
            return self._refresh(session, row)

    def status(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus | None:
        """Read retained evidence; this method does not refresh I/O authorization."""
        with self._sessions() as session:
            row = session.get(IngestExecutionRow, ticket.execution_id)
            if row is None:
                return None
            current = ingest_status(row)
            if current.ticket != ticket:
                raise ResourceAdmissionError("ingest_execution_stale")
            return current

    def reconcile(self, ticket: IngestExecutionTicket) -> IngestExecutionStatus | None:
        """Resolve absence only after any uncertain prepare transaction has settled.

        An unlocked read can miss PREPARED while its original COMMIT is in flight.
        The shared admission lock is held through that commit. Read again after
        acquiring it, under READ COMMITTED, before acknowledging NOT_STARTED.
        """
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(IngestExecutionRow, ticket.execution_id)
            if row is None:
                return None
            current = ingest_status(row)
            if current.ticket != ticket:
                raise ResourceAdmissionError("ingest_execution_stale")
            return current

    def confirm(
        self, ticket: IngestExecutionTicket, proof: ProcessExitEvidence
    ) -> IngestExecutionStatus:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = self._locked(session, ticket)
            current = ingest_status(row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state not in {ExecutionState.PREPARED, ExecutionState.CLOSED}
            ):
                raise ResourceAdmissionError("ingest_execution_stale")
            expected = (proof.kind, proof.evidence_sha256, proof.exit_code)
            if current.state == ExecutionState.CLOSED:
                if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != expected:
                    raise ResourceAdmissionError("ingest_execution_conflict")
                return current
            row.state, row.closed_at = "CLOSED", self._now(session)
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = expected
            session.flush()
            return ingest_status(row)
