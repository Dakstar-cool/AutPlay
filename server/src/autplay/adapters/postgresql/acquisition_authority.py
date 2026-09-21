"""Enqueue-time generation snapshot under the caller's admission lock."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autplay.domain.resource_admission import ResourceAdmissionError

from .models import DeviceRow, UserAccountRow, UserSessionRow


def acquisition_generation(session: Session, user_id: UUID) -> int:
    """Caller locks admission BEFORE any job/candidate/source/owner transaction lock."""
    generation = session.scalar(
        select(UserAccountRow.authority_generation).where(
            UserAccountRow.user_id == user_id,
            UserAccountRow.status == "ACTIVE",
            UserAccountRow.deleted_at.is_(None),
        )
    )
    if generation is None:
        raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
    return generation


def require_acquisition_session(
    session: Session,
    *,
    user_id: UUID,
    device_id: UUID | None,
    family_id: UUID | None,
    mode: str | None,
    now: datetime,
) -> None:
    """Keep the original device/family live across lawful same-family rotation."""
    if device_id is None or family_id is None or mode not in {"LEGACY", "V2"}:
        raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
    active = session.scalar(
        select(UserSessionRow.session_id)
        .join(DeviceRow, DeviceRow.device_id == UserSessionRow.device_id)
        .where(
            UserSessionRow.user_id == user_id,
            UserSessionRow.device_id == device_id,
            DeviceRow.user_id == user_id,
            DeviceRow.revoked_at.is_(None),
            UserSessionRow.session_mode == mode,
            UserSessionRow.revoked_at.is_(None),
            UserSessionRow.expires_at > now,
            (
                func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id)
                if mode == "V2"
                else UserSessionRow.session_id
            )
            == family_id,
        )
        .limit(1)
    )
    if active is None:
        raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
