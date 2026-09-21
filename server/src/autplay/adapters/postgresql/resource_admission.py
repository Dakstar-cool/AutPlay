"""PostgreSQL policy, fair grants and draining permits, serialized across processes."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    IO_PERMIT_TTL,
    LEASE_TTL,
    RESERVATION_TTL,
    TERMINAL_RETENTION,
    AccountLimits,
    AcquisitionClaim,
    ActivationFence,
    AdmissionState,
    AdmissionStatus,
    AuthorityKind,
    IoPermit,
    LocalBridgeClaim,
    ResourceAdmission,
    ResourceAdmissionError,
    ResourceAuthority,
    ResourceKind,
    ResourceRequest,
    ResourceUsage,
)
from autplay.ports.resource_admission import (
    ResourceAdmissionRepository,
    ResourceAdmissionUnitOfWork,
)
from autplay.ports.resource_execution import ResourceExecutionRepository

from .models.jobs import JobRow
from .models.resource_admission import (
    QuotaOperationReceiptRow,
    ResourceAdmissionRow,
    ResourceGrantCursorRow,
    ResourceIoExecutionRow,
    ResourceIoPermitRow,
    ResourceQuotaPolicyRow,
)
from .resource_authority import ResourceAuthorityGate
from .resource_execution import SqlAlchemyResourceExecutionRepository
from .resource_limits import effective_account_limits, lock_resource_admission

_ELIGIBLE = text("""
WITH charged AS (
    SELECT operation_id, user_id, device_id FROM account.resource_admission a
    WHERE kind=:kind AND (
        (state='ACTIVE' AND lease_until>:now AND (claimed_at IS NOT NULL OR claim_until>:now))
        OR EXISTS (SELECT 1 FROM account.resource_io_permit p
                   WHERE p.operation_id=a.operation_id AND p.expires_at>:now)
        OR EXISTS (SELECT 1 FROM account.resource_io_execution e
                   WHERE e.operation_id=a.operation_id AND e.closed_at IS NULL)
    )
), account_usage AS (
    SELECT user_id,count(*) AS used FROM charged GROUP BY user_id
), device_usage AS (
    SELECT device_id,count(*) AS used FROM charged WHERE device_id IS NOT NULL GROUP BY device_id
)
SELECT a.operation_id FROM account.resource_admission a
JOIN account.user_account u ON u.user_id=a.user_id AND u.status='ACTIVE'
    AND u.deleted_at IS NULL AND u.authority_generation=a.authority_generation
LEFT JOIN account.account_quota_override q ON q.user_id=a.user_id
LEFT JOIN account.resource_grant_cursor g ON g.user_id=a.user_id AND g.kind=a.kind
LEFT JOIN account_usage au ON au.user_id=a.user_id
LEFT JOIN device_usage du ON du.device_id=a.device_id
WHERE a.kind=:kind AND a.state='WAITING'
  AND (a.waiting_until>:now OR (a.job_id IS NOT NULL AND a.waiting_until IS NULL))
  AND (a.job_id IS NULL OR EXISTS (
      SELECT 1 FROM jobs.job j WHERE j.job_id=a.job_id AND j.cancel_requested_at IS NULL
        AND ((j.state='RUNNING' AND j.lease_deadline>:now)
             OR (j.state='RETRY_WAIT' AND j.resource_waiting
                 AND (j.resource_wake_until IS NULL OR j.resource_wake_until>:now)))))
  AND NOT EXISTS (SELECT 1 FROM account.resource_io_permit p
                  WHERE p.operation_id=a.operation_id AND p.expires_at>:now)
  AND NOT EXISTS (SELECT 1 FROM account.resource_io_execution e
                  WHERE e.operation_id=a.operation_id AND e.closed_at IS NULL)
  AND coalesce(au.used,0)<coalesce(CASE WHEN :kind='PLAYBACK' THEN q.playbacks ELSE q.transfers END,
                                 :account_limit)
  AND (a.device_id IS NULL OR coalesce(du.used,0)<:device_limit)
