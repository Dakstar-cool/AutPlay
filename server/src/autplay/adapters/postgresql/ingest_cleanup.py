"""Claim-bound cleanup never borrows expired job or source authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.ingest_cleanup import IngestCleanupClaim, IngestCleanupTicket
from autplay.domain.ingest_execution import INGEST_IO_TTL, IngestExecutionStatus
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VerifiedStagedFile

from .internal_io import require_internal_io_capacity
from .models.ingest_cleanup import IngestCleanupClaimRow, IngestCleanupExecutionRow
from .models.ingest_execution import IngestExecutionRow
from .models.vault import UploadSessionRow, VaultObjectRow, VaultReplicaRow
from .resource_limits import lock_resource_admission
from .resource_upload_guard import upload_has_unclosed_writer


def cleanup_claim(row: IngestCleanupClaimRow) -> IngestCleanupClaim:
    return IngestCleanupClaim(
        row.claim_id,
        OpaqueStorageKey(row.staging_key),
        VerifiedStagedFile(row.expected_size, Sha256Digest(row.sha256)),
        row.completed_execution_id,
    )


class PostgresIngestCleanupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("ingest_cleanup_unavailable")
        return now

    @staticmethod
    def _status(
        session: Session, row: IngestCleanupExecutionRow
    ) -> IngestExecutionStatus[IngestCleanupTicket]:
        claim = session.get(IngestCleanupClaimRow, row.claim_id)
        if claim is None:
            raise ResourceAdmissionError("ingest_cleanup_stale")
        target = cleanup_claim(claim)
        child = None
        if row.child_pid is not None and row.child_identity_sha256 is not None:
            child = ProcessIdentity(row.child_pid, row.child_identity_sha256)
        return IngestExecutionStatus(
            IngestCleanupTicket(
                row.execution_id,
                row.owner_run_id,
                row.claim_id,
                target.staging_key,
                target.expected,
            ),
            ExecutionState(row.state),
            child,
            row.io_deadline_at,
            row.heartbeat_at,
        )

    def get(self, claim_id: UUID) -> IngestCleanupClaim | None:
        with self._sessions() as session:
            row = session.get(IngestCleanupClaimRow, claim_id)
            return None if row is None else cleanup_claim(row)

    def pending(self, *, maximum: int = 100, after: UUID | None = None) -> tuple[UUID, ...]:
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ValueError("ingest_cleanup_limit_invalid")
        statement = select(IngestCleanupClaimRow.claim_id).where(
            IngestCleanupClaimRow.completed_at.is_(None)
        )
        if after is not None:
            statement = statement.where(IngestCleanupClaimRow.claim_id > after)
        with self._sessions() as session:
            return tuple(
                session.scalars(statement.order_by(IngestCleanupClaimRow.claim_id).limit(maximum))
            )

    def _target(self, session: Session, ticket: IngestCleanupTicket) -> IngestCleanupClaimRow:
        upload = session.get(
            UploadSessionRow, ticket.claim_id, with_for_update=True, populate_existing=True
        )
        claim = session.get(
            IngestCleanupClaimRow, ticket.claim_id, with_for_update=True, populate_existing=True
        )
        if (
            upload is None
            or claim is None
            or claim.completed_at is not None
            or cleanup_claim(claim)
            != IngestCleanupClaim(ticket.claim_id, ticket.staging_key, ticket.expected)
            or (
                upload.state,
                upload.staging_key,
                upload.expected_size,
                upload.computed_sha256,
                upload.vault_object_id,
                upload.audio_variant_id,
            )
            != (
                claim.final_state,
                claim.staging_key,
                claim.expected_size,
                claim.sha256,
                claim.vault_object_id,
                claim.audio_variant_id,
            )
        ):
            raise ResourceAdmissionError("ingest_cleanup_stale")
        work = session.get(IngestExecutionRow, claim.work_execution_id)
        if (
            work is None
            or work.state != "CLOSED"
            or work.closure_kind != "PROCESS_EXIT"
            or work.child_pid is None
            or work.closed_at is None
            or upload_has_unclosed_writer(
                session, ticket.claim_id, exclude_cleanup=ticket.execution_id
            )
        ):
            raise ResourceAdmissionError("ingest_cleanup_busy")
        obj = session.get(VaultObjectRow, claim.vault_object_id, populate_existing=True)
        replica = session.scalar(
            select(VaultReplicaRow)
            .where(
                VaultReplicaRow.vault_object_id == claim.vault_object_id,
                VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                VaultReplicaRow.storage_key == claim.sha256.hex(),
            )
            .execution_options(populate_existing=True)
        )
        if (
            obj is None
            or (obj.commit_status, obj.sha256, obj.byte_size)
            != ("COMMITTED", claim.sha256, claim.expected_size)
            or replica is None
            or replica.replica_status != "AVAILABLE"
            or replica.verified_at is None
        ):
            raise ResourceAdmissionError("ingest_cleanup_cas_unavailable")
        return claim

    def _locked(self, session: Session, ticket: IngestCleanupTicket) -> IngestCleanupExecutionRow:
        row = session.get(
            IngestCleanupExecutionRow,
            ticket.execution_id,
            with_for_update=True,
            populate_existing=True,
        )
        if row is None or self._status(session, row).ticket != ticket:
            raise ResourceAdmissionError("ingest_cleanup_stale")
        return row

    def prepare(self, ticket: IngestCleanupTicket) -> IngestExecutionStatus[IngestCleanupTicket]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(IngestCleanupExecutionRow, ticket.execution_id)
            if row is not None:
                current = self._status(session, row)
                if current.ticket != ticket:
                    raise ResourceAdmissionError("ingest_cleanup_stale")
                return current
            self._target(session, ticket)
            require_internal_io_capacity(session)
            row = IngestCleanupExecutionRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                claim_id=ticket.claim_id,
                state="PREPARED",
                created_at=self._now(session),
            )
            session.add(row)
            session.flush()
            return self._status(session, row)

    def _refresh(
        self, session: Session, row: IngestCleanupExecutionRow
    ) -> IngestExecutionStatus[IngestCleanupTicket]:
        with session.no_autoflush:
            now = self._now(session)
        if row.io_deadline_at is not None and row.io_deadline_at <= now:
            raise ResourceAdmissionError("ingest_cleanup_stale")
        row.heartbeat_at, row.io_deadline_at = now, now + INGEST_IO_TTL
        try:
            session.flush()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "55000":
                raise ResourceAdmissionError("ingest_cleanup_stale") from error
            raise
        return self._status(session, row)

    def start(
        self, ticket: IngestCleanupTicket, child: ProcessIdentity
    ) -> IngestExecutionStatus[IngestCleanupTicket]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            self._target(session, ticket)
            row = self._locked(session, ticket)
            if row.state == "PREPARED":
                row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
                row.state, row.started_at = "RUNNING", self._now(session)
            elif row.state != "RUNNING" or self._status(session, row).child != child:
                raise ResourceAdmissionError("ingest_cleanup_stale")
            return self._refresh(session, row)

    def renew(
        self, ticket: IngestCleanupTicket, child: ProcessIdentity
    ) -> IngestExecutionStatus[IngestCleanupTicket]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            self._target(session, ticket)
            row = self._locked(session, ticket)
            if row.state != "RUNNING" or self._status(session, row).child != child:
                raise ResourceAdmissionError("ingest_cleanup_stale")
            return self._refresh(session, row)

    def status(
        self, ticket: IngestCleanupTicket
    ) -> IngestExecutionStatus[IngestCleanupTicket] | None:
        with self._sessions() as session:
            return self._inspect(session, ticket)

    def reconcile(
        self, ticket: IngestCleanupTicket
    ) -> IngestExecutionStatus[IngestCleanupTicket] | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            return self._inspect(session, ticket)

    def _inspect(
        self, session: Session, ticket: IngestCleanupTicket
    ) -> IngestExecutionStatus[IngestCleanupTicket] | None:
        row = session.get(IngestCleanupExecutionRow, ticket.execution_id)
        if row is None:
            return None
        current = self._status(session, row)
        if current.ticket != ticket:
            raise ResourceAdmissionError("ingest_cleanup_stale")
        return current

    def confirm(
        self, ticket: IngestCleanupTicket, proof: ProcessExitEvidence
    ) -> IngestExecutionStatus[IngestCleanupTicket]:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = self._locked(session, ticket)
            current = self._status(session, row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state not in {ExecutionState.PREPARED, ExecutionState.CLOSED}
            ):
                raise ResourceAdmissionError("ingest_cleanup_stale")
            expected = (proof.kind, proof.evidence_sha256, proof.exit_code)
            if current.state == ExecutionState.CLOSED:
                if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != expected:
                    raise ResourceAdmissionError("ingest_cleanup_conflict")
                return current
            row.state, row.closed_at = "CLOSED", self._now(session)
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = expected
            session.flush()
            return self._status(session, row)

    def complete(self, claim: IngestCleanupClaim, execution_id: UUID) -> None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            session.get(UploadSessionRow, claim.claim_id, with_for_update=True)
            row = session.get(IngestCleanupClaimRow, claim.claim_id, with_for_update=True)
            if row is None:
                raise ResourceAdmissionError("ingest_cleanup_stale")
            expected = IngestCleanupClaim(
                claim.claim_id, claim.staging_key, claim.expected, row.completed_execution_id
            )
            if cleanup_claim(row) != expected:
                raise ResourceAdmissionError("ingest_cleanup_stale")
            if row.completed_at is not None:
                if row.completed_execution_id != execution_id:
                    raise ResourceAdmissionError("ingest_cleanup_conflict")
                return
            run = session.get(IngestCleanupExecutionRow, execution_id)
            if (
                run is None
                or run.claim_id != claim.claim_id
                or run.state != "CLOSED"
                or run.closure_kind != "PROCESS_EXIT"
                or run.exit_code != 0
            ):
                raise ResourceAdmissionError("ingest_cleanup_unconfirmed")
            self._target(session, self._status(session, run).ticket)
            row.completed_at, row.completed_execution_id = self._now(session), execution_id
