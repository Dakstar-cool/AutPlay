"""Revoke an account's authority without retiring its durable social/library data."""

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models.account import DeviceRow, UserAccountRow, UserSessionRow
from .models.profile_pairing import (
    DeviceAdmissionRow,
    EnrollmentInvitationRow,
    TrustedDeviceKeyRow,
    TrustedDeviceReenrollmentChallengeRow,
)
from .models.public_access import AccountInvitationRow
from .models.self_device_pairing import SelfDevicePairingRow
from .models.web_admin import WebSessionInvitationRow, WebSessionRow
from .models.web_passkeys import WebPasskeyRow
from .resource_limits import terminate_resource_authority


def revoke_account_authority(session: Session, account: UserAccountRow, now: datetime) -> None:
    """Caller holds identity -> admission -> sync owner -> account locks, in that order."""
    user_id = account.user_id
    account.authority_generation += 1
    account.updated_at = now
    # Keep ACTIVE: account retirement triggers deliberately remove social state.
    for model in (DeviceRow, UserSessionRow, WebSessionRow, WebPasskeyRow):
        session.execute(
            update(model)
            .where(model.user_id == user_id, model.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    session.execute(
        update(TrustedDeviceKeyRow)
        .where(TrustedDeviceKeyRow.user_id == user_id, TrustedDeviceKeyRow.removed_at.is_(None))
        .values(removed_at=now, revision=TrustedDeviceKeyRow.revision + 1)
    )
    session.execute(
        update(TrustedDeviceReenrollmentChallengeRow)
        .where(
            TrustedDeviceReenrollmentChallengeRow.user_id == user_id,
            TrustedDeviceReenrollmentChallengeRow.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.execute(
        update(EnrollmentInvitationRow)
        .where(
            or_(
                EnrollmentInvitationRow.user_id == user_id,
                EnrollmentInvitationRow.issued_by_user_id == user_id,
            ),
            EnrollmentInvitationRow.consumed_at.is_(None),
            EnrollmentInvitationRow.cancelled_at.is_(None),
        )
        .values(cancelled_at=now)
    )
    session.execute(
        update(WebSessionInvitationRow)
        .where(
            WebSessionInvitationRow.user_id == user_id,
            WebSessionInvitationRow.consumed_at.is_(None),
            WebSessionInvitationRow.cancelled_at.is_(None),
        )
        .values(cancelled_at=now)
    )
    session.execute(
        update(AccountInvitationRow)
        .where(
            AccountInvitationRow.issued_by_user_id == user_id,
            AccountInvitationRow.consumed_at.is_(None),
            AccountInvitationRow.cancelled_at.is_(None),
        )
        .values(cancelled_at=now)
    )
    browser_ids = select(WebSessionRow.web_session_id).where(WebSessionRow.user_id == user_id)
    session.execute(
        update(DeviceAdmissionRow)
        .where(
            or_(
                (DeviceAdmissionRow.state == "APPROVED")
                & (DeviceAdmissionRow.approved_user_id == user_id),
                (DeviceAdmissionRow.state == "PENDING")
                & DeviceAdmissionRow.review_web_session_id.in_(browser_ids),
            )
        )
        .values(state="CANCELLED", approved_user_id=None, decided_at=now)
    )
    session.execute(
        update(SelfDevicePairingRow)
        .where(
            SelfDevicePairingRow.user_id == user_id,
            SelfDevicePairingRow.state.in_(("OPEN", "CLAIMED", "APPROVED")),
        )
        .values(state="CANCELLED", revision=SelfDevicePairingRow.revision + 1)
    )
    # These grants become unusable immediately. Retained executions still own their
    # charged capacity until the process adapter acknowledges exact tree exit.
    terminate_resource_authority(session, user_id, now)
