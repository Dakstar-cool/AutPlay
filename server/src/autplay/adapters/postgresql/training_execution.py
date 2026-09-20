"""Retain global capacity until exact shared-training process-tree exit."""

import re
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.training_work import TrainingWorkService
from autplay.domain.ingest_execution import INGEST_IO_TTL, IngestExecutionStatus
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.training_execution import (
    TrainingExecutionCleanupPlan,
    TrainingExecutionTicket,
)
from autplay.domain.vault import Sha256Digest

from .internal_io import internal_io_policy, require_internal_io_capacity
from .models.training_work import TrainingExecutionRow, TrainingRunRow
from .resource_limits import lock_resource_admission

type TrainingExecutionStatus = IngestExecutionStatus[TrainingExecutionTicket]


def _status(row: TrainingExecutionRow) -> TrainingExecutionStatus:
    child = (
        None
        if row.child_pid is None or row.child_identity_sha256 is None
        else ProcessIdentity(row.child_pid, row.child_identity_sha256)
    )
    return IngestExecutionStatus(
        TrainingExecutionTicket(
            row.execution_id,
            row.run_id,
            row.input_root,
            row.output_root,
            row.input_root_device,
            row.input_root_inode,
            row.input_scope_device,
            row.input_scope_inode,
            row.output_scope_device,
            row.output_scope_inode,
            Sha256Digest(row.input_inventory_sha256),
            Sha256Digest(row.root_inventory_sha256),
            row.input_bytes,
            row.maximum_output_bytes,
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
        raise ResourceAdmissionError("training_execution_unavailable")
    return value


def _cleanup_plan(row: TrainingExecutionRow) -> TrainingExecutionCleanupPlan:
    if row.state not in {"STOPPING", "CLOSED"}:
        raise ResourceAdmissionError("training_execution_stale")
    return TrainingExecutionCleanupPlan(
        _status(row).ticket,
        row.checkpoint_device,
        row.checkpoint_inode,
        row.retain_checkpoint,
        (None if row.checkpoint_manifest_sha256 is None else row.checkpoint_manifest_sha256.hex()),
        (None if row.checkpoint_weights_sha256 is None else row.checkpoint_weights_sha256.hex()),
        row.checkpoint_optimizer_steps,
        row.checkpoint_device_type,
        (None if row.publication_seal_sha256 is None else row.publication_seal_sha256.hex()),
    )


class PostgresTrainingExecutionRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        authority: TrainingWorkService | None = None,
    ) -> None:
        self._sessions, self._authority = sessions, authority

    def _current_authority(self) -> TrainingWorkService:
        if self._authority is None:
            raise ResourceAdmissionError("training_execution_authority_unavailable")
        return self._authority

    @staticmethod
    def _locked(session: Session, ticket: TrainingExecutionTicket) -> TrainingExecutionRow:
        row = session.get(
            TrainingExecutionRow,
            ticket.execution_id,
            with_for_update=True,
            populate_existing=True,
        )
        if row is None or _status(row).ticket != ticket:
            raise ResourceAdmissionError("training_execution_stale")
        return row

    def prepare(self, ticket: TrainingExecutionTicket) -> TrainingExecutionStatus:
        with self._sessions.begin() as session:
            existing = session.get(TrainingExecutionRow, ticket.execution_id)
            if existing is not None:
                if _status(existing).ticket != ticket:
                    raise ResourceAdmissionError("training_execution_stale")
                return _status(existing)
            authority = self._current_authority()
            authority.lock_execution_identity(session)
            lock_resource_admission(session)
            # A concurrent prepare may have committed while this transaction waited
            # for the shared admission lock. Read Committed gives this statement the
            # new snapshot; replay the exact row instead of racing its primary key.
            existing = session.get(
                TrainingExecutionRow,
                ticket.execution_id,
                populate_existing=True,
            )
            if existing is not None:
                if _status(existing).ticket != ticket:
                    raise ResourceAdmissionError("training_execution_stale")
                return _status(existing)
            policy = internal_io_policy(session)
            if (
                policy.workload_version < 3
                or (policy.server_instance_id, policy.identity_epoch)
                != authority.execution_identity
            ):
                raise ResourceAdmissionError("training_budget_unconfigured")
            authority.lock_execution_authority(session, ticket.run_id, running=False)
            require_internal_io_capacity(session)
            row = TrainingExecutionRow(
                execution_id=ticket.execution_id,
                run_id=ticket.run_id,
                input_root=ticket.input_root,
                output_root=ticket.output_root,
                input_root_device=ticket.input_root_device,
                input_root_inode=ticket.input_root_inode,
                input_scope_device=ticket.input_scope_device,
                input_scope_inode=ticket.input_scope_inode,
                output_scope_device=ticket.output_scope_device,
                output_scope_inode=ticket.output_scope_inode,
                input_inventory_sha256=ticket.input_inventory_sha256.value,
                root_inventory_sha256=ticket.root_inventory_sha256.value,
                input_bytes=ticket.input_bytes,
                maximum_output_bytes=ticket.maximum_output_bytes,
                state="PREPARED",
                created_at=_now(session),
            )
            session.add(row)
            session.flush()
            return _status(row)

    def _refresh(self, session: Session, row: TrainingExecutionRow) -> TrainingExecutionStatus:
        now = _now(session)
        if row.io_deadline_at is not None and row.io_deadline_at <= now:
            raise ResourceAdmissionError("training_execution_stale")
        row.heartbeat_at, row.io_deadline_at = now, now + INGEST_IO_TTL
        session.flush()
        return _status(row)

    def start(
        self, ticket: TrainingExecutionTicket, child: ProcessIdentity
    ) -> TrainingExecutionStatus:
        with self._sessions.begin() as session:
            self._current_authority().lock_execution_authority(
                session, ticket.run_id, running=False
            )
            row = self._locked(session, ticket)
            if row.state == "PREPARED":
                row.state, row.started_at = "RUNNING", _now(session)
                row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
            elif row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("training_execution_stale")
            return self._refresh(session, row)

    def renew(
        self, ticket: TrainingExecutionTicket, child: ProcessIdentity
    ) -> TrainingExecutionStatus:
        with self._sessions.begin() as session:
            # The fixed child may spend several grants checking the payload-free root
            # inventory before its first input-binding RPC transitions READY to RUNNING.
            self._current_authority().lock_execution_authority(
                session, ticket.run_id, running=False
            )
            row = self._locked(session, ticket)
            if row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("training_execution_stale")
            return self._refresh(session, row)

    def bind_checkpoint(
        self,
        ticket: TrainingExecutionTicket,
        child: ProcessIdentity,
        *,
        device: str,
        inode: str,
    ) -> TrainingExecutionStatus:
        if not device.isdecimal() or not inode.isdecimal() or len(device) > 32 or len(inode) > 32:
            raise ValueError("invalid training checkpoint identity")
        with self._sessions.begin() as session:
            self._current_authority().lock_execution_authority(
                session, ticket.run_id, running=False
            )
            row = self._locked(session, ticket)
            if row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("training_execution_stale")
            if row.checkpoint_device is None and row.checkpoint_inode is None:
                row.checkpoint_device, row.checkpoint_inode = device, inode
            elif (row.checkpoint_device, row.checkpoint_inode) != (device, inode):
                raise ResourceAdmissionError("training_execution_conflict")
            return self._refresh(session, row)

    def status(self, ticket: TrainingExecutionTicket) -> TrainingExecutionStatus | None:
        with self._sessions() as session:
            row = session.get(TrainingExecutionRow, ticket.execution_id)
            if row is None:
                return None
            if _status(row).ticket != ticket:
                raise ResourceAdmissionError("training_execution_stale")
            return _status(row)

    def reconcile(self, ticket: TrainingExecutionTicket) -> TrainingExecutionStatus | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(TrainingExecutionRow, ticket.execution_id)
            if row is None:
                return None
            if _status(row).ticket != ticket:
                raise ResourceAdmissionError("training_execution_stale")
            return _status(row)

    def cleanup_plan(self, ticket: TrainingExecutionTicket) -> TrainingExecutionCleanupPlan | None:
        with self._sessions() as session:
            row = session.get(TrainingExecutionRow, ticket.execution_id)
            if row is None:
                return None
            if _status(row).ticket != ticket:
                raise ResourceAdmissionError("training_execution_stale")
            if row.state not in {"STOPPING", "CLOSED"}:
                return None
            return _cleanup_plan(row)

    def pending_cleanups(self, *, limit: int = 100) -> tuple[TrainingExecutionCleanupPlan, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid training cleanup limit")
        with self._sessions() as session:
            rows = session.scalars(
                select(TrainingExecutionRow)
                .where(TrainingExecutionRow.state == "STOPPING")
                .order_by(
                    TrainingExecutionRow.cleanup_started_at,
                    TrainingExecutionRow.execution_id,
                )
                .limit(limit)
            )
            return tuple(_cleanup_plan(row) for row in rows)

    def begin_cleanup(
        self,
        ticket: TrainingExecutionTicket,
        proof: ProcessExitEvidence,
        *,
        retain_checkpoint: bool | None,
        checkpoint_manifest_sha256: str | None,
        checkpoint_weights_sha256: str | None = None,
        checkpoint_optimizer_steps: int | None = None,
        checkpoint_device_type: str | None = None,
        publication_seal_sha256: str | None = None,
    ) -> TrainingExecutionStatus:
        candidate_values = (
            checkpoint_manifest_sha256,
            checkpoint_weights_sha256,
            checkpoint_optimizer_steps,
            checkpoint_device_type,
        )
        candidate = any(value is not None for value in candidate_values)
        if (
            retain_checkpoint not in {None, False, True}
            or candidate != all(value is not None for value in candidate_values)
            or (
                candidate
                and (
                    re.fullmatch(r"[0-9a-f]{64}", str(checkpoint_manifest_sha256)) is None
                    or re.fullmatch(r"[0-9a-f]{64}", str(checkpoint_weights_sha256)) is None
                    or type(checkpoint_optimizer_steps) is not int
                    or checkpoint_optimizer_steps < 1
                    or checkpoint_device_type not in {"cpu", "cuda"}
                )
            )
            or (retain_checkpoint in {None, True} and not candidate)
            or (
                publication_seal_sha256 is not None
                and (
                    not candidate or re.fullmatch(r"[0-9a-f]{64}", publication_seal_sha256) is None
                )
            )
        ):
            raise ValueError("invalid training checkpoint cleanup result")
        manifest = (
            None
            if checkpoint_manifest_sha256 is None
            else bytes.fromhex(checkpoint_manifest_sha256)
        )
        weights = (
            None if checkpoint_weights_sha256 is None else bytes.fromhex(checkpoint_weights_sha256)
        )
        publication_seal = (
            None if publication_seal_sha256 is None else bytes.fromhex(publication_seal_sha256)
        )
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            session.get(
                TrainingRunRow,
                ticket.run_id,
                with_for_update=True,
                populate_existing=True,
            )
            row = self._locked(session, ticket)
            current = _status(row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state
                not in {ExecutionState.PREPARED, ExecutionState.STOPPING, ExecutionState.CLOSED}
            ):
                raise ResourceAdmissionError("training_execution_stale")
            evidence = proof.kind, proof.evidence_sha256, proof.exit_code
            if current.state in {ExecutionState.STOPPING, ExecutionState.CLOSED}:
                if (
                    row.closure_kind,
                    row.closure_evidence_sha256,
                    row.exit_code,
                    row.retain_checkpoint,
                    row.checkpoint_manifest_sha256,
                    row.checkpoint_weights_sha256,
                    row.checkpoint_optimizer_steps,
                    row.checkpoint_device_type,
                    row.publication_seal_sha256,
                ) != (
                    *evidence,
                    retain_checkpoint,
                    manifest,
                    weights,
                    checkpoint_optimizer_steps,
                    checkpoint_device_type,
                    publication_seal,
                ):
                    raise ResourceAdmissionError("training_execution_conflict")
                return current
            if retain_checkpoint and (
                row.checkpoint_device is None or row.checkpoint_inode is None
            ):
                raise ResourceAdmissionError("training_execution_stale")
            session.execute(
                text(
                    "SELECT app_private.authorize_training_execution_transition("
                    ":execution,:expected,'STOPPING')"
                ),
                {
                    "execution": ticket.execution_id,
                    "expected": current.state.value,
                },
            )
            row.state, row.cleanup_started_at = "STOPPING", _now(session)
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = evidence
            row.retain_checkpoint = retain_checkpoint
            row.checkpoint_manifest_sha256 = manifest
            row.checkpoint_weights_sha256 = weights
            row.checkpoint_optimizer_steps = checkpoint_optimizer_steps
            row.checkpoint_device_type = checkpoint_device_type
            row.publication_seal_sha256 = publication_seal
            session.flush()
            return _status(row)

    def decide_checkpoint(
        self,
        ticket: TrainingExecutionTicket,
        *,
        retain_checkpoint: bool,
    ) -> TrainingExecutionCleanupPlan:
        if type(retain_checkpoint) is not bool:
            raise ValueError("invalid training checkpoint cleanup decision")
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            session.get(
                TrainingRunRow,
                ticket.run_id,
                with_for_update=True,
                populate_existing=True,
            )
            row = self._locked(session, ticket)
            if row.state != "STOPPING":
                raise ResourceAdmissionError("training_execution_stale")
            if row.retain_checkpoint is not None:
                if row.retain_checkpoint != retain_checkpoint:
                    raise ResourceAdmissionError("training_execution_conflict")
                return _cleanup_plan(row)
            if retain_checkpoint and row.checkpoint_manifest_sha256 is None:
                raise ResourceAdmissionError("training_execution_stale")
            session.execute(
                text(
                    "SELECT app_private.authorize_training_execution_transition("
                    ":execution,'STOPPING','STOPPING')"
                ),
                {"execution": ticket.execution_id},
            )
            row.retain_checkpoint = retain_checkpoint
            session.flush()
            return _cleanup_plan(row)

    def confirm(
        self,
        ticket: TrainingExecutionTicket,
        input_cleanup_sha256: str,
    ) -> TrainingExecutionStatus:
        if re.fullmatch(r"[0-9a-f]{64}", input_cleanup_sha256) is None:
            raise ValueError("invalid training input cleanup evidence")
        cleanup = bytes.fromhex(input_cleanup_sha256)
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            session.get(
                TrainingRunRow,
                ticket.run_id,
                with_for_update=True,
                populate_existing=True,
            )
            row = self._locked(session, ticket)
            current = _status(row)
            if current.state == ExecutionState.CLOSED:
                if row.input_cleanup_sha256 != cleanup:
                    raise ResourceAdmissionError("training_execution_conflict")
                return current
            if current.state != ExecutionState.STOPPING:
                raise ResourceAdmissionError("training_execution_stale")
            if row.retain_checkpoint is None:
                raise ResourceAdmissionError("training_execution_stale")
            session.execute(
                text(
                    "SELECT app_private.authorize_training_execution_transition("
                    ":execution,'STOPPING','CLOSED')"
                ),
                {"execution": ticket.execution_id},
            )
            now = _now(session)
            row.state, row.closed_at, row.input_cleaned_at = "CLOSED", now, now
            row.input_cleanup_sha256 = cleanup
            session.flush()
            session.execute(
                text(
                    "UPDATE ml.training_cleanup_claim SET phase='COMPLETE',"
                    "completed_at=clock_timestamp() WHERE run_id=:run AND phase='PENDING' "
                    "AND EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=:run) "
                    "AND NOT EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=:run "
                    "AND (state<>'CLOSED' OR input_cleanup_sha256 IS NULL))"
                ),
                {"run": ticket.run_id},
            )
            return _status(row)


__all__ = ["PostgresTrainingExecutionRepository", "TrainingExecutionStatus"]
