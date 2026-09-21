"""Lightweight shared lock and account resource gates for existing authority writers."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from autplay.domain.resource_admission import (
    AccountLimitOverride,
    AccountLimits,
    ResourceAdmissionError,
)

from .models.account import DeviceRow, UserSessionRow
from .models.resource_admission import (
    AccountQuotaOverrideRow,
    ResourceAdmissionRow,
    ResourceQuotaPolicyRow,
)

RESOURCE_ADMISSION_LOCK = 0x415554504C515401


def lock_resource_admission(session: Session) -> None:
    """Call before account/device/session/job locks; never hold it during network I/O."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RESOURCE_ADMISSION_LOCK})


def effective_account_limits(session: Session, user_id: UUID) -> AccountLimits:
    policy = session.get(ResourceQuotaPolicyRow, 1, populate_existing=True)
    if policy is None:
        raise ResourceAdmissionError()
    defaults = AccountLimits(
        policy.default_devices, policy.default_playbacks, policy.default_transfers
    )
    override = session.get(AccountQuotaOverrideRow, user_id, populate_existing=True)
    return (
        defaults
        if override is None
        else AccountLimitOverride(
            override.devices,
            override.playbacks,
            override.transfers,
        ).effective(defaults)
    )


def require_device_capacity(session: Session, user_id: UUID) -> None:
    """For a new binding after exact receipt replay; caller holds admission/account locks."""
    used = (
        session.scalar(
            select(func.count())
            .select_from(DeviceRow)
            .where(
                DeviceRow.user_id == user_id,
                DeviceRow.revoked_at.is_(None),
            )
        )
        or 0
    )
    if used >= effective_account_limits(session, user_id).devices:
        raise ResourceAdmissionError("account_device_limit_reached")


def terminate_resource_authority(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    device_id: UUID | None = None,
    family_id: UUID | None = None,
    session_mode: str | None = None,
    device_sessions_only: bool = False,
) -> None:
    """Withdraw grants/waiters while preserving charged permits until adapter acknowledgement."""
    statement = update(ResourceAdmissionRow).where(
        ResourceAdmissionRow.user_id == user_id,
        ResourceAdmissionRow.state.in_(("ACTIVE", "WAITING")),
    )
    if device_id is not None:
        statement = statement.where(ResourceAdmissionRow.device_id == device_id)
    if device_sessions_only:
        statement = statement.where(ResourceAdmissionRow.authority_kind == "DEVICE_SESSION")
    if family_id is not None:
        if session_mode not in {"LEGACY", "V2"}:
            raise ValueError("session mode required for family revocation")
        statement = statement.where(
            ResourceAdmissionRow.session_family_id == family_id,
            ResourceAdmissionRow.session_mode == session_mode,
        )
    session.execute(
        statement.values(
            state="EXPIRED",
            terminal_at=now,
            updated_at=func.greatest(ResourceAdmissionRow.updated_at, now),
        )
    )


def terminate_retired_session(session: Session, row: UserSessionRow, now: datetime) -> None:
    """After terminal revocation, retain a V2 family with a live rotated successor."""
    session.flush()
    family = (row.family_id or row.session_id) if row.session_mode == "V2" else row.session_id
    if row.session_mode == "V2":
        successor = session.scalar(
            select(UserSessionRow.session_id)
            .where(
                UserSessionRow.user_id == row.user_id,
                UserSessionRow.device_id == row.device_id,
                UserSessionRow.session_mode == "V2",
                func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id) == family,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
            )
            .limit(1)
        )
        if successor is not None:
            return
    terminate_resource_authority(
        session,
        row.user_id,
        now,
        device_id=row.device_id,
        family_id=family,
        session_mode=row.session_mode,
    )
