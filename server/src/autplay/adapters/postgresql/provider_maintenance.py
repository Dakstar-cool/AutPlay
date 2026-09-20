"""Short maintenance transactions; no TTL or connection loss proves child exit."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.provider_cleanup import ProviderCleanupClaim
from autplay.application.provider_scratch import ProviderScratchClaim
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest

from .internal_io import require_internal_io_capacity
from .models.orphan_object_claim import OrphanObjectClaimRow
from .models.provider_maintenance import ProviderMaintenanceRow
from .models.provider_staging import ProviderStagingRow
from .orphan_object_retirement import lock_cas_digest, object_key_is_registered
from .resource_limits import lock_resource_admission
from .upload_cleanup import require_cleanup_target


class PostgresProviderMaintenanceRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("maintenance_unavailable")
        return now

    @staticmethod
    def _status(row: ProviderMaintenanceRow) -> MaintenanceStatus:
        child = None
        if row.child_pid is not None and row.child_identity_sha256 is not None:
            child = ProcessIdentity(row.child_pid, row.child_identity_sha256)
        return MaintenanceStatus(
            MaintenanceTicket(
                row.execution_id,
                row.owner_run_id,
                row.provider_execution_id,
                row.claim_id,
                MaintenanceAction(row.action),
                OpaqueStorageKey(row.storage_key) if row.storage_key is not None else None,
            ),
            ExecutionState(row.state),
            child,
        )

    def _locked(self, session: Session, ticket: MaintenanceTicket) -> ProviderMaintenanceRow:
        lock_resource_admission(session)
        row = session.get(ProviderMaintenanceRow, ticket.execution_id, with_for_update=True)
        if row is None or self._status(row).ticket != ticket:
            raise ResourceAdmissionError("maintenance_execution_stale")
        return row

    def prepare(self, ticket: MaintenanceTicket) -> MaintenanceStatus:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            existing = session.get(ProviderMaintenanceRow, ticket.execution_id)
            if existing is not None:
                if self._status(existing).ticket != ticket:
                    raise ResourceAdmissionError("maintenance_execution_stale")
                return self._status(existing)
            if (
                session.scalar(
                    select(ProviderMaintenanceRow.execution_id)
                    .where(ProviderMaintenanceRow.closed_at.is_(None))
                    .limit(1)
                )
                is not None
            ):
                raise ResourceAdmissionError("maintenance_busy")
            if ticket.action == MaintenanceAction.INVENTORY:
                eligible = True
            elif ticket.action == MaintenanceAction.UPLOAD_CLEANUP:
                assert ticket.storage_key is not None
                cleanup = require_cleanup_target(session, ticket.claim_id, ticket.storage_key.value)
                eligible = cleanup.completed_at is None
            elif ticket.action in (
                MaintenanceAction.ORPHAN_OBJECT,
                MaintenanceAction.ORPHAN_MISSING,
            ):
                assert ticket.storage_key is not None
                lock_cas_digest(session, Sha256Digest(bytes.fromhex(ticket.storage_key.value)))
                orphan = session.get(OrphanObjectClaimRow, ticket.claim_id, with_for_update=True)
                eligible = (
                    orphan is not None
                    and orphan.completed_at is None
                    and orphan.storage_key == ticket.storage_key.value
                    and not object_key_is_registered(session, orphan.storage_key)
                )
            else:
                assert ticket.provider_execution_id is not None
                provider = session.get(
                    ProviderStagingRow, ticket.provider_execution_id, with_for_update=True
                )
                if ticket.action == MaintenanceAction.CLEANUP:
                    ProviderCleanupClaim(ticket.provider_execution_id, ticket.claim_id)
                    eligible = (
                        provider is not None
                        and provider.state == "CLEANUP_CLAIMED"
                        and provider.cleanup_claim_id == ticket.claim_id
                    )
                else:
                    ProviderScratchClaim(ticket.provider_execution_id, ticket.claim_id)
                    eligible = (
                        provider is not None
                        and provider.state == "HANDED_OFF"
                        and provider.scratch_claim_id == ticket.claim_id
                        and provider.scratch_retired_at is None
                    )
            if not eligible:
                raise ResourceAdmissionError("maintenance_claim_stale")
            require_internal_io_capacity(session)
            row = ProviderMaintenanceRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                provider_execution_id=ticket.provider_execution_id,
                claim_id=ticket.claim_id,
                upload_claim_id=(
                    ticket.claim_id if ticket.action == MaintenanceAction.UPLOAD_CLEANUP else None
                ),
                orphan_claim_id=(
                    ticket.claim_id
                    if ticket.action
                    in (MaintenanceAction.ORPHAN_OBJECT, MaintenanceAction.ORPHAN_MISSING)
                    else None
                ),
                storage_key=ticket.storage_key.value if ticket.storage_key is not None else None,
                action=ticket.action,
                singleton_id=1,
                state="PREPARED",
                created_at=self._now(session),
            )
            session.add(row)
            session.flush()
            return self._status(row)

    def start(self, ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus:
        with self._sessions.begin() as session:
            row = self._locked(session, ticket)
            current = self._status(row)
            if current.state == ExecutionState.RUNNING and current.child == child:
                return current
            if current.state != ExecutionState.PREPARED:
                raise ResourceAdmissionError("maintenance_execution_stale")
            row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
            row.state, row.started_at = "RUNNING", self._now(session)
            session.flush()
            return self._status(row)

    def status(self, ticket: MaintenanceTicket) -> MaintenanceStatus | None:
        with self._sessions() as session:
            return self._inspect(session, ticket)

    def reconcile(self, ticket: MaintenanceTicket) -> MaintenanceStatus | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            return self._inspect(session, ticket)

    def _inspect(self, session: Session, ticket: MaintenanceTicket) -> MaintenanceStatus | None:
        row = session.get(ProviderMaintenanceRow, ticket.execution_id)
        if row is None:
            return None
        if self._status(row).ticket != ticket:
            raise ResourceAdmissionError("maintenance_execution_stale")
        return self._status(row)

    def confirm(self, ticket: MaintenanceTicket, proof: ProcessExitEvidence) -> MaintenanceStatus:
        with self._sessions.begin() as session:
            row = self._locked(session, ticket)
            current = self._status(row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state != ExecutionState.PREPARED
                and current.state != ExecutionState.CLOSED
            ):
                raise ResourceAdmissionError("maintenance_execution_stale")
            if current.state == ExecutionState.CLOSED:
                if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != (
                    proof.kind,
                    proof.evidence_sha256,
                    proof.exit_code,
                ):
                    raise ResourceAdmissionError("maintenance_execution_conflict")
                return current
            row.state, row.closed_at = "CLOSED", self._now(session)
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = (
                proof.kind,
                proof.evidence_sha256,
                proof.exit_code,
            )
            session.flush()
            return self._status(row)
