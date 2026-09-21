"""Metadata receipts retain internal capacity and provider occupancy until exact exit."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.job_worker import JobLeaseLost, JobResourceWait
from autplay.domain.ingest_execution import INGEST_IO_TTL, IngestExecutionStatus
from autplay.domain.jobs import JobAttemptOutcome, JobState, LeaseFence
from autplay.domain.metadata_execution import MetadataAudioTarget, MetadataExecutionTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VerifiedStagedFile

from .internal_io import internal_io_policy, internal_io_wait_required, require_internal_io_capacity
from .jobs_runtime import PostgresJobRepository
from .metadata_authority import lock_metadata_job, metadata_audio
from .models.jobs import JobRow
from .models.metadata_execution import MetadataExecutionRow, MetadataProviderGateRow
from .resource_limits import lock_resource_admission

type MetadataStatus = IngestExecutionStatus[MetadataExecutionTicket]


def audio_document(audio: MetadataAudioTarget | None) -> dict[str, object] | None:
    if audio is None:
        return None
    return {
        "recording_id": str(audio.recording_id),
        "audio_variant_id": str(audio.audio_variant_id),
        "vault_object_id": str(audio.vault_object_id),
        "storage_key": audio.storage_key.value,
        "byte_size": audio.expected.byte_size,
        "sha256": audio.expected.sha256.hex,
    }


def _status(row: MetadataExecutionRow) -> MetadataStatus:
    audio = row.audio
    target = (
        None
        if audio is None
        else MetadataAudioTarget(
            UUID(audio["recording_id"]),
            UUID(audio["audio_variant_id"]),
            UUID(audio["vault_object_id"]),
            OpaqueStorageKey(audio["storage_key"]),
            VerifiedStagedFile(audio["byte_size"], Sha256Digest(bytes.fromhex(audio["sha256"]))),
        )
    )
    child = (
        None
        if row.child_pid is None or row.child_identity_sha256 is None
        else ProcessIdentity(
            row.child_pid,
            row.child_identity_sha256,
        )
    )
    return IngestExecutionStatus(
        MetadataExecutionTicket(
            row.execution_id,
            row.owner_run_id,
            row.user_id,
            row.user_track_ref_id,
            row.generation,
            row.authority_generation,
            LeaseFence(row.job_id, row.worker_id, row.attempt_no),
            target,
        ),
        ExecutionState(row.state),
        child,
        row.io_deadline_at,
        row.heartbeat_at,
    )


def _now(session: Session) -> datetime:
    with session.no_autoflush:
        value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise ResourceAdmissionError("metadata_execution_unavailable")
    return value


def _locked(session: Session, ticket: MetadataExecutionTicket) -> MetadataExecutionRow:
    row = session.get(
        MetadataExecutionRow,
        ticket.execution_id,
        with_for_update=True,
        populate_existing=True,
    )
    if row is None or _status(row).ticket != ticket:
        raise ResourceAdmissionError("metadata_execution_stale")
    return row


def _authority(session: Session, ticket: MetadataExecutionTicket) -> JobRow:
    ref, _, job = lock_metadata_job(
        session, ticket.user_track_ref_id, ticket.generation, ticket.fence
    )
    if (
        ref.user_id != ticket.user_id
        or not isinstance(job.payload, dict)
        or job.payload["authority_generation"] != ticket.authority_generation
        or (ticket.audio is not None and metadata_audio(session, ref) != ticket.audio)
    ):
        raise JobLeaseLost
    return job


def require_metadata_execution(session: Session, expected: MetadataStatus) -> MetadataExecutionRow:
    _authority(session, expected.ticket)
    row = _locked(session, expected.ticket)
    if (
        row.state != "RUNNING"
        or expected.child is None
        or _status(row).child != expected.child
        or row.io_deadline_at is None
        or row.io_deadline_at <= _now(session)
    ):
        raise ResourceAdmissionError("metadata_execution_stale")
    return row


class PostgresMetadataExecutionRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def plan(
        self, ref_id: UUID, generation: int, fence: LeaseFence, owner: UUID
    ) -> MetadataExecutionTicket:
        with self._sessions.begin() as session:
            ref, _, job = lock_metadata_job(session, ref_id, generation, fence)
            assert isinstance(job.payload, dict)
            authority = job.payload["authority_generation"]
            assert type(authority) is int
            return MetadataExecutionTicket(
                uuid4(),
                owner,
                ref.user_id,
                ref_id,
                generation,
                authority,
                fence,
                metadata_audio(session, ref),
            )

    def prepare(self, ticket: MetadataExecutionTicket) -> MetadataStatus:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            old = session.get(MetadataExecutionRow, ticket.execution_id)
            if old is not None:
                if _status(old).ticket != ticket:
                    raise ResourceAdmissionError("metadata_execution_stale")
                return _status(old)
            _authority(session, ticket)
            if (
                session.scalar(
                    select(MetadataExecutionRow.execution_id).where(
                        MetadataExecutionRow.user_track_ref_id == ticket.user_track_ref_id,
                        MetadataExecutionRow.closed_at.is_(None),
                    )
                )
                is not None
            ):
                raise ResourceAdmissionError("metadata_execution_busy")
            if internal_io_policy(session).workload_version < 2:
                raise ResourceAdmissionError("metadata_budget_unconfigured")
            require_internal_io_capacity(session)
            row = MetadataExecutionRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                user_id=ticket.user_id,
                user_track_ref_id=ticket.user_track_ref_id,
                generation=ticket.generation,
                authority_generation=ticket.authority_generation,
                job_id=ticket.fence.job_id,
                worker_id=ticket.fence.worker_id,
                attempt_no=ticket.fence.attempt_no,
                audio=audio_document(ticket.audio),
                state="PREPARED",
                created_at=_now(session),
            )
            session.add(row)
            session.flush()
            return _status(row)

    def _refresh(self, session: Session, row: MetadataExecutionRow, job: JobRow) -> MetadataStatus:
        assert job.lease_deadline is not None
        now = _now(session)
        if job.lease_deadline <= now or (
            row.io_deadline_at is not None and row.io_deadline_at <= now
        ):
            raise ResourceAdmissionError("metadata_execution_stale")
        row.heartbeat_at, row.io_deadline_at = now, min(now + INGEST_IO_TTL, job.lease_deadline)
        session.flush()
        return _status(row)

    def start(self, ticket: MetadataExecutionTicket, child: ProcessIdentity) -> MetadataStatus:
        with self._sessions.begin() as session:
            job = _authority(session, ticket)
            row = _locked(session, ticket)
            if row.state == "PREPARED":
                row.state, row.started_at = "RUNNING", _now(session)
                row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
            elif row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("metadata_execution_stale")
            return self._refresh(session, row, job)

    def renew(self, ticket: MetadataExecutionTicket, child: ProcessIdentity) -> MetadataStatus:
        with self._sessions.begin() as session:
            job = _authority(session, ticket)
            row = _locked(session, ticket)
            if row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("metadata_execution_stale")
            return self._refresh(session, row, job)

    def _inspect(self, session: Session, ticket: MetadataExecutionTicket) -> MetadataStatus | None:
        row = session.get(MetadataExecutionRow, ticket.execution_id)
        if row is None:
            return None
        if _status(row).ticket != ticket:
            raise ResourceAdmissionError("metadata_execution_stale")
        return _status(row)

    def status(self, ticket: MetadataExecutionTicket) -> MetadataStatus | None:
        with self._sessions() as session:
            return self._inspect(session, ticket)

    def reconcile(self, ticket: MetadataExecutionTicket) -> MetadataStatus | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            return self._inspect(session, ticket)

    def confirm(
        self, ticket: MetadataExecutionTicket, proof: ProcessExitEvidence
    ) -> MetadataStatus:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = _locked(session, ticket)
            current = _status(row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state not in {ExecutionState.PREPARED, ExecutionState.CLOSED}
            ):
                raise ResourceAdmissionError("metadata_execution_stale")
            evidence = proof.kind, proof.evidence_sha256, proof.exit_code
            if current.state == ExecutionState.CLOSED:
                if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != evidence:
                    raise ResourceAdmissionError("metadata_execution_conflict")
                return current
            now = _now(session)
            row.state, row.closed_at = "CLOSED", now
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = evidence
            gate = session.get(MetadataProviderGateRow, 1, with_for_update=True)
            if gate is not None and gate.execution_id == ticket.execution_id:
                gate.execution_id = gate.request_id = None
                gate.next_request_at = now + timedelta(milliseconds=1100)
            session.flush()
            return _status(row)

    def provider_begin(self, running: MetadataStatus, request_id: UUID) -> bool:
        with self._sessions.begin() as session:
            require_metadata_execution(session, running)
            gate = session.get(
                MetadataProviderGateRow, 1, with_for_update=True, populate_existing=True
            )
            if gate is None:
                raise ResourceAdmissionError("metadata_provider_gate_unavailable")
            if gate.execution_id is not None:
                return (gate.execution_id, gate.request_id) == (
                    running.ticket.execution_id,
                    request_id,
                )
            if gate.next_request_at > _now(session):
                return False
            gate.execution_id, gate.request_id = running.ticket.execution_id, request_id
            return True

    def provider_end(self, running: MetadataStatus, request_id: UUID) -> None:
        with self._sessions.begin() as session:
            require_metadata_execution(session, running)
            gate = session.get(
                MetadataProviderGateRow, 1, with_for_update=True, populate_existing=True
            )
            if gate is None or (gate.execution_id, gate.request_id) != (
                running.ticket.execution_id,
                request_id,
            ):
                raise ResourceAdmissionError("metadata_provider_gate_stale")
            gate.execution_id = gate.request_id = None
            gate.next_request_at = _now(session) + timedelta(milliseconds=1100)

    def defer(self, ticket: MetadataExecutionTicket) -> None:
        deferred = False
        with self._sessions.begin() as session:
            _, _, job = lock_metadata_job(
                session, ticket.user_track_ref_id, ticket.generation, ticket.fence
            )
            deferred = (
                internal_io_wait_required(session)
                or internal_io_policy(session).workload_version < 2
            )
            deferred = (
                deferred
                or session.scalar(
                    select(MetadataExecutionRow.execution_id)
                    .where(
                        MetadataExecutionRow.user_track_ref_id == ticket.user_track_ref_id,
                        MetadataExecutionRow.closed_at.is_(None),
                    )
                    .limit(1)
                )
                is not None
            )
            if deferred:
                PostgresJobRepository(session)._finish(
                    ticket.fence,
                    outcome=JobAttemptOutcome.RESOURCE_WAIT,
                    state=JobState.RETRY_WAIT,
                    error=None,
                    retry_delay=timedelta(seconds=2),
                )
                session.refresh(job)
                job.resource_wait_count += 1
                job.resource_waiting = True
        if deferred:
            raise JobResourceWait()
