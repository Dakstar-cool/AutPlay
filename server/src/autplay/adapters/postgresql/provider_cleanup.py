"""Claim abandoned provider files without confusing lease expiry with process exit."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.provider_cleanup import ProviderCleanupClaim, provider_cleanup_id
from autplay.domain.resource_admission import ResourceAdmissionError

from .acquisition_authority import acquisition_generation, require_acquisition_session
from .discovery_runtime import BulkDiscoveryError, PostgresBulkDiscoveryRepository
from .models import (
    AcquisitionAttemptRow,
    DiscoveryCandidateRow,
    InternetAcquisitionRow,
    JobRow,
    ProviderStagingRow,
    UploadSessionRow,
)
from .models.resource_admission import ResourceIoExecutionRow
from .resource_limits import lock_resource_admission


class PostgresProviderCleanupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("provider_cleanup_unavailable")
        return now

    @staticmethod
    def _receipt(row: ProviderStagingRow) -> ProviderCleanupClaim:
        if row.cleanup_claim_id is None:
            raise ResourceAdmissionError("provider_cleanup_conflict")
        return ProviderCleanupClaim(row.execution_id, row.cleanup_claim_id, row.state == "CLEANED")

    @staticmethod
    def _protected(session: Session, row: ProviderStagingRow) -> bool:
        return bool(
            session.scalar(
                select(
                    exists().where(
                        UploadSessionRow.staging_key == row.staging_key,
                    )
                )
            )
            or session.scalar(
                select(
                    exists().where(
                        ResourceIoExecutionRow.execution_id == row.execution_id,
                        ResourceIoExecutionRow.closed_at.is_(None),
                    )
                )
            )
        )

    def claim(self, execution_id: UUID) -> ProviderCleanupClaim | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            job_id = session.scalar(
                select(ProviderStagingRow.job_id).where(
                    ProviderStagingRow.execution_id == execution_id
                )
            )
            if job_id is None:
                return None
            job = session.scalar(select(JobRow).where(JobRow.job_id == job_id).with_for_update())
            row = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == execution_id)
                .with_for_update()
            )
            if row is None or job is None or row.state in {"OWNED", "HANDED_OFF"}:
                return None
            if row.closed_at is None or self._protected(session, row):
                return None
            if row.state in {"CLEANUP_CLAIMED", "CLEANED"}:
                return self._receipt(row)
            if row.state not in {"EXITED", "SEALED"}:
                return None
            reason = self._reason(session, row, job)
            if reason is None:
                return None
            now = self._now(session)
            row.state, row.updated_at = "CLEANUP_CLAIMED", now
            row.cleanup_reason, row.cleanup_claim_id = reason, provider_cleanup_id(execution_id)
            row.cleanup_claimed_at = now
            session.flush()
            return self._receipt(row)

    def complete(self, claim: ProviderCleanupClaim) -> None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == claim.execution_id)
                .with_for_update()
            )
            if (
                row is None
                or row.state not in {"CLEANUP_CLAIMED", "CLEANED"}
                or row.cleanup_claim_id != claim.claim_id
                or row.staging_key != claim.staging_key.value
                or self._protected(session, row)
            ):
                raise ResourceAdmissionError("provider_cleanup_conflict")
            if row.state != "CLEANED":
                now = self._now(session)
                row.state, row.cleaned_at, row.updated_at = "CLEANED", now, now

    def _reason(self, session: Session, row: ProviderStagingRow, job: JobRow) -> str | None:
        if job.user_id != row.user_id:
            return None
        if job.cancel_requested_at is not None or job.state == "CANCELLED":
            return "CANCELLED"
        if job.state == "FAILED" or row.closure_kind == "NOT_STARTED" or row.exit_code != 0:
            return "FAILED"
        if (
            job.attempt_count > row.job_attempt
            or (job.state == "RUNNING" and job.lease_owner != row.job_worker_id)
            or job.state == "COMPLETED"
        ):
            return "SUPERSEDED"
        if row.resource_type == "INTERNET_ACQUISITION":
            source = session.get(InternetAcquisitionRow, row.acquisition_id)
            if source is None or source.user_id != row.user_id or source.job_id != row.job_id:
                return None
            if source.upload_id is not None:
                return None
            if source.state == "FAILED":
                return "FAILED"
            now = self._now(session)
            try:
                if acquisition_generation(session, row.user_id) != source.authority_generation:
                    return "AUTHORITY_REVOKED"
                require_acquisition_session(
                    session,
                    user_id=row.user_id,
                    device_id=source.device_id,
                    family_id=source.source_session_family_id,
                    mode=source.source_session_mode,
                    now=now,
                )
            except ResourceAdmissionError:
                return "AUTHORITY_REVOKED"
        elif row.resource_type == "DISCOVERY_ACQUISITION":
            attempt = session.get(AcquisitionAttemptRow, row.acquisition_id)
            if attempt is None or attempt.job_id != row.job_id:
                return None
            candidate = session.get(DiscoveryCandidateRow, attempt.candidate_id)
            if candidate is None or candidate.user_id != row.user_id:
                return None
            if (
                candidate.current_acquisition_attempt_id != row.acquisition_id
                or candidate.job_id != row.job_id
            ):
                return "SUPERSEDED"
            if attempt.state == "CANCELLED":
                return "CANCELLED"
            if attempt.state == "FAILED":
                return "FAILED"
            try:
                PostgresBulkDiscoveryRepository(session).require_persistent_acquisition_authority(
                    candidate_id=attempt.candidate_id,
                    owner_user_id=row.user_id,
                    acquisition_attempt_id=attempt.acquisition_attempt_id,
                    authority_now=lambda: self._now(session),
                )
            except BulkDiscoveryError:
                return "AUTHORITY_REVOKED"
        return None
