"""PostgreSQL transaction for bounded M6-C browser administration commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    AuditEventRow,
    DeviceRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.profile_pairing import (
    EnrollmentInvitationRow,
    ServerInstanceRow,
)
from autplay.adapters.postgresql.models.web_admin import WebSessionRow, WebTerminalReceiptRow
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebAdminError


class SqlAlchemyAdminCommandRepository:
    """Executes one exact browser mutation in one short PostgreSQL transaction."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def execute(self, command: AdminCommand, *, action: str, target_type: str) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            # Global lock order: server instance, account, browser session, target, receipt.
            instance = session.get(
                ServerInstanceRow, command.actor.server_instance_id, with_for_update=True
            )
            account = session.get(UserAccountRow, command.actor.user_id, with_for_update=True)
            web = session.get(WebSessionRow, command.actor.web_session_id, with_for_update=True)
            if (
                instance is None
                or account is None
                or account.status != "ACTIVE"
                or account.deleted_at is not None
                or account.role not in {AccountRole.OWNER.value, AccountRole.ADMIN.value}
                or web is None
                or web.user_id != command.actor.user_id
                or web.server_instance_id != command.actor.server_instance_id
                or web.token_generation != command.actor.token_generation
                or web.revoked_at is not None
                or web.idle_expires_at <= now
                or web.absolute_expires_at <= now
            ):
                raise WebAdminError("authentication_required")

            receipt = session.get(WebTerminalReceiptRow, command.operation_id, with_for_update=True)
            if receipt is not None:
                if (
                    receipt.server_instance_id != command.actor.server_instance_id
                    or receipt.user_id != command.actor.user_id
                    or receipt.web_session_id != command.actor.web_session_id
                    or receipt.token_generation != command.actor.token_generation
                    or receipt.action != action
                    or receipt.target_type != target_type
                    or receipt.target_id != command.target_id
                    or receipt.reason_code != command.reason_code
                    or receipt.request_sha256 != command.request_sha256
                ):
                    raise WebAdminError("operation_conflict")
                return _result(command.operation_id, receipt.outcome, receipt.terminal_at)

            target = self._lock_target(session, command, target_type)
            already_terminal = _terminal(target, now)
            terminal_at = _terminal_at(target, now)
            if not already_terminal:
                if isinstance(target, EnrollmentInvitationRow):
                    target.cancelled_at = now
                elif isinstance(target, DeviceRow):
                    target.revoked_at = now
                    for row in session.scalars(
                        select(UserSessionRow)
                        .where(
                            UserSessionRow.user_id == command.actor.user_id,
                            UserSessionRow.device_id == command.target_id,
                            UserSessionRow.revoked_at.is_(None),
                        )
                        .with_for_update()
                    ).all():
                        row.revoked_at = now
                else:
                    target.revoked_at = now
                terminal_at = now
            outcome = "ALREADY_TERMINAL" if already_terminal else "APPLIED"
            session.add(
                WebTerminalReceiptRow(
                    operation_id=command.operation_id,
                    server_instance_id=command.actor.server_instance_id,
                    user_id=command.actor.user_id,
                    web_session_id=command.actor.web_session_id,
                    token_generation=command.actor.token_generation,
                    token_sha256=web.token_sha256,
                    action=action,
                    target_type=target_type,
                    target_id=command.target_id,
                    reason_code=command.reason_code,
                    request_sha256=command.request_sha256,
                    outcome=outcome,
                    terminal_at=terminal_at,
                    receipt_expires_at=web.absolute_expires_at + timedelta(minutes=5),
                )
            )
            session.add(
                AuditEventRow(
                    occurred_at=now,
                    actor_type="ADMIN",
                    actor_user_id=command.actor.user_id,
                    actor_device_id=None,
                    action=action,
                    target_type=target_type,
                    target_id=command.target_id,
                    request_id=command.operation_id,
                    reason_code=command.reason_code,
                    metadata_sanitized={"outcome": outcome},
                )
            )
            return _result(command.operation_id, outcome, terminal_at)

    def _lock_target(self, session: Session, command: AdminCommand, target_type: str) -> TargetRow:
        if target_type == "ENROLLMENT_INVITATION":
            invitation = session.scalar(
                select(EnrollmentInvitationRow)
                .where(
                    EnrollmentInvitationRow.invitation_id == command.target_id,
                    EnrollmentInvitationRow.user_id == command.actor.user_id,
                )
                .with_for_update()
            )
            if invitation is not None:
                return invitation
        elif target_type == "DEVICE":
            device = session.scalar(
                select(DeviceRow)
                .where(
                    DeviceRow.device_id == command.target_id,
                    DeviceRow.user_id == command.actor.user_id,
                )
                .with_for_update()
            )
            if device is not None:
                return device
        else:
            user_session = session.scalar(
                select(UserSessionRow)
                .where(
                    UserSessionRow.session_id == command.target_id,
                    UserSessionRow.user_id == command.actor.user_id,
                )
                .with_for_update()
            )
            if user_session is not None:
                return user_session
        raise WebAdminError("forbidden")


type TargetRow = EnrollmentInvitationRow | DeviceRow | UserSessionRow


def _terminal(row: TargetRow, now: datetime) -> bool:
    if isinstance(row, EnrollmentInvitationRow):
        return row.cancelled_at is not None or row.consumed_at is not None or row.expires_at <= now
    return row.revoked_at is not None


def _terminal_at(row: TargetRow, now: datetime) -> datetime:
    if isinstance(row, EnrollmentInvitationRow):
        return (
            row.cancelled_at
            or row.consumed_at
            or (row.expires_at if row.expires_at <= now else now)
        )
    return row.revoked_at or now


def _result(operation_id: UUID, outcome: str, terminal_at: datetime) -> dict[str, object]:
    return {
        "operation_id": str(operation_id),
        "outcome": outcome,
        "terminal_at": terminal_at.isoformat(),
    }


__all__ = ("SqlAlchemyAdminCommandRepository",)
