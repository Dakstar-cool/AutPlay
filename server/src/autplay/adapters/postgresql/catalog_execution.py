"""Shared provider admission with short grants and retained exact catalog process exit."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import Principal
from autplay.domain.catalog_execution import CatalogExecutionTicket
from autplay.domain.ingest_execution import INGEST_IO_TTL, IngestExecutionStatus
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)

from .models.account import DeviceRow, UserAccountRow, UserSessionRow
from .models.catalog_execution import CatalogExecutionRow
from .models.metadata_execution import MetadataProviderGateRow

type CatalogStatus = IngestExecutionStatus[CatalogExecutionTicket]


def _now(session: Session) -> datetime:
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise ResourceAdmissionError("metadata_catalog_unavailable")
    return value


def _status(row: CatalogExecutionRow) -> CatalogStatus:
    return IngestExecutionStatus(
        CatalogExecutionTicket(
            row.execution_id,
            row.owner_run_id,
            row.user_id,
            row.device_id,
            row.session_id,
            row.authority_generation,
            row.request_id,
        ),
        ExecutionState(row.state),
        None
        if row.child_pid is None or row.child_identity_sha256 is None
        else ProcessIdentity(row.child_pid, row.child_identity_sha256),
        row.io_deadline_at,
        row.heartbeat_at,
    )


def _authority(session: Session, user: UUID, device: UUID, actor: UUID) -> int:
    generation = session.scalar(
        select(UserAccountRow.authority_generation)
        .join(DeviceRow, DeviceRow.user_id == UserAccountRow.user_id)
        .join(
            UserSessionRow,
            (UserSessionRow.user_id == UserAccountRow.user_id)
            & (UserSessionRow.device_id == DeviceRow.device_id),
        )
        .where(
            UserAccountRow.user_id == user,
            UserAccountRow.status == "ACTIVE",
            UserAccountRow.deleted_at.is_(None),
            DeviceRow.device_id == device,
            DeviceRow.revoked_at.is_(None),
            UserSessionRow.session_id == actor,
            UserSessionRow.revoked_at.is_(None),
            UserSessionRow.expires_at > func.clock_timestamp(),
        )
        .with_for_update()
    )
    if generation is None:
        raise ResourceAdmissionError("metadata_catalog_unauthorized")
    return generation


def _require_authority(session: Session, ticket: CatalogExecutionTicket) -> None:
    if (
        _authority(session, ticket.user_id, ticket.device_id, ticket.session_id)
        != ticket.authority_generation
    ):
        raise ResourceAdmissionError("metadata_catalog_unauthorized")


def _locked(session: Session, ticket: CatalogExecutionTicket) -> CatalogExecutionRow:
    row = session.get(
        CatalogExecutionRow, ticket.execution_id, with_for_update=True, populate_existing=True
    )
    if row is None or _status(row).ticket != ticket:
        raise ResourceAdmissionError("metadata_catalog_stale")
    return row


class PostgresCatalogExecutionRepository:
    """Expiry revokes GO/renewal; only trusted process evidence releases occupancy."""

    def __init__(
        self, sessions: sessionmaker[Session], *, lifetime: timedelta = timedelta(seconds=30)
    ) -> None:
        if not 0 < lifetime.total_seconds() <= 30:
            raise ValueError("metadata_catalog_lifetime_invalid")
        self._sessions, self._lifetime = sessions, lifetime

    def plan(self, principal: Principal, owner: UUID) -> CatalogExecutionTicket:
        with self._sessions.begin() as session:
            generation = _authority(
                session, principal.user_id, principal.device_id, principal.session_id
            )
        return CatalogExecutionTicket(
            uuid4(),
            owner,
            principal.user_id,
            principal.device_id,
            principal.session_id,
            generation,
            uuid4(),
        )

    def require_current(self, ticket: CatalogExecutionTicket) -> None:
        with self._sessions.begin() as session:
            _require_authority(session, ticket)

    def prepare(self, ticket: CatalogExecutionTicket) -> CatalogStatus:
        with self._sessions.begin() as session:
            _require_authority(session, ticket)
            old = session.get(CatalogExecutionRow, ticket.execution_id)
            if old is not None:
                if _status(old).ticket != ticket:
                    raise ResourceAdmissionError("metadata_catalog_stale")
                return _status(old)
            gate = session.scalar(
                select(MetadataProviderGateRow)
                .where(MetadataProviderGateRow.singleton_id == 1)
                .with_for_update(skip_locked=True)
            )
            now = _now(session)
            if gate is None or gate.request_id is not None or gate.next_request_at > now:
                raise ResourceAdmissionError("metadata_provider_busy")
            row = CatalogExecutionRow(
                execution_id=ticket.execution_id,
                owner_run_id=ticket.owner_run_id,
                user_id=ticket.user_id,
                device_id=ticket.device_id,
                session_id=ticket.session_id,
                authority_generation=ticket.authority_generation,
                request_id=ticket.request_id,
                state="PREPARED",
                created_at=now,
                deadline_at=now + self._lifetime,
            )
            session.add(row)
            session.flush()
            gate.catalog_execution_id, gate.request_id = ticket.execution_id, ticket.request_id
            session.flush()
            return _status(row)

    def _refresh(self, session: Session, row: CatalogExecutionRow) -> CatalogStatus:
        now = _now(session)
        if row.deadline_at <= now or (row.io_deadline_at is not None and row.io_deadline_at <= now):
            raise ResourceAdmissionError("metadata_catalog_stale")
        row.heartbeat_at, row.io_deadline_at = now, min(now + INGEST_IO_TTL, row.deadline_at)
        session.flush()
        return _status(row)

    def start(self, ticket: CatalogExecutionTicket, child: ProcessIdentity) -> CatalogStatus:
        with self._sessions.begin() as session:
            _require_authority(session, ticket)
            row = _locked(session, ticket)
            if row.state == "PREPARED":
                row.state, row.started_at = "RUNNING", _now(session)
                row.child_pid, row.child_identity_sha256 = child.pid, child.identity_sha256
            elif row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("metadata_catalog_stale")
            return self._refresh(session, row)

    def renew(self, ticket: CatalogExecutionTicket, child: ProcessIdentity) -> CatalogStatus:
        with self._sessions.begin() as session:
            _require_authority(session, ticket)
            row = _locked(session, ticket)
            if row.state != "RUNNING" or _status(row).child != child:
                raise ResourceAdmissionError("metadata_catalog_stale")
            return self._refresh(session, row)

    def status(self, ticket: CatalogExecutionTicket) -> CatalogStatus | None:
        with self._sessions() as session:
            row = session.get(CatalogExecutionRow, ticket.execution_id)
            if row is None:
                return None
            if _status(row).ticket != ticket:
                raise ResourceAdmissionError("metadata_catalog_stale")
            return _status(row)

    def reconcile(self, ticket: CatalogExecutionTicket) -> CatalogStatus | None:
        return self.status(ticket)

    def confirm(self, ticket: CatalogExecutionTicket, proof: ProcessExitEvidence) -> CatalogStatus:
        with self._sessions.begin() as session:
            row = _locked(session, ticket)
            current = _status(row)
            if current.child != proof.child or (
                proof.kind == ExitKind.NOT_STARTED
                and current.state not in {ExecutionState.PREPARED, ExecutionState.CLOSED}
            ):
                raise ResourceAdmissionError("metadata_catalog_stale")
            evidence = proof.kind, proof.evidence_sha256, proof.exit_code
            if current.state == ExecutionState.CLOSED:
                if (row.closure_kind, row.closure_evidence_sha256, row.exit_code) != evidence:
                    raise ResourceAdmissionError("metadata_catalog_conflict")
                return current
            now = _now(session)
            row.state, row.closed_at = "CLOSED", now
            row.closure_kind, row.closure_evidence_sha256, row.exit_code = evidence
            # Flush the exact closure before the gate trigger checks its authority.
            session.flush()
            gate = session.get(MetadataProviderGateRow, 1, with_for_update=True)
            if gate is not None and (gate.catalog_execution_id, gate.request_id) == (
                ticket.execution_id,
                ticket.request_id,
            ):
                gate.catalog_execution_id = gate.request_id = None
                gate.next_request_at = max(
                    gate.next_request_at, _now(session) + timedelta(milliseconds=1100)
                )
            session.flush()
            return _status(row)
