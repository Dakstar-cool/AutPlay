"""Quota policy CAS with exact M6 authority and shared terminal receipts."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import AccountRole
from autplay.domain.resource_admission import (
    AccountLimitOverride,
    AccountLimits,
    ResourceAdmissionError,
)
from autplay.domain.resource_policy import (
    GlobalResourceLimits,
    QuotaAccountItem,
    QuotaAccountPage,
    QuotaChange,
    QuotaEditorSnapshot,
    QuotaPolicySnapshot,
    QuotaUsageSummary,
)
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.ports.resource_policy import ResourcePolicyRepository, ResourcePolicyUnitOfWork

from .models.account import DeviceRow, UserAccountRow
from .models.audit import AuditEventRow
from .models.profile_pairing import ServerInstanceRow
from .models.public_access import AccountProvisioningLinkRow
from .models.resource_admission import (
    AccountQuotaOverrideRow,
    QuotaOperationReceiptRow,
    ResourceAdmissionRow,
    ResourceQuotaPolicyRow,
)
from .models.web_admin import WebSessionRow, WebTerminalReceiptRow
from .resource_admission import SqlAlchemyResourceAdmissionRepository
from .resource_limits import lock_resource_admission


class SqlAlchemyResourcePolicyRepository:
    def __init__(self, session: Session, *, automatic_acquisition_enabled: bool = False) -> None:
        self._s, self._automatic = session, automatic_acquisition_enabled

    def lock_actor(self, actor: WebActor, target: UUID | None, *, mutation: bool) -> datetime:
        instance = self._s.get(ServerInstanceRow, actor.server_instance_id, with_for_update=True)
        if instance is None:
            raise WebAdminError("authentication_required")
        lock_resource_admission(self._s)
        identifiers = sorted(
            {actor.user_id, *([target] if target is not None else [])}, key=lambda x: x.int
        )
        accounts = {
            row.user_id: row
            for row in self._s.scalars(
                select(UserAccountRow)
                .where(UserAccountRow.user_id.in_(identifiers))
                .order_by(UserAccountRow.user_id)
                .with_for_update()
                .execution_options(populate_existing=True),
            )
        }
        account = accounts.get(actor.user_id)
        web = self._s.get(
            WebSessionRow, actor.web_session_id, with_for_update=True, populate_existing=True
        )
        now = self._s.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError()
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role != "OWNER"
            or actor.role != AccountRole.OWNER
            or web is None
            or web.user_id != actor.user_id
            or web.server_instance_id != actor.server_instance_id
            or web.token_generation != actor.token_generation
            or web.revoked_at is not None
            or web.idle_expires_at <= now
            or web.absolute_expires_at <= now
            or (mutation and web.token_issued_at <= now - timedelta(minutes=15))
        ):
            raise WebAdminError("authentication_required")
        # OWNER is created only by one-time bootstrap; PA2 can create USER accounts only.
        owners = self._s.scalars(
            select(UserAccountRow.user_id).where(
                UserAccountRow.role == "OWNER",
                UserAccountRow.status == "ACTIVE",
                UserAccountRow.deleted_at.is_(None),
            )
        ).all()
        if owners != [actor.user_id]:
            raise WebAdminError("forbidden")
        if target is not None:
            target_account = accounts.get(target)
            if target_account is None or target_account.deleted_at is not None:
                raise WebAdminError("forbidden")
            if target != actor.user_id:
                link = self._s.get(AccountProvisioningLinkRow, target)
                if link is None or link.issued_by_user_id != actor.user_id:
                    raise WebAdminError("forbidden")
        return now

    def snapshot(self, target: UUID | None) -> QuotaPolicySnapshot:
        policy = self._s.get(ResourceQuotaPolicyRow, 1, populate_existing=True)
        if policy is None:
            raise ResourceAdmissionError()
        override = (
            self._s.get(AccountQuotaOverrideRow, target, populate_existing=True) if target else None
        )
        return QuotaPolicySnapshot(
            policy.revision,
            AccountLimits(
                policy.default_devices, policy.default_playbacks, policy.default_transfers
            ),
            policy.global_playbacks,
            policy.global_transfers,
            policy.playback_ceiling,
            policy.transfer_ceiling,
            target,
            override.revision if override else 0,
            AccountLimitOverride(override.devices, override.playbacks, override.transfers)
            if override
            else AccountLimitOverride(),
        )

    def editor(self, target: UUID | None, now: datetime) -> QuotaEditorSnapshot:
        row = ResourceAdmissionRow
        charged = SqlAlchemyResourceAdmissionRepository.charged(now)
        waiting = SqlAlchemyResourceAdmissionRepository.waiting(now)
        counts = select(
            func.count().filter(and_(row.kind == "PLAYBACK", charged)),
            func.count().filter(and_(row.kind == "TRANSFER", charged)),
            func.count().filter(and_(row.kind == "PLAYBACK", waiting)),
            func.count().filter(and_(row.kind == "TRANSFER", waiting)),
        ).select_from(row)
        devices = select(func.count()).select_from(DeviceRow).where(DeviceRow.revoked_at.is_(None))
        if target is not None:
            counts = counts.where(row.user_id == target)
            devices = devices.where(DeviceRow.user_id == target)
        playbacks, transfers, waiting_playbacks, waiting_transfers = self._s.execute(counts).one()
        account = self._s.get(UserAccountRow, target) if target is not None else None
        return QuotaEditorSnapshot(
            self.snapshot(target),
            QuotaUsageSummary(
                int(self._s.scalar(devices) or 0),
                int(playbacks),
                int(transfers),
                int(waiting_playbacks),
                int(waiting_transfers),
            ),
            QuotaAccountItem(account.user_id, account.display_name, account.status)
            if account is not None
            else None,
        )

    def accounts(self, actor_id: UUID, after: UUID | None) -> QuotaAccountPage:
        row = UserAccountRow
        statement = select(row.user_id, row.display_name, row.status).where(
            row.deleted_at.is_(None),
            or_(
                row.user_id == actor_id,
                exists().where(
                    AccountProvisioningLinkRow.user_id == row.user_id,
                    AccountProvisioningLinkRow.issued_by_user_id == actor_id,
                ),
            ),
        )
        if after is not None:
            statement = statement.where(row.user_id > after)
        rows = self._s.execute(statement.order_by(row.user_id).limit(51)).all()
        items = tuple(
            QuotaAccountItem(user_id, name, status) for user_id, name, status in rows[:50]
        )
        return QuotaAccountPage(items, items[-1].user_id if len(rows) > 50 else None)

    def replay(self, actor: WebActor, change: QuotaChange) -> dict[str, object] | None:
        receipt = self._s.get(QuotaOperationReceiptRow, change.operation_id)
        common = self._s.get(WebTerminalReceiptRow, change.operation_id)
        if receipt is not None:
            if (
                receipt.actor_user_id != actor.user_id
                or receipt.web_session_id != actor.web_session_id
                or receipt.web_generation != actor.token_generation
                or receipt.request_sha256 != change.digest
                or receipt.action != change.action.value
                or receipt.target_user_id != change.target_user_id
            ):
                raise ResourceAdmissionError("resource_operation_conflict")
            return dict(receipt.result)
        if common is not None:
            raise ResourceAdmissionError("resource_operation_conflict")
        return None

    def save(self, change: QuotaChange, now: datetime) -> None:
        values = change.values
        policy = self._s.get(ResourceQuotaPolicyRow, 1)
        if policy is None:
            raise ResourceAdmissionError()
        if isinstance(values, AccountLimitOverride):
            if change.target_user_id is None:
                raise ResourceAdmissionError()
            override = self._s.get(AccountQuotaOverrideRow, change.target_user_id)
            if override is None:
                override = AccountQuotaOverrideRow(user_id=change.target_user_id, revision=0)
                self._s.add(override)
            override.revision += 1
            override.devices, override.playbacks, override.transfers = (
                values.devices,
                values.playbacks,
                values.transfers,
            )
            override.updated_at = now
        else:
            if isinstance(values, AccountLimits):
                policy.default_devices, policy.default_playbacks, policy.default_transfers = (
                    values.devices,
                    values.playbacks,
                    values.transfers,
                )
            elif isinstance(values, GlobalResourceLimits):
                policy.global_playbacks, policy.global_transfers = (
                    values.playbacks,
                    values.transfers,
                )
            policy.revision += 1
            policy.updated_at = now
        self._s.flush()

    def receipt(
        self,
        actor: WebActor,
        change: QuotaChange,
        result: dict[str, object],
        now: datetime,
    ) -> None:
        web = self._s.get(WebSessionRow, actor.web_session_id)
        if web is None:
            raise WebAdminError("authentication_required")
        action = "resource_quota." + change.action.value.lower() + "_changed"
        target_type = "USER_ACCOUNT" if change.target_user_id else "RESOURCE_QUOTA_POLICY"
        target_id = change.target_user_id or actor.server_instance_id
        self._s.add(
            QuotaOperationReceiptRow(
                operation_id=change.operation_id,
                actor_user_id=actor.user_id,
                web_session_id=actor.web_session_id,
                web_generation=actor.token_generation,
                action=change.action.value,
                target_user_id=change.target_user_id,
                request_sha256=change.digest,
                result=result,
                created_at=now,
            )
        )
        self._s.add(
            WebTerminalReceiptRow(
                operation_id=change.operation_id,
                server_instance_id=actor.server_instance_id,
                user_id=actor.user_id,
                web_session_id=actor.web_session_id,
                token_generation=actor.token_generation,
                token_sha256=web.token_sha256,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason_code=None,
                request_sha256=change.digest,
                outcome="APPLIED",
                terminal_at=now,
                receipt_expires_at=web.absolute_expires_at + timedelta(minutes=5),
            )
        )
        self._s.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=actor.user_id,
                actor_device_id=None,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=change.operation_id,
                metadata_sanitized={
                    "global_revision": result["global_revision"],
                    "account_revision": result["account_revision"],
                },
            )
        )
        self._s.flush()

    def advance(self, now: datetime) -> None:
        SqlAlchemyResourceAdmissionRepository(
            self._s,
            automatic_acquisition_enabled=self._automatic,
        ).advance(now)


class SqlAlchemyResourcePolicyUnitOfWork:
    def __init__(
        self, sessions: sessionmaker[Session], *, automatic_acquisition_enabled: bool
    ) -> None:
        self._sessions, self._automatic = sessions, automatic_acquisition_enabled
        self._session: Session | None = None
        self.policy: ResourcePolicyRepository

    def __enter__(self) -> SqlAlchemyResourcePolicyUnitOfWork:
        self._session = self._sessions()
        self.policy = SqlAlchemyResourcePolicyRepository(
            self._session, automatic_acquisition_enabled=self._automatic
        )
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("resource policy unit of work inactive")
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


class SqlAlchemyResourcePolicyUnitOfWorkFactory:
    def __init__(
        self, sessions: sessionmaker[Session], *, automatic_acquisition_enabled: bool = False
    ) -> None:
        self._sessions, self._automatic = sessions, automatic_acquisition_enabled

    def __call__(self) -> ResourcePolicyUnitOfWork:
        return SqlAlchemyResourcePolicyUnitOfWork(
            self._sessions, automatic_acquisition_enabled=self._automatic
        )