ORDER BY coalesce(g.last_grant,0),a.enqueued_at,a.operation_id
LIMIT 1
""")


class SqlAlchemyResourceAdmissionRepository:
    def __init__(self, session: Session, *, automatic_acquisition_enabled: bool = False) -> None:
        self._s = session
        self._gate = ResourceAuthorityGate(
            session,
            automatic_acquisition_enabled=automatic_acquisition_enabled,
        )

    def lock(self) -> datetime:
        lock_resource_admission(self._s)
        return self.current_time()

    def current_time(self) -> datetime:
        # Transaction start/client time can precede a contended lock by an arbitrary interval.
        now = self._s.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError()
        return now

    def authenticate(self, actor: Principal, now: datetime) -> ResourceAuthority:
        return self._gate.authenticate(actor, now)

    def authenticate_acquisition(self, actor: AcquisitionClaim, now: datetime) -> ResourceAuthority:
        return self._gate.authenticate_acquisition(actor, now)

    def authenticate_bridge(self, actor: LocalBridgeClaim, now: datetime) -> ResourceAuthority:
        return self._gate.authenticate_bridge(actor, now)

    def require_authority(self, authority: ResourceAuthority, now: datetime) -> None:
        self._gate.require(authority, now)

    def require_target(
        self,
        authority: ResourceAuthority,
        request: ResourceRequest,
        now: datetime,
    ) -> None:
        self._gate.target(authority, request, now)

    def require_recording(self, authority: ResourceAuthority, recording_id: UUID) -> None:
        self._gate.recording(authority, recording_id)

    def stream_recording(self, authority: ResourceAuthority, audio_variant_id: UUID) -> UUID:
        return self._gate.stream_recording(authority, audio_variant_id)

    def limits(self, user_id: UUID) -> AccountLimits:
        return effective_account_limits(self._s, user_id)

    def _policy(self) -> ResourceQuotaPolicyRow:
        policy = self._s.get(ResourceQuotaPolicyRow, 1, populate_existing=True)
        if policy is None:
            raise ResourceAdmissionError()
        return policy

    def server_limit(self, kind: ResourceKind) -> int:
        policy = self._policy()
        limit = (
            policy.global_playbacks if kind == ResourceKind.PLAYBACK else policy.global_transfers
        )
        if limit is None:
            raise ResourceAdmissionError("resource_budget_unconfigured")
        return limit

    def find(self, operation_id: UUID) -> ResourceAdmission | None:
        row = self._s.get(ResourceAdmissionRow, operation_id, populate_existing=True)
        if row is None:
            return None
        authority = ResourceAuthority(
            row.user_id,
            row.authority_generation,
            AuthorityKind(row.authority_kind),
            row.device_id,
            row.session_family_id,
            row.session_mode,
            row.job_id,
            row.job_worker_id,
            row.job_attempt,
            row.acquisition_attempt_id,
            row.source_authorization_id,
            row.source_authorization_revision,
            row.policy_id,
            row.policy_revision,
        )
        request = ResourceRequest(
            row.operation_id,
            ResourceKind(row.kind),
            row.resource_type,
            row.resource_id,
            row.target_id,
        )
        rest = {
            field.name: getattr(row, field.name)
            for field in fields(ResourceAdmission)
            if field.name not in {"authority", "request", "state"}
        }
        return ResourceAdmission(authority, request, state=AdmissionState(row.state), **rest)

    def save(self, operation: ResourceAdmission) -> None:
        values = {
            **asdict(operation.authority),
            **asdict(operation.request),
            **{
                field.name: getattr(operation, field.name)
                for field in fields(ResourceAdmission)
                if field.name not in {"authority", "request"}
            },
        }
        row = self._s.get(ResourceAdmissionRow, operation.request.operation_id)
        if row is None:
            self._s.add(ResourceAdmissionRow(**values))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self._s.flush()

    def waiting_count(self, user_id: UUID, kind: ResourceKind, now: datetime) -> int:
        return (
            self._s.scalar(
                select(func.count())
                .select_from(ResourceAdmissionRow)
                .where(
                    ResourceAdmissionRow.user_id == user_id,
                    ResourceAdmissionRow.kind == kind,
                    self.waiting(now),
                )
            )
            or 0
        )

    @staticmethod
    def waiting(now: datetime) -> ColumnElement[bool]:
        row = ResourceAdmissionRow
        return and_(
            row.state == "WAITING",
            or_(
                row.waiting_until > now, and_(row.job_id.is_not(None), row.waiting_until.is_(None))
            ),
        )

    @staticmethod
    def charged(now: datetime) -> ColumnElement[bool]:
        """Shared occupied-capacity predicate for admission and the Admin usage view."""
        row = ResourceAdmissionRow
        return or_(
            and_(
                row.state == "ACTIVE",
                row.lease_until > now,
                or_(row.claimed_at.is_not(None), row.claim_until > now),
            ),
            exists().where(
                ResourceIoPermitRow.operation_id == row.operation_id,
                ResourceIoPermitRow.expires_at > now,
            ),
            exists().where(
                ResourceIoExecutionRow.operation_id == row.operation_id,
                ResourceIoExecutionRow.closed_at.is_(None),
            ),
        )

    def usage(
        self, authority: ResourceAuthority, kind: ResourceKind, now: datetime
    ) -> ResourceUsage:
        row = ResourceAdmissionRow
        result = self._s.execute(
            select(
                func.count().filter(row.user_id == authority.user_id),
                func.count().filter(row.device_id == authority.device_id)
                if authority.device_id is not None
                else func.count().filter(text("false")),
                func.count(),
            )
            .select_from(row)
            .where(row.kind == kind, self.charged(now))
        ).one()
        return ResourceUsage(int(result[0]), int(result[1]), int(result[2]))

    def advance(self, now: datetime, maximum: int = 64) -> int:
        policy = self._policy()
        if policy.global_playbacks is None or policy.global_transfers is None:
            return 0
        changed = 0
        for kind in (ResourceKind.PLAYBACK, ResourceKind.TRANSFER):
            global_limit = self.server_limit(kind)
            used = (
                self._s.scalar(
                    select(func.count())
                    .select_from(ResourceAdmissionRow)
                    .where(
                        ResourceAdmissionRow.kind == kind,
                        self.charged(now),
                    )
                )
                or 0
            )
            for _ in range(maximum):
                if used >= global_limit:
                    break
                identifier = self._s.scalar(
                    _ELIGIBLE,
                    {
                        "kind": kind.value,
                        "now": now,
                        "device_limit": 1 if kind == ResourceKind.PLAYBACK else 2,
                        "account_limit": policy.default_playbacks
                        if kind == ResourceKind.PLAYBACK
                        else policy.default_transfers,
                    },
                )
                if identifier is None:
                    break
                operation = self.find(identifier)
                if operation is None:
                    raise ResourceAdmissionError()
                try:
                    if operation.authority.job_id is not None:
                        self._gate.require_intent(operation.authority, operation.request, now)
                        job = self._s.get(JobRow, operation.authority.job_id)
                        if job is None:
                            raise ResourceAdmissionError()
                        if (
                            job.state != "RUNNING"
                            or job.lease_owner != operation.authority.job_worker_id
                            or job.attempt_count != operation.authority.job_attempt
                        ):
                            # Wake the fair winner before letting a younger intent grant.
                            # This hint gives no I/O authority; the next claim must rebind.
                            if (
                                job.state == "RETRY_WAIT"
                                and job.resource_waiting
                                and job.resource_wake_until is None
                            ):
                                job.resource_wake_until = now + RESERVATION_TTL
                                job.scheduled_at = now
                                self._s.flush()
                                changed += 1
                            break
                    self.require_authority(operation.authority, now)
                    self.require_target(operation.authority, operation.request, now)
                except ResourceAdmissionError:
                    operation.state, operation.terminal_at = AdmissionState.EXPIRED, now
                    operation.updated_at = now
                    self.save(operation)
                    changed += 1
                    continue
                # The scheduler excludes unclosed executions before replacing this fence.
                self._s.execute(
                    delete(ResourceIoExecutionRow).where(
                        ResourceIoExecutionRow.operation_id == identifier,
                        ResourceIoExecutionRow.closed_at.is_not(None),
                    )
                )
                self._s.execute(
                    delete(ResourceIoPermitRow).where(
                        ResourceIoPermitRow.operation_id == identifier,
                        ResourceIoPermitRow.expires_at <= now,
                    )
                )
                operation.state, operation.activation_id = AdmissionState.ACTIVE, uuid4()
                operation.generation += 1
                operation.lease_until, operation.claim_until = (
                    now + LEASE_TTL,
                    now + RESERVATION_TTL,
                )
                operation.waiting_until = operation.terminal_at = operation.claimed_at = None
                operation.updated_at = now
                self.save(operation)
                # _policy() refreshes the same ORM instance; flush each grant before another lookup.
                policy.grant_sequence += 1
                cursor = self._s.get(
                    ResourceGrantCursorRow, (operation.authority.user_id, kind.value)
                )
                if cursor is None:
                    self._s.add(
                        ResourceGrantCursorRow(
                            user_id=operation.authority.user_id,
                            kind=kind.value,
                            last_grant=policy.grant_sequence,
                        )
                    )
                else:
                    cursor.last_grant = policy.grant_sequence
                self._s.flush()
                changed, used = changed + 1, used + 1
        return changed

    def status(self, operation: ResourceAdmission, now: datetime) -> AdmissionStatus:
        limits = self.limits(operation.authority.user_id)
        used = self.usage(operation.authority, operation.request.kind, now)
        reason = None
        if operation.state == AdmissionState.WAITING:
            if used.account >= limits.for_kind(operation.request.kind):
                reason = "ACCOUNT_CAPACITY"
            elif operation.authority.device_id is not None and used.device >= (
                1 if operation.request.kind == ResourceKind.PLAYBACK else 2
            ):
                reason = "DEVICE_CAPACITY"
            else:
                reason = "SERVER_CAPACITY"
        return AdmissionStatus(
            operation, limits, used, self.server_limit(operation.request.kind), reason
        )

    @staticmethod
    def _permit(row: ResourceIoPermitRow) -> IoPermit:
        return IoPermit(
            row.permit_id,
            ActivationFence(
                row.operation_id,
                row.activation_id,
                row.generation,
            ),
            row.target_id,
            row.expires_at,
        )

    def permits(self, operation_id: UUID, now: datetime) -> tuple[IoPermit, ...]:
        return tuple(
            self._permit(row)
            for row in self._s.scalars(
                select(ResourceIoPermitRow).where(
                    ResourceIoPermitRow.operation_id == operation_id,
                    or_(
                        ResourceIoPermitRow.expires_at > now,
                        self._unclosed_execution(),
                    ),
                ),
            )
        )

    def open_permit(self, operation: ResourceAdmission, target_id: UUID, now: datetime) -> IoPermit:
        if operation.fence is None:
            raise ResourceAdmissionError("resource_activation_stale")
        row = ResourceIoPermitRow(
            permit_id=uuid4(),
            operation_id=operation.request.operation_id,
            activation_id=operation.activation_id,
            generation=operation.generation,
            target_id=target_id,
            opened_at=now,
            renewed_at=now,
            expires_at=now + IO_PERMIT_TTL,
        )
        self._s.add(row)
        self._s.flush()
        return self._permit(row)

    def renew_permit(self, permit: IoPermit, now: datetime) -> IoPermit:
        row = self._s.get(ResourceIoPermitRow, permit.permit_id, populate_existing=True)
        if (
            row is None
            or self._permit(row).fence != permit.fence
            or row.target_id != permit.target_id
        ):
            raise ResourceAdmissionError("resource_io_stale")
        if row.expires_at <= now:
            raise ResourceAdmissionError("resource_io_stale")
        execution = self._s.scalar(
            select(ResourceIoExecutionRow).where(
                ResourceIoExecutionRow.permit_id == permit.permit_id,
            )
        )
        if execution is not None and execution.state not in {"PREPARED", "RUNNING"}:
            raise ResourceAdmissionError("resource_io_stale")
        row.renewed_at, row.expires_at = now, now + IO_PERMIT_TTL
        self._s.flush()
        return self._permit(row)

    def close_permit(self, permit: IoPermit) -> None:
        matching = (
            ResourceIoPermitRow.permit_id == permit.permit_id,
            ResourceIoPermitRow.operation_id == permit.fence.operation_id,
            ResourceIoPermitRow.activation_id == permit.fence.activation_id,
            ResourceIoPermitRow.generation == permit.fence.generation,
            ResourceIoPermitRow.target_id == permit.target_id,
        )
        if self._s.scalar(select(exists().where(*matching, self._unclosed_execution()))):
            raise ResourceAdmissionError("resource_execution_unconfirmed")
        self._s.execute(
            delete(ResourceIoExecutionRow).where(
                ResourceIoExecutionRow.permit_id.in_(
                    select(ResourceIoPermitRow.permit_id).where(*matching)
                ),
                ResourceIoExecutionRow.closed_at.is_not(None),
            )
        )
        self._s.execute(delete(ResourceIoPermitRow).where(*matching))

    @staticmethod
    def _unclosed_execution() -> ColumnElement[bool]:
        return exists().where(
            ResourceIoExecutionRow.permit_id == ResourceIoPermitRow.permit_id,
            ResourceIoExecutionRow.closed_at.is_(None),
        )

    def cleanup(self, now: datetime, maximum: int) -> int:
        # Touch validated intents so the bounded scan progresses beyond a valid prefix.
        # This changes scan recency only; enqueued_at remains the fairness authority.
        row = ResourceAdmissionRow
        worker_waits = self._s.scalars(
            select(row.operation_id)
            .where(row.state == "WAITING", row.job_id.is_not(None))
            .order_by(row.updated_at, row.operation_id)
            .limit(maximum)
        ).all()
        invalidated = 0
        for identifier in worker_waits:
            operation = self.find(identifier)
            if operation is None:
                continue
            try:
                self._gate.require_intent(operation.authority, operation.request, now)
            except ResourceAdmissionError:
                operation.state, operation.terminal_at = AdmissionState.EXPIRED, now
                invalidated += 1
            operation.updated_at = now
            self.save(operation)
        expired_permits = (
            select(ResourceIoPermitRow.permit_id)
            .where(
                ResourceIoPermitRow.expires_at <= now,
                ~self._unclosed_execution(),
            )
            .order_by(ResourceIoPermitRow.expires_at)
            .limit(maximum)
        )
        expired_ids = list(self._s.scalars(expired_permits))
        self._s.execute(
            delete(ResourceIoExecutionRow).where(
                ResourceIoExecutionRow.permit_id.in_(expired_ids),
                ResourceIoExecutionRow.closed_at.is_not(None),
            )
        )
        self._s.execute(
            delete(ResourceIoPermitRow).where(
                ResourceIoPermitRow.permit_id.in_(expired_ids),
            )
        )
        row = ResourceAdmissionRow
        expired = self._s.scalars(
            select(row.operation_id)
            .where(
                or_(
                    and_(row.state == "WAITING", row.waiting_until <= now),
                    and_(
                        row.state == "ACTIVE",
                        or_(
                            row.lease_until <= now,
                            and_(row.claimed_at.is_(None), row.claim_until <= now),
                        ),
                    ),
                )
            )
            .order_by(row.updated_at)
            .limit(maximum)
        ).all()
        for identifier in expired:
            operation = self.find(identifier)
            if operation is not None:
                operation.expire(now)
                self.save(operation)
        removable = self._s.scalars(
            select(row.operation_id)
            .where(
                row.state.in_(("RELEASED", "EXPIRED")),
                row.terminal_at < now - TERMINAL_RETENTION,
                ~exists().where(ResourceIoPermitRow.operation_id == row.operation_id),
            )
            .order_by(row.terminal_at)
            .limit(maximum)
        ).all()
        if removable:
            self._s.execute(delete(row).where(row.operation_id.in_(removable)))
        receipts = self._s.scalars(
            select(QuotaOperationReceiptRow.operation_id)
            .where(QuotaOperationReceiptRow.created_at < now - TERMINAL_RETENTION)
            .order_by(QuotaOperationReceiptRow.created_at)
            .limit(maximum)
        ).all()
        if receipts:
            self._s.execute(
                delete(QuotaOperationReceiptRow).where(
                    QuotaOperationReceiptRow.operation_id.in_(receipts)
                )
            )
        return invalidated + len(expired) + len(removable) + len(receipts)


class SqlAlchemyResourceAdmissionUnitOfWork:
    def __init__(
        self, sessions: sessionmaker[Session], *, automatic_acquisition_enabled: bool
    ) -> None:
        self._sessions, self._automatic = sessions, automatic_acquisition_enabled
        self._session: Session | None = None
        self.admissions: ResourceAdmissionRepository
        self.executions: ResourceExecutionRepository

    def __enter__(self) -> SqlAlchemyResourceAdmissionUnitOfWork:
        self._session = self._sessions()
        self.executions = SqlAlchemyResourceExecutionRepository(self._session)
        self.admissions = SqlAlchemyResourceAdmissionRepository(
            self._session,
            automatic_acquisition_enabled=self._automatic,
        )
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("resource unit of work inactive")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._session is not None:
            self._session.rollback()
            self._session.close()
            self._session = None


class SqlAlchemyResourceAdmissionUnitOfWorkFactory:
    def __init__(
        self, sessions: sessionmaker[Session], *, automatic_acquisition_enabled: bool = False
    ) -> None:
        self._sessions, self._automatic = sessions, automatic_acquisition_enabled

    def __call__(self) -> ResourceAdmissionUnitOfWork:
        return SqlAlchemyResourceAdmissionUnitOfWork(
            self._sessions,
            automatic_acquisition_enabled=self._automatic,
        )
