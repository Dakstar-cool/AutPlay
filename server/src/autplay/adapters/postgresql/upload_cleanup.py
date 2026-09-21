"""Short cleanup transactions never interpret a job lease as writer exit."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.upload_cleanup import UploadCleanupClaim
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import OpaqueStorageKey

from .models.provider_maintenance import ProviderMaintenanceRow
from .models.upload_cleanup import UploadCleanupClaimRow
from .models.vault import UploadSessionRow
from .resource_limits import lock_resource_admission
from .resource_upload_guard import upload_has_unclosed_writer


def eligible_upload(row: UploadSessionRow) -> bool:
    return (
        row.actor_kind == "DEVICE"
        and row.state in {"CANCELLED", "EXPIRED"}
        and row.vault_object_id is None
        and row.audio_variant_id is None
        and row.computed_sha256 is None
        and row.source_candidate_id is None
        and row.source_acquisition_attempt_id is None
        and row.source_internet_acquisition_id is None
    )


def queue_upload_cleanup(session: Session, row: UploadSessionRow) -> UploadCleanupClaimRow:
    """Caller holds the upload row lock; the intent commits with its terminal state.

    Do not acquire the global lock inside this transaction. Execution eligibility
    is checked separately in global-admission then upload-row order before GO.
    """
    if not eligible_upload(row):
        raise ResourceAdmissionError("upload_cleanup_conflict")
    claim = session.get(UploadCleanupClaimRow, row.upload_session_id)
    if claim is None:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("upload_cleanup_unavailable")
        claim = UploadCleanupClaimRow(
            claim_id=row.upload_session_id,
            storage_key=row.staging_key,
            terminal_state=row.state,
            created_at=now,
        )
        session.add(claim)
        session.flush()
    if claim.storage_key != row.staging_key or claim.terminal_state != row.state:
        raise ResourceAdmissionError("upload_cleanup_conflict")
    return claim


def require_cleanup_target(session: Session, claim_id: UUID, key: str) -> UploadCleanupClaimRow:
    """Called after the global lock; retain the upload row lock to transaction end."""
    upload = session.get(UploadSessionRow, claim_id, with_for_update=True)
    row = session.get(UploadCleanupClaimRow, claim_id, with_for_update=True)
    if (
        upload is None
        or row is None
        or not eligible_upload(upload)
        or row.storage_key != key
        or upload.staging_key != key
        or row.terminal_state != upload.state
        or upload_has_unclosed_writer(session, claim_id)
    ):
        raise ResourceAdmissionError("upload_cleanup_conflict")
    return row


class PostgresUploadCleanupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _receipt(row: UploadCleanupClaimRow) -> UploadCleanupClaim:
        return UploadCleanupClaim(
            row.claim_id, OpaqueStorageKey(row.storage_key), row.completed_at is not None
        )

    def claim(self, upload_id: UUID) -> UploadCleanupClaim | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            upload = session.get(UploadSessionRow, upload_id, with_for_update=True)
            if (
                upload is None
                or not eligible_upload(upload)
                or upload_has_unclosed_writer(session, upload_id)
            ):
                return None
            return self._receipt(queue_upload_cleanup(session, upload))

    def pending(self, *, maximum: int = 100, after: UUID | None = None) -> tuple[UUID, ...]:
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ValueError("upload_cleanup_limit_invalid")
        statement = (
            select(UploadSessionRow.upload_session_id)
            .outerjoin(
                UploadCleanupClaimRow,
                UploadCleanupClaimRow.claim_id == UploadSessionRow.upload_session_id,
            )
            .where(
                UploadSessionRow.actor_kind == "DEVICE",
                UploadSessionRow.state.in_(("CANCELLED", "EXPIRED")),
                UploadSessionRow.vault_object_id.is_(None),
                UploadSessionRow.audio_variant_id.is_(None),
                UploadSessionRow.computed_sha256.is_(None),
                UploadSessionRow.source_candidate_id.is_(None),
                UploadSessionRow.source_acquisition_attempt_id.is_(None),
                UploadSessionRow.source_internet_acquisition_id.is_(None),
                UploadCleanupClaimRow.completed_at.is_(None),
            )
        )
        if after is not None:
            statement = statement.where(UploadSessionRow.upload_session_id > after)
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    statement.order_by(UploadSessionRow.upload_session_id).limit(maximum)
                )
            )

    def complete(self, claim: UploadCleanupClaim, execution_id: UUID) -> None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = require_cleanup_target(session, claim.claim_id, claim.storage_key.value)
            if row.completed_at is not None:
                if row.completed_execution_id != execution_id:
                    raise ResourceAdmissionError("upload_cleanup_conflict")
                return
            run = session.get(ProviderMaintenanceRow, execution_id)
            if (
                run is None
                or run.action != "UPLOAD_CLEANUP"
                or run.upload_claim_id != claim.claim_id
                or run.storage_key != row.storage_key
                or run.state != "CLOSED"
                or run.closure_kind != "PROCESS_EXIT"
                or run.exit_code != 0
                or run.child_pid is None
                or run.closed_at is None
                or session.scalar(
                    select(
                        exists().where(
                            ProviderMaintenanceRow.upload_claim_id == claim.claim_id,
                            ProviderMaintenanceRow.closed_at.is_(None),
                        )
                    )
                )
            ):
                raise ResourceAdmissionError("upload_cleanup_execution_unconfirmed")
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ResourceAdmissionError("upload_cleanup_unavailable")
            row.completed_at, row.completed_execution_id = now, execution_id
