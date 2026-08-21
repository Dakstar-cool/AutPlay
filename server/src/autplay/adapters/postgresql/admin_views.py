"""SQLAlchemy read adapter for the first bounded M6 administration surfaces."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.account import DeviceRow, UserSessionRow
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.importing import ImportEntryRow, ImportJobRow
from autplay.adapters.postgresql.models.jobs import JobRow
from autplay.adapters.postgresql.models.profile_pairing import (
    EnrollmentInvitationRow,
    ServerInstanceRow,
)
from autplay.adapters.postgresql.models.vault import (
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.domain.admin_views import (
    AdminAuditItem,
    AdminConfirmationTarget,
    AdminDashboard,
    AdminDeviceItem,
    AdminImportItem,
    AdminInvitationItem,
    AdminJobItem,
    AdminPage,
    AdminReviewItem,
    AdminSessionItem,
    AdminUnavailable,
    AdminVaultStatus,
)
from autplay.domain.web_admin import WebActor, WebAdminError


class PostgreSqlAdminViews:
    def __init__(self, session: Session) -> None:
        self._session = session

    def dashboard(self, actor: WebActor) -> AdminDashboard:
        row = self._session.scalar(
            select(ServerInstanceRow).where(
                ServerInstanceRow.server_instance_id == actor.server_instance_id
            )
        )
        if row is None:
            return AdminDashboard(
                "Unavailable", False, 0, False, postgresql_ready=True, vault_status="UNKNOWN"
            )
        vault = self.vault(actor)
        vault_status = (
            "DEGRADED"
            if vault.unhealthy_replicas or vault.quarantined_objects or vault.uploads_quarantined
            else "HEALTHY"
        )
        return AdminDashboard(
            row.label_hint,
            True,
            row.capability_revision,
            False,
            postgresql_ready=True,
            worker_status="UNKNOWN",
            vault_status=vault_status,
        )

    def confirmation_target(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget:
        if action == "logout-current" and target_id == actor.web_session_id:
            return AdminConfirmationTarget(target_id, "CURRENT_BROWSER_SESSION")
        if action == "logout-all" and target_id == actor.user_id:
            return AdminConfirmationTarget(target_id, "ALL_BROWSER_SESSIONS")
        if action == "device":
            device_row = self._session.scalar(
                select(DeviceRow).where(
                    DeviceRow.device_id == target_id, DeviceRow.user_id == actor.user_id
                )
            )
            if device_row is not None:
                return AdminConfirmationTarget(target_id, "ANDROID_DEVICE", device_row.device_name)
        elif action == "invitation":
            invitation_row = self._session.scalar(
                select(EnrollmentInvitationRow).where(
                    EnrollmentInvitationRow.invitation_id == target_id,
                    EnrollmentInvitationRow.user_id == actor.user_id,
                )
            )
            if invitation_row is not None:
                return AdminConfirmationTarget(target_id, "ENROLLMENT_INVITATION")
        elif action == "session":
            session_row = self._session.scalar(
                select(UserSessionRow).where(
                    UserSessionRow.session_id == target_id,
                    UserSessionRow.user_id == actor.user_id,
                )
            )
            if session_row is not None:
                device = self._session.get(DeviceRow, session_row.device_id)
                return AdminConfirmationTarget(
                    target_id,
                    "ANDROID_SESSION",
                    device.device_name if device is not None else None,
                )
        elif action == "browser-session":
            browser_row = self._session.scalar(
                select(WebSessionRow).where(
                    WebSessionRow.web_session_id == target_id,
                    WebSessionRow.user_id == actor.user_id,
                    WebSessionRow.server_instance_id == actor.server_instance_id,
                )
            )
            if browser_row is not None:
                return AdminConfirmationTarget(target_id, "BROWSER_SESSION")
        raise WebAdminError("forbidden")

    def devices(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = select(DeviceRow).where(DeviceRow.user_id == actor.user_id)
        if after is not None:
            query = query.where(DeviceRow.device_id < _cursor(after))
        rows = self._session.scalars(
            query.order_by(DeviceRow.device_id.desc()).limit(limit + 1)
        ).all()
        items = tuple(
            AdminDeviceItem(row.device_id, row.device_name, row.platform, row.created_at)
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].device_id) if len(rows) > limit else None)

    def invitations(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = select(EnrollmentInvitationRow).where(
            EnrollmentInvitationRow.user_id == actor.user_id
        )
        if after is not None:
            query = query.where(EnrollmentInvitationRow.invitation_id < _cursor(after))
        rows = self._session.scalars(
            query.order_by(EnrollmentInvitationRow.invitation_id.desc()).limit(limit + 1)
        ).all()
        items = tuple(
            AdminInvitationItem(
                row.invitation_id,
                row.issued_at,
                row.expires_at,
                row.cancelled_at is not None or row.consumed_at is not None,
            )
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].invitation_id) if len(rows) > limit else None)

    def audit(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = select(AuditEventRow).where(
            or_(
                AuditEventRow.actor_user_id == actor.user_id,
                AuditEventRow.target_id == actor.user_id,
            )
        )
        if after is not None:
            query = query.where(AuditEventRow.audit_event_id < _cursor(after))
        rows = self._session.scalars(
            query.order_by(AuditEventRow.audit_event_id.desc()).limit(limit + 1)
        ).all()
        items = tuple(
            AdminAuditItem(row.occurred_at, row.action, row.target_type, row.reason_code)
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].audit_event_id) if len(rows) > limit else None)

    def sessions(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        cursor = _cursor(after) if after is not None else None
        query = select(UserSessionRow).where(UserSessionRow.user_id == actor.user_id)
        web_query = select(WebSessionRow).where(WebSessionRow.user_id == actor.user_id)
        if cursor is not None:
            query = query.where(UserSessionRow.session_id < cursor)
            web_query = web_query.where(WebSessionRow.web_session_id < cursor)
        android_rows = self._session.scalars(
            query.order_by(UserSessionRow.session_id.desc()).limit(limit + 1)
        ).all()
        web_rows = self._session.scalars(
            web_query.order_by(WebSessionRow.web_session_id.desc()).limit(limit + 1)
        ).all()
        items = [
            AdminSessionItem(
                row.session_id,
                f"ANDROID_{row.session_mode}",
                "REVOKED" if row.revoked_at else "ACTIVE",
                row.issued_at,
                row.expires_at,
                False,
            )
            for row in android_rows
        ]
        items.extend(
            AdminSessionItem(
                row.web_session_id,
                "BROWSER",
                "REVOKED" if row.revoked_at else "ACTIVE",
                row.issued_at,
                row.absolute_expires_at,
                row.web_session_id == actor.web_session_id,
            )
            for row in web_rows
        )
        items.sort(key=lambda item: item.session_id, reverse=True)
        page = items[:limit]
        next_after = str(page[-1].session_id) if len(items) > limit else None
        return AdminPage(tuple(page), next_after)

    def jobs(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = select(JobRow).where(JobRow.user_id == actor.user_id)
        if after is not None:
            query = query.where(JobRow.job_id < _cursor(after))
        rows = self._session.scalars(query.order_by(JobRow.job_id.desc()).limit(limit + 1)).all()
        items = tuple(
            AdminJobItem(
                row.job_id,
                row.job_type,
                row.state,
                row.created_at,
                row.progress_current,
                row.progress_total,
            )
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].job_id) if len(rows) > limit else None)

    def imports(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = select(ImportJobRow).where(ImportJobRow.user_id == actor.user_id)
        if after is not None:
            query = query.where(ImportJobRow.import_job_id < _cursor(after))
        rows = self._session.scalars(
            query.order_by(ImportJobRow.import_job_id.desc()).limit(limit + 1)
        ).all()
        items = tuple(
            AdminImportItem(row.import_job_id, row.adapter_id, row.mode, row.created_at)
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].import_job_id) if len(rows) > limit else None)

    def review(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        query = (
            select(ImportEntryRow)
            .join(ImportJobRow, ImportJobRow.import_job_id == ImportEntryRow.import_job_id)
            .where(
                ImportJobRow.user_id == actor.user_id,
                ImportEntryRow.match_status.in_(("REVIEW_REQUIRED", "INTEGRITY_CONFLICT")),
            )
        )
        if after is not None:
            query = query.where(ImportEntryRow.import_entry_id < _cursor(after))
        rows = self._session.scalars(
            query.order_by(ImportEntryRow.import_entry_id.desc()).limit(limit + 1)
        ).all()
        items = tuple(
            AdminReviewItem(
                row.import_entry_id,
                row.import_job_id,
                row.match_status,
            )
            for row in rows[:limit]
        )
        return AdminPage(items, str(rows[limit - 1].import_entry_id) if len(rows) > limit else None)

    def diagnostics(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        return self.audit(actor, limit=limit, after=after)

    def vault(self, actor: WebActor) -> AdminVaultStatus:
        object_count, committed_bytes, quarantined_objects, last_verified_at = (
            self._session.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(VaultObjectRow.byte_size).filter(
                            VaultObjectRow.commit_status == "COMMITTED"
                        ),
                        0,
                    ),
                    func.count().filter(VaultObjectRow.commit_status == "QUARANTINED"),
                    func.max(VaultObjectRow.last_verified_at),
                ).select_from(VaultObjectRow)
            ).one()
        )
        available_replicas, unhealthy_replicas = self._session.execute(
            select(
                func.count().filter(VaultReplicaRow.replica_status == "AVAILABLE"),
                func.count().filter(
                    VaultReplicaRow.replica_status.in_(("MISSING", "CORRUPT", "QUARANTINED"))
                ),
            ).select_from(VaultReplicaRow)
        ).one()
        open_count = (
            self._session.scalar(
                select(func.count())
                .select_from(UploadSessionRow)
                .where(UploadSessionRow.user_id == actor.user_id, UploadSessionRow.state == "OPEN")
            )
            or 0
        )
        quarantined = (
            self._session.scalar(
                select(func.count())
                .select_from(UploadSessionRow)
                .where(
                    UploadSessionRow.user_id == actor.user_id,
                    UploadSessionRow.state == "QUARANTINED",
                )
            )
            or 0
        )
        return AdminVaultStatus(
            int(object_count),
            int(committed_bytes),
            int(quarantined_objects),
            int(available_replicas),
            int(unhealthy_replicas),
            open_count,
            quarantined,
            last_verified_at,
            False,
        )

    def recovery(self, actor: WebActor) -> AdminUnavailable:
        del actor
        return AdminUnavailable("recovery_evidence_unavailable")


def _cursor(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("admin cursor is invalid") from error


__all__ = ("PostgreSqlAdminViews",)
