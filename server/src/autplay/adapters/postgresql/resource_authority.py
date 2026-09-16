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
    AuthorityKind,
    ResourceAdmissionError,
    ResourceAuthority,
    ResourceRequest,
)

from .discovery_runtime import BulkDiscoveryError, PostgresBulkDiscoveryRepository
from .models import DeviceRow, UserAccountRow, UserSessionRow
from .models.discovery import AcquisitionAttemptRow
from .models.internet_music import InternetAcquisitionRow
from .models.jobs import JobRow
from .models.vault import AudioVariantRow, UploadSessionRow
from .vault_runtime import PostgresVaultRuntime


class ResourceAuthorityGate:
    def __init__(self, session: Session, *, automatic_acquisition_enabled: bool) -> None:
        self._s, self._automatic = session, automatic_acquisition_enabled

    def authenticate(self, actor: Principal, now: datetime) -> ResourceAuthority:
        account = self._s.scalar(
            select(UserAccountRow)
            .where(UserAccountRow.user_id == actor.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        device = self._s.scalar(
            select(DeviceRow)
            .where(DeviceRow.device_id == actor.device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        session = self._s.scalar(
            select(UserSessionRow)
            .where(UserSessionRow.session_id == actor.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
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

    def require(self, authority: ResourceAuthority, now: datetime) -> None:
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
            active = self._s.scalar(
                select(UserSessionRow.session_id)
                .join(DeviceRow, DeviceRow.device_id == UserSessionRow.device_id)
                .where(
                    UserSessionRow.user_id == authority.user_id,
                    UserSessionRow.device_id == authority.device_id,
                    DeviceRow.user_id == authority.user_id,
                    DeviceRow.revoked_at.is_(None),
                    UserSessionRow.session_mode == authority.session_mode,
                    UserSessionRow.revoked_at.is_(None),
                    UserSessionRow.expires_at > now,
                    (
                        func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id)
                        if authority.session_mode == "V2"
                        else UserSessionRow.session_id
                    )
                    == authority.session_family_id,
                )
                .limit(1)
            )
            if active is None:
                raise ResourceAdmissionError()
        elif authority.authority_kind != AuthorityKind.SERVER_ACQUISITION:
            raise ResourceAdmissionError()
        if authority.job_id is not None:
            job = self._s.scalar(
                select(JobRow)
                .where(JobRow.job_id == authority.job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                job is None
                or job.user_id != authority.user_id
                or job.state != "RUNNING"
                or job.lease_owner != authority.job_worker_id
                or job.attempt_count != authority.job_attempt
                or job.cancel_requested_at is not None
                or job.lease_deadline is None
                or job.lease_deadline <= now
            ):
                raise ResourceAdmissionError()
        elif authority.authority_kind == AuthorityKind.SERVER_ACQUISITION:
            raise ResourceAdmissionError()
        if authority.authority_kind == AuthorityKind.SERVER_ACQUISITION:
            self._discovery(authority)

    def _discovery(self, authority: ResourceAuthority) -> None:
        attempt = self._s.scalar(
            select(AcquisitionAttemptRow)
            .where(
                AcquisitionAttemptRow.acquisition_attempt_id == authority.acquisition_attempt_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            attempt is None
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

    def target(self, authority: ResourceAuthority, request: ResourceRequest, now: datetime) -> None:
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
            self._discovery(authority)
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
                    InternetAcquisitionRow.state.in_(("QUEUED", "DOWNLOADING", "UPLOADING")),
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
                self.recording(authority, upload.target_recording_id)
                return
        raise ResourceAdmissionError("resource_target_unavailable")
