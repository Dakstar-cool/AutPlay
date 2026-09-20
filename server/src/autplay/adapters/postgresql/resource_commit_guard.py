"""Nonblocking admission revalidation at the end of an existing upload transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    IoPermit,
    LocalBridgeClaim,
    ResourceAdmissionError,
)
from autplay.domain.resource_execution import ExecutionStatus

from .models.account import DeviceRow, UserAccountRow, UserSessionRow
from .models.audit import AuditEventRow
from .models.catalog import RecordingRow
from .models.identity import RecordingRedirectRow
from .models.library import LibraryEntryRow, UserTrackRefRow
from .models.resource_admission import (
    ResourceAdmissionRow,
    ResourceIoExecutionRow,
    ResourceIoPermitRow,
)
from .models.vault import UploadSessionRow
from .resource_upload_guard import matches_upload_execution


def require_device_upload_commit(
    session: Session,
    actor: Principal | LocalBridgeClaim,
    upload_id: UUID,
    permit: IoPermit,
    *,
    stopped: Callable[[], bool],
    execution: ExecutionStatus | None = None,
) -> None:
    """Caller performs no more filesystem work and immediately commits or rolls back.

    Vault may already hold its upload row. Every later row lock is SHARE NOWAIT;
    taking the global admission lock or opening a nested unit here would invert
    the normal lock order. SHARE also blocks non-key revocation/lease updates.
    This read-only guard never grants capacity or renews a permit.
    """
    if stopped() or permit.target_id != upload_id:
        raise ResourceAdmissionError("resource_io_stale")
    try:
        with session.no_autoflush:
            # NOWAIT covers row locks; bound table-lock waits and statement execution too.
            session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            mode = {"read": True, "nowait": True}
            account = session.get(
                UserAccountRow, actor.user_id, with_for_update=mode, populate_existing=True
            )
            device = session.get(
                DeviceRow, actor.device_id, with_for_update=mode, populate_existing=True
            )
            credential = (
                session.get(
                    UserSessionRow,
                    actor.session_id,
                    with_for_update=mode,
                    populate_existing=True,
                )
                if isinstance(actor, Principal)
                else None
            )
            bridge_grant = (
                session.scalar(
                    select(AuditEventRow.audit_event_id)
                    .where(
                        AuditEventRow.actor_user_id == actor.user_id,
                        AuditEventRow.action == "acquisition_bridge.enabled",
                        AuditEventRow.target_type == "device",
                        AuditEventRow.target_id == actor.device_id,
                        AuditEventRow.reason_code == "OWNER_AUTHORIZED_COMPLETED_DOWNLOAD_IMPORT",
                    )
                    .limit(1)
                    .with_for_update(read=True, nowait=True)
                )
                if isinstance(actor, LocalBridgeClaim)
                else None
            )
            operation = session.get(
                ResourceAdmissionRow,
                permit.fence.operation_id,
                with_for_update=mode,
                populate_existing=True,
            )
            row = session.get(
                ResourceIoPermitRow, permit.permit_id, with_for_update=mode, populate_existing=True
            )
            if execution is not None:
                _lock_execution(session, upload_id, permit, execution)
            upload_expiry = _lock_upload_target(session, actor, upload_id)
            now = session.scalar(select(func.clock_timestamp()))
            if (
                stopped()
                or not isinstance(now, datetime)
                or upload_expiry <= now
                or account is None
                or account.status != "ACTIVE"
                or account.deleted_at is not None
                or device is None
                or device.user_id != actor.user_id
                or device.revoked_at is not None
                or (
                    isinstance(actor, Principal)
                    and (
                        credential is None
                        or credential.user_id != actor.user_id
                        or credential.device_id != actor.device_id
                        or credential.revoked_at is not None
                        or credential.expires_at <= now
                    )
                )
                or (
                    isinstance(actor, LocalBridgeClaim)
                    and (account.role != "OWNER" or bridge_grant is None)
                )
                or operation is None
                or operation.authority_kind
                != ("DEVICE_SESSION" if isinstance(actor, Principal) else "LOCAL_BRIDGE")
                or operation.user_id != actor.user_id
                or operation.device_id != actor.device_id
                or operation.authority_generation != account.authority_generation
                or (
                    isinstance(actor, Principal)
                    and credential is not None
                    and (
                        operation.session_mode != credential.session_mode
                        or operation.session_family_id
                        != (
                            credential.family_id or credential.session_id
                            if credential.session_mode == "V2"
                            else credential.session_id
                        )
                    )
                )
                or (
                    isinstance(actor, LocalBridgeClaim)
                    and (
                        operation.session_mode is not None
                        or operation.session_family_id is not None
                        or operation.job_id is not None
                    )
                )
                or operation.kind != "TRANSFER"
                or operation.resource_type != "UPLOAD_INTENT"
                or operation.target_id != upload_id
                or operation.state != "ACTIVE"
                or operation.activation_id != permit.fence.activation_id
                or operation.generation != permit.fence.generation
                or operation.lease_until is None
                or operation.lease_until <= now
                or (
                    operation.claimed_at is None
                    and (operation.claim_until is None or operation.claim_until <= now)
                )
                or row is None
                or row.operation_id != permit.fence.operation_id
                or row.activation_id != permit.fence.activation_id
                or row.generation != permit.fence.generation
                or row.target_id != upload_id
                or row.expires_at <= now
            ):
                raise ResourceAdmissionError("resource_io_stale")
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"55P03", "57014"}:
            raise ResourceAdmissionError("resource_commit_busy") from None
        raise


def _lock_execution(
    session: Session, upload_id: UUID, permit: IoPermit, execution: ExecutionStatus
) -> None:
    row = session.get(
        ResourceIoExecutionRow,
        execution.ticket.execution_id,
        with_for_update={"read": True, "nowait": True},
        populate_existing=True,
    )
    if (
        (
            execution.ticket.permit.permit_id,
            execution.ticket.permit.fence,
            execution.ticket.permit.target_id,
        )
        != (permit.permit_id, permit.fence, permit.target_id)
        or execution.ticket.actual_target_id != upload_id
        or row is None
        or not matches_upload_execution(row, execution)
    ):
        raise ResourceAdmissionError("resource_io_stale")


def _lock_upload_target(
    session: Session, actor: Principal | LocalBridgeClaim, upload_id: UUID
) -> datetime:
    # Column projections preserve the caller's dirty received_size/chunk_count.
    upload = session.execute(
        select(UploadSessionRow.target_recording_id, UploadSessionRow.expires_at)
        .where(
            UploadSessionRow.upload_session_id == upload_id,
            UploadSessionRow.user_id == actor.user_id,
            UploadSessionRow.device_id == actor.device_id,
            UploadSessionRow.actor_kind == "DEVICE",
            UploadSessionRow.state == "OPEN",
        )
        .with_for_update(read=True, nowait=True)
    ).one_or_none()
    if upload is None:
        raise ResourceAdmissionError("resource_io_stale")
    recording_id, expires_at = upload
    if not isinstance(expires_at, datetime):
        raise ResourceAdmissionError("resource_io_stale")
    # Match PostgresVaultRuntime.authorize_target. One live path suffices, even
    # if the entry originally used to create this upload has since been removed.
    proof = session.scalar(
        select(LibraryEntryRow.library_entry_id)
        .join(
            UserTrackRefRow,
            UserTrackRefRow.user_track_ref_id == LibraryEntryRow.user_track_ref_id,
        )
        .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
        .where(
            LibraryEntryRow.user_id == actor.user_id,
            LibraryEntryRow.removed_at.is_(None),
            UserTrackRefRow.user_id == actor.user_id,
            UserTrackRefRow.deleted_at.is_(None),
            UserTrackRefRow.resolution_status == "RESOLVED",
            UserTrackRefRow.recording_id == recording_id,
            RecordingRow.deleted_at.is_(None),
        )
        .limit(1)
        .with_for_update(
            read=True, nowait=True, of=(LibraryEntryRow, UserTrackRefRow, RecordingRow)
        )
    )
    if proof is None:
        raise ResourceAdmissionError("resource_io_stale")
    # Catalog redirect writers lock Recording FOR UPDATE. Check absence with a
    # fresh READ COMMITTED snapshot after acquiring its SHARE lock.
    redirected = session.scalar(
        select(exists().where(RecordingRedirectRow.source_recording_id == recording_id))
    )
    if redirected:
        raise ResourceAdmissionError("resource_io_stale")
    return expires_at
