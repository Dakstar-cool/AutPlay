"""Current application/worker authority and exact resource ownership inside an admission UoW."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autplay.application.vault_uploads import VaultNotFoundError, VaultPrincipal
from autplay.domain.auth import Principal
from autplay.domain.jobs import LeaseFence
from autplay.domain.resource_admission import (
    AcquisitionClaim,
    AuthorityKind,
    LocalBridgeClaim,
    ResourceAdmissionError,
    ResourceAuthority,
    ResourceRequest,
)

from .acquisition_authority import require_acquisition_session
from .discovery_runtime import BulkDiscoveryError, PostgresBulkDiscoveryRepository
from .models import DeviceRow, UserAccountRow, UserSessionRow
from .models.audit import AuditEventRow
from .models.discovery import AcquisitionAttemptRow, DiscoveryCandidateRow
from .models.internet_music import InternetAcquisitionRow
from .models.jobs import JobRow
from .models.vault import AudioVariantRow, UploadSessionRow
from .vault_runtime import PostgresVaultRuntime


class ResourceAuthorityGate:
    def __init__(self, session: Session, *, automatic_acquisition_enabled: bool) -> None:
        self._s, self._automatic = session, automatic_acquisition_enabled

    def authenticate(
        self, actor: Principal, now: datetime, *, lock_rows: bool = True
    ) -> ResourceAuthority:
        # Enqueue already owns admission; taking FOR UPDATE before its sync-owner
        # lock would conflict with a publisher's FK KEY SHARE on this account.
        account_query = select(UserAccountRow).where(UserAccountRow.user_id == actor.user_id)
        device_query = select(DeviceRow).where(DeviceRow.device_id == actor.device_id)
        session_query = select(UserSessionRow).where(UserSessionRow.session_id == actor.session_id)
        if lock_rows:
            account_query = account_query.with_for_update()
            device_query = device_query.with_for_update()
            session_query = session_query.with_for_update()
        account = self._s.scalar(account_query.execution_options(populate_existing=True))
        device = self._s.scalar(device_query.execution_options(populate_existing=True))
        session = self._s.scalar(session_query.execution_options(populate_existing=True))
        now = self._now()
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or device is None
            or device.user_id != actor.user_id
            or device.revoked_at is not None
            or session is None
            or session.user_id != actor.user_id
            or session.device_id != actor.device_id
            or session.revoked_at is not None
            or session.expires_at <= now
            or session.session_mode not in {"LEGACY", "V2"}
        ):
            raise ResourceAdmissionError()
        return ResourceAuthority(
            actor.user_id,
            account.authority_generation,
            AuthorityKind.DEVICE_SESSION,
            actor.device_id,
            (session.family_id or session.session_id)
            if session.session_mode == "V2"
            else session.session_id,
            session.session_mode,
        )

    def authenticate_bridge(
        self, actor: LocalBridgeClaim, now: datetime, *, lock_rows: bool = True
    ) -> ResourceAuthority:
        """Derive local authority from an explicit, audited and revocable bridge device."""
        account_query = select(UserAccountRow).where(UserAccountRow.user_id == actor.user_id)
        device_query = select(DeviceRow).where(DeviceRow.device_id == actor.device_id)
        if lock_rows:
            account_query = account_query.with_for_update()
            device_query = device_query.with_for_update()
        account = self._s.scalar(account_query.execution_options(populate_existing=True))
        device = self._s.scalar(device_query.execution_options(populate_existing=True))
        grant = self._s.scalar(
            select(AuditEventRow.audit_event_id)
            .where(
                AuditEventRow.actor_user_id == actor.user_id,
                AuditEventRow.action == "acquisition_bridge.enabled",
                AuditEventRow.target_type == "device",
                AuditEventRow.target_id == actor.device_id,
                AuditEventRow.reason_code == "OWNER_AUTHORIZED_COMPLETED_DOWNLOAD_IMPORT",
            )
            .limit(1)
        )
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role != "OWNER"
            or device is None
            or device.user_id != actor.user_id
            or device.revoked_at is not None
            or grant is None
        ):
            raise ResourceAdmissionError()
        return ResourceAuthority(
            actor.user_id,
            account.authority_generation,
            AuthorityKind.LOCAL_BRIDGE,
            device_id=actor.device_id,
        )

    def require(self, authority: ResourceAuthority, now: datetime) -> None:
        self._require(authority, now, execution=True)

    def require_intent(
        self, authority: ResourceAuthority, request: ResourceRequest, now: datetime
    ) -> None:
        """Validate durable demand without claiming that its previous worker still executes."""
        self._require(authority, now, execution=False)
        job = self._s.get(JobRow, authority.job_id)
        internet = request.resource_type == "INTERNET_ACQUISITION"
        expected_type = "music.internet.acquire" if internet else "discovery.acquire"
        payload_key = "acquisition_id" if internet else "candidate_id"
        attempt = None if internet else self._s.get(AcquisitionAttemptRow, request.target_id)
        target = request.target_id if internet else (attempt.candidate_id if attempt else None)
        if (
            job is None
            or job.job_type != expected_type
            or job.schema_version != 1
            or not isinstance(job.payload, dict)
            or target is None
            or job.payload.get(payload_key) != str(target)
        ):
            raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
        self.target(authority, request, now, execution=False)

    def _require(self, authority: ResourceAuthority, now: datetime, *, execution: bool) -> None:
        job = None
        if authority.job_id is not None:
            job = self._s.scalar(
                select(JobRow)
                .where(JobRow.job_id == authority.job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        # Job locks can outlive either lease or source session. All expiry checks
        # below use a clock read after the lock, never the admission-lock timestamp.
        now = self._now()
        # The admission advisory lock serializes account/session authority writers. Queue
        # selection reads other accounts without acquiring their row locks out of UUID order.
        account = self._s.scalar(
            select(UserAccountRow)
            .where(UserAccountRow.user_id == authority.user_id)
            .execution_options(populate_existing=True)
        )
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.authority_generation != authority.authority_generation
        ):
            raise ResourceAdmissionError()
        if authority.authority_kind == AuthorityKind.DEVICE_SESSION:
            try:
                require_acquisition_session(
                    self._s,
                    user_id=authority.user_id,
                    device_id=authority.device_id,
                    family_id=authority.session_family_id,
                    mode=authority.session_mode,
                    now=now,
                )
            except ResourceAdmissionError as error:
                raise ResourceAdmissionError() from error
        elif authority.authority_kind == AuthorityKind.LOCAL_BRIDGE:
            if authority.device_id is None:
                raise ResourceAdmissionError()
            self.authenticate_bridge(
                LocalBridgeClaim(authority.user_id, authority.device_id), now, lock_rows=False
            )
        elif authority.authority_kind != AuthorityKind.SERVER_ACQUISITION:
            raise ResourceAdmissionError()
        if authority.job_id is not None:
            if (
                job is None
                or job.user_id != authority.user_id
                or job.state not in {"QUEUED", "RETRY_WAIT", "RUNNING", "PAUSED"}
                or job.cancel_requested_at is not None
                or (
                    execution
                    and (
                        job.state != "RUNNING"
                        or job.lease_owner != authority.job_worker_id
                        or job.attempt_count != authority.job_attempt
                        or job.lease_deadline is None
                        or job.lease_deadline <= now
                    )
                )
            ):
                raise ResourceAdmissionError()
        elif authority.authority_kind == AuthorityKind.SERVER_ACQUISITION:
            raise ResourceAdmissionError()
        if authority.authority_kind == AuthorityKind.SERVER_ACQUISITION:
            self._discovery(authority, execution=execution)

    def _now(self) -> datetime:
        now = self._s.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError()
        return now

    def authenticate_acquisition(self, actor: AcquisitionClaim, now: datetime) -> ResourceAuthority:
        """Only the exact current job claim can derive authority from an enqueue snapshot."""
        fence = actor.fence
        job = self._s.scalar(
            select(JobRow)
            .where(JobRow.job_id == fence.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        expected_type = (
            "music.internet.acquire"
            if actor.resource_type == "INTERNET_ACQUISITION"
            else "discovery.acquire"
        )
        if (
            job is None
            or job.job_type != expected_type
            or job.schema_version != 1
            or not isinstance(job.payload, dict)
        ):
            raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
        if actor.resource_type == "INTERNET_ACQUISITION":
            selection = self._s.get(
                InternetAcquisitionRow, actor.acquisition_id, populate_existing=True
            )
            if (
                selection is None
                or selection.job_id != fence.job_id
                or selection.authority_generation is None
                or selection.source_session_family_id is None
                or selection.source_session_mode not in {"LEGACY", "V2"}
                or job.payload.get("acquisition_id") != str(actor.acquisition_id)
            ):
                raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
            authority = ResourceAuthority(
                selection.user_id,
                selection.authority_generation,
                AuthorityKind.DEVICE_SESSION,
                selection.device_id,
                selection.source_session_family_id,
                selection.source_session_mode,
                fence.job_id,
                fence.worker_id,
                fence.attempt_no,
            )
        else:
            attempt = self._s.get(
                AcquisitionAttemptRow, actor.acquisition_id, populate_existing=True
            )
            if (
                attempt is None
                or attempt.authority_generation is None
                or attempt.job_id != fence.job_id
            ):
                raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
            candidate = self._s.get(
                DiscoveryCandidateRow, attempt.candidate_id, populate_existing=True
            )
            if candidate is None or job.payload.get("candidate_id") != str(candidate.candidate_id):
                raise ResourceAdmissionError("resource_acquisition_authority_unavailable")
            authority = ResourceAuthority(
                candidate.user_id,
                attempt.authority_generation,
                AuthorityKind.SERVER_ACQUISITION,
                job_id=fence.job_id,
                job_worker_id=fence.worker_id,
                job_attempt=fence.attempt_no,
                acquisition_attempt_id=attempt.acquisition_attempt_id,
                source_authorization_id=attempt.source_authorization_id,
                source_authorization_revision=attempt.source_authorization_revision,
                policy_id=attempt.policy_id,
                policy_revision=attempt.policy_revision,
            )
        self.require(authority, now)
        return authority

    def _discovery(self, authority: ResourceAuthority, *, execution: bool = True) -> None:
        attempt = self._s.scalar(
            select(AcquisitionAttemptRow)
            .where(
                AcquisitionAttemptRow.acquisition_attempt_id == authority.acquisition_attempt_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            attempt is None
            or attempt.authority_generation != authority.authority_generation
            or attempt.job_id != authority.job_id
            or authority.job_worker_id is None
            or authority.job_attempt is None
            or authority.job_id is None
            or attempt.state not in {"QUEUED", "RUNNING"}
            or (
                attempt.source_authorization_id,
                attempt.source_authorization_revision,
                attempt.policy_id,
                attempt.policy_revision,
            )
            != (
                authority.source_authorization_id,
                authority.source_authorization_revision,
                authority.policy_id,
                authority.policy_revision,
            )
        ):
            raise ResourceAdmissionError()
        try:
            if not execution:
                PostgresBulkDiscoveryRepository(self._s).require_ingest_boundary(
                    candidate_id=attempt.candidate_id,
                    owner_user_id=authority.user_id,
                    acquisition_attempt_id=attempt.acquisition_attempt_id,
                    automatic_enabled=self._automatic,
                )
                return
            PostgresBulkDiscoveryRepository(self._s).require_before_acquire(
                candidate_id=attempt.candidate_id,
                owner_user_id=authority.user_id,
                acquisition_attempt_id=attempt.acquisition_attempt_id,
                fence=LeaseFence(authority.job_id, authority.job_worker_id, authority.job_attempt),
                automatic_enabled=self._automatic,
            )
        except BulkDiscoveryError as error:
            raise ResourceAdmissionError() from error

    def recording(self, authority: ResourceAuthority, recording_id: UUID) -> None:
        if authority.device_id is None or not PostgresVaultRuntime(self._s).authorize_target(
            VaultPrincipal(authority.user_id, authority.device_id),
            recording_id,
        ):
            raise ResourceAdmissionError("resource_target_unavailable")

    def stream_recording(self, authority: ResourceAuthority, audio_variant_id: UUID) -> UUID:
        if authority.device_id is None:
            raise ResourceAdmissionError("resource_target_unavailable")
        try:
            PostgresVaultRuntime(self._s).resolve_stream(
                VaultPrincipal(authority.user_id, authority.device_id), audio_variant_id
            )
        except VaultNotFoundError as error:
            raise ResourceAdmissionError("resource_target_unavailable") from error
        recording_id = self._s.scalar(
            select(AudioVariantRow.recording_id).where(
                AudioVariantRow.audio_variant_id == audio_variant_id
            )
        )
        if recording_id is None:
            raise ResourceAdmissionError("resource_target_unavailable")
        return recording_id

    def target(
        self,
        authority: ResourceAuthority,
        request: ResourceRequest,
        now: datetime,
        *,
        execution: bool = True,
    ) -> None:
        if request.resource_type == "PLAY_INSTANCE":
            return
        if request.target_id is None:
            raise ResourceAdmissionError("resource_target_unavailable")
        if request.resource_type == "DISCOVERY_ACQUISITION":
            if (
                authority.authority_kind != AuthorityKind.SERVER_ACQUISITION
                or authority.acquisition_attempt_id != request.target_id
            ):
                raise ResourceAdmissionError("resource_target_unavailable")
            self._discovery(authority, execution=execution)
            eligible = self._s.scalar(
                select(DiscoveryCandidateRow.candidate_id)
                .join(
                    AcquisitionAttemptRow,
                    AcquisitionAttemptRow.candidate_id == DiscoveryCandidateRow.candidate_id,
                )
                .where(
                    AcquisitionAttemptRow.acquisition_attempt_id == request.target_id,
                    DiscoveryCandidateRow.acquisition_state.in_(
                        ("QUEUED", "ACQUIRING", "RETRY_WAIT")
                    ),
                    ~select(UploadSessionRow.upload_session_id)
                    .where(
                        UploadSessionRow.source_acquisition_attempt_id == request.target_id,
                        UploadSessionRow.actor_kind == "PROVIDER",
                    )
                    .exists(),
                )
            )
            if eligible is None:
                raise ResourceAdmissionError("resource_target_unavailable")
            return
        if authority.device_id is None:
            raise ResourceAdmissionError("resource_target_unavailable")
        if request.resource_type == "INTERNET_ACQUISITION":
            row = self._s.scalar(
                select(InternetAcquisitionRow)
                .where(
                    InternetAcquisitionRow.acquisition_id == request.target_id,
                    InternetAcquisitionRow.user_id == authority.user_id,
                    InternetAcquisitionRow.device_id == authority.device_id,
                    InternetAcquisitionRow.job_id == authority.job_id,
                    InternetAcquisitionRow.authority_generation == authority.authority_generation,
                    InternetAcquisitionRow.source_session_family_id == authority.session_family_id,
                    InternetAcquisitionRow.source_session_mode == authority.session_mode,
                    InternetAcquisitionRow.state.in_(("QUEUED", "DOWNLOADING", "UPLOADING")),
                    InternetAcquisitionRow.upload_id.is_(None),
                    ~select(UploadSessionRow.upload_session_id)
                    .where(
                        UploadSessionRow.source_internet_acquisition_id == request.target_id,
                        UploadSessionRow.actor_kind == "INTERNET",
                    )
                    .exists(),
                )
                .execution_options(populate_existing=True)
            )
            if row is None or authority.job_id is None:
                raise ResourceAdmissionError("resource_target_unavailable")
            return
        principal = VaultPrincipal(authority.user_id, authority.device_id)
        if request.resource_type == "DOWNLOAD_INTENT":
            try:
                PostgresVaultRuntime(self._s).resolve_stream(principal, request.target_id)
            except VaultNotFoundError as error:
                raise ResourceAdmissionError("resource_target_unavailable") from error
            return
        if request.resource_type == "UPLOAD_INTENT":
            upload = self._s.scalar(
                select(UploadSessionRow)
                .where(
                    UploadSessionRow.upload_session_id == request.target_id,
                    UploadSessionRow.user_id == authority.user_id,
                    UploadSessionRow.device_id == authority.device_id,
                    UploadSessionRow.actor_kind == "DEVICE",
                    UploadSessionRow.state == "OPEN",
                    UploadSessionRow.expires_at > now,
                )
                .execution_options(populate_existing=True)
            )
            if upload is not None:
                if authority.authority_kind not in {
                    AuthorityKind.DEVICE_SESSION,
                    AuthorityKind.LOCAL_BRIDGE,
                }:
                    raise ResourceAdmissionError("resource_target_unavailable")
                self.recording(authority, upload.target_recording_id)
                return
        raise ResourceAdmissionError("resource_target_unavailable")
