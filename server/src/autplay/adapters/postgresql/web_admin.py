"""Bounded PostgreSQL cleanup for M6 non-authority evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError, WebSessionMetadata

from .models import AuditEventRow
from .models.account import UserAccountRow
from .models.profile_pairing import ServerInstanceRow
from .models.web_admin import (
    WebLoginChallengeRow,
    WebLoginRateWindowRow,
    WebSessionInvitationRow,
    WebSessionRotationEvidenceRow,
    WebSessionRow,
    WebTerminalReceiptRow,
)


class SqlAlchemyWebAdminRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cleanup_expired(self, limit: int) -> int:
        now = datetime.now(UTC)
        deleted = 0
        for row, key, expiry, extra in (
            (
                WebSessionRotationEvidenceRow,
                WebSessionRotationEvidenceRow.evidence_id,
                WebSessionRotationEvidenceRow.expires_at,
                (),
            ),
            (
                WebTerminalReceiptRow,
                WebTerminalReceiptRow.operation_id,
                WebTerminalReceiptRow.receipt_expires_at,
                (),
            ),
            (
                WebLoginChallengeRow,
                WebLoginChallengeRow.challenge_id,
                WebLoginChallengeRow.expires_at,
                (),
            ),
            (
                WebLoginRateWindowRow,
                WebLoginRateWindowRow.rate_key_sha256,
                WebLoginRateWindowRow.expires_at,
                (),
            ),
            (
                WebSessionInvitationRow,
                WebSessionInvitationRow.invitation_id,
                WebSessionInvitationRow.expires_at,
                (),
            ),
            (
                WebSessionRow,
                WebSessionRow.web_session_id,
                WebSessionRow.absolute_expires_at,
                (
                    ~select(WebSessionRotationEvidenceRow.evidence_id)
                    .where(
                        WebSessionRotationEvidenceRow.web_session_id == WebSessionRow.web_session_id
                    )
                    .exists(),
                    ~select(WebTerminalReceiptRow.operation_id)
                    .where(WebTerminalReceiptRow.web_session_id == WebSessionRow.web_session_id)
                    .exists(),
                ),
            ),
        ):
            remaining = limit - deleted
            if remaining <= 0:
                break
            ids = self._session.scalars(
                select(key)
                .where(expiry <= now, *extra)
                .order_by(expiry, key)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            ).all()
            for identifier in ids:
                self._session.execute(delete(row).where(key == identifier))
                deleted += 1
        return deleted

    def issue_invitation(
        self, user_id: UUID, secret_sha256: bytes, issued_at: datetime, expires_at: datetime
    ) -> tuple[UUID, UUID]:
        instance = self._session.scalar(select(ServerInstanceRow).with_for_update())
        if instance is None:
            raise ValueError("server_unavailable")
        account = self._session.scalar(
            select(UserAccountRow).where(UserAccountRow.user_id == user_id).with_for_update()
        )
        if account is None or account.status != "ACTIVE" or account.role not in {"OWNER", "ADMIN"}:
            raise ValueError("forbidden")
        instance_id = instance.server_instance_id
        issued = (
            self._session.scalar(
                select(func.count())
                .select_from(WebSessionInvitationRow)
                .where(
                    WebSessionInvitationRow.server_instance_id == instance_id,
                    WebSessionInvitationRow.issuer_kind == "LOCAL_CLI",
                    WebSessionInvitationRow.issued_at > issued_at - timedelta(hours=1),
                )
            )
            or 0
        )
        if issued >= 10:
            raise ValueError("rate_limited")
        active = self._session.scalars(
            select(WebSessionInvitationRow)
            .where(
                WebSessionInvitationRow.user_id == user_id,
                WebSessionInvitationRow.consumed_at.is_(None),
                WebSessionInvitationRow.cancelled_at.is_(None),
                WebSessionInvitationRow.expires_at > issued_at,
            )
            .with_for_update()
        ).all()
        if len(active) >= 3:
            raise ValueError("rate_limited")
        invitation_id = uuid4()
        self._session.add(
            WebSessionInvitationRow(
                invitation_id=invitation_id,
                server_instance_id=instance_id,
                user_id=user_id,
                issuer_kind="LOCAL_CLI",
                secret_sha256=secret_sha256,
                issued_at=issued_at,
                expires_at=expires_at,
                consumed_at=None,
                cancelled_at=None,
            )
        )
        self._session.add(
            AuditEventRow(
                occurred_at=issued_at,
                actor_type="SYSTEM",
                actor_user_id=None,
                actor_device_id=None,
                action="web.invitation_issued",
                target_type="WEB_SESSION_INVITATION",
                target_id=invitation_id,
                request_id=invitation_id,
                reason_code=None,
                metadata_sanitized={"outcome": "ISSUED"},
            )
        )
        return invitation_id, instance_id

    def begin_login(
        self,
        challenge_id: UUID,
        operation_id: UUID,
        cookie_sha256: bytes,
        nonce_sha256: bytes,
        expires_at: datetime,
    ) -> None:
        self._session.add(
            WebLoginChallengeRow(
                challenge_id=challenge_id,
                login_operation_id=operation_id,
                cookie_sha256=cookie_sha256,
                nonce_sha256=nonce_sha256,
                expires_at=expires_at,
                consumed_at=None,
            )
        )

    def consume_login(
        self,
        challenge_id: UUID,
        operation_id: UUID,
        invitation_sha256: bytes,
        cookie_sha256: bytes,
        nonce_sha256: bytes,
        token_sha256: bytes,
        csrf_sha256: bytes,
        request_sha256: bytes,
        now: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
    ) -> tuple[UUID, UUID, UUID, str] | None:
        discovered = self._session.execute(
            select(
                WebSessionInvitationRow.invitation_id,
                WebSessionInvitationRow.server_instance_id,
                WebSessionInvitationRow.user_id,
            ).where(WebSessionInvitationRow.secret_sha256 == invitation_sha256)
        ).one_or_none()
        if discovered is None:
            return None
        invitation_id, server_instance_id, user_id = discovered
        instance = self._session.scalar(
            select(ServerInstanceRow)
            .where(ServerInstanceRow.server_instance_id == server_instance_id)
            .with_for_update()
        )
        if instance is None:
            return None
        account = self._session.scalar(
            select(UserAccountRow).where(UserAccountRow.user_id == user_id).with_for_update()
        )
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role not in {"OWNER", "ADMIN"}
        ):
            return None
        challenge = self._session.scalar(
            select(WebLoginChallengeRow)
            .where(
                WebLoginChallengeRow.challenge_id == challenge_id,
                WebLoginChallengeRow.login_operation_id == operation_id,
                WebLoginChallengeRow.cookie_sha256 == cookie_sha256,
                WebLoginChallengeRow.nonce_sha256 == nonce_sha256,
                WebLoginChallengeRow.consumed_at.is_(None),
                WebLoginChallengeRow.expires_at > now,
            )
            .with_for_update()
        )
        invitation = self._session.scalar(
            select(WebSessionInvitationRow)
            .where(
                WebSessionInvitationRow.invitation_id == invitation_id,
                WebSessionInvitationRow.server_instance_id == server_instance_id,
                WebSessionInvitationRow.user_id == user_id,
                WebSessionInvitationRow.secret_sha256 == invitation_sha256,
                WebSessionInvitationRow.consumed_at.is_(None),
                WebSessionInvitationRow.cancelled_at.is_(None),
                WebSessionInvitationRow.expires_at > now,
            )
            .with_for_update()
        )
        if challenge is None or invitation is None:
            return None
        active_sessions = self._session.scalars(
            select(WebSessionRow.web_session_id)
            .where(WebSessionRow.user_id == invitation.user_id, WebSessionRow.revoked_at.is_(None))
            .order_by(WebSessionRow.web_session_id)
            .with_for_update()
        ).all()
        if len(active_sessions) >= 8:
            return None
        session_id = uuid4()
        self._session.add(
            WebSessionRow(
                web_session_id=session_id,
                family_id=session_id,
                server_instance_id=invitation.server_instance_id,
                user_id=invitation.user_id,
                token_generation=0,
                token_sha256=token_sha256,
                csrf_sha256=csrf_sha256,
                issued_at=now,
                token_issued_at=now,
                last_activity_at=now,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
                revoked_at=None,
            )
        )
        self._session.add(
            WebTerminalReceiptRow(
                operation_id=operation_id,
                server_instance_id=invitation.server_instance_id,
                user_id=invitation.user_id,
                web_session_id=session_id,
                token_generation=0,
                token_sha256=token_sha256,
                action="BROWSER_LOGIN",
                target_type="WEB_SESSION_INVITATION",
                target_id=invitation.invitation_id,
                reason_code=None,
                request_sha256=request_sha256,
                login_challenge_id=challenge_id,
                login_cookie_sha256=cookie_sha256,
                login_invitation_sha256=invitation_sha256,
                outcome="BROWSER_LOGIN_CREATED",
                terminal_at=now,
                receipt_expires_at=absolute_expires_at + timedelta(minutes=5),
            )
        )
        challenge.consumed_at = now
        invitation.consumed_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=invitation.user_id,
                actor_device_id=None,
                action="web.login_succeeded",
                target_type="WEB_SESSION",
                target_id=session_id,
                request_id=operation_id,
                reason_code=None,
                metadata_sanitized={"outcome": "CREATED"},
            )
        )
        return invitation.server_instance_id, invitation.user_id, session_id, account.role

    def authenticate(self, token_sha256: bytes, now: datetime, mutation: bool) -> WebActor | None:
        locked = self._locked_active_session_by_token(token_sha256, now)
        if locked is None:
            return None
        session, account = locked
        if mutation and session.token_issued_at <= now - timedelta(minutes=15):
            raise ValueError("browser_session_rotation_required")
        if not mutation and session.last_activity_at <= now - timedelta(minutes=5):
            session.last_activity_at = now
        return WebActor(
            session.server_instance_id,
            session.user_id,
            session.web_session_id,
            AccountRole(account.role),
            session.token_generation,
        )

    def validate_csrf(self, actor: WebActor, csrf_sha256: bytes, operation_id: UUID) -> bool:
        del operation_id
        row = self._locked_actor_session(actor)
        return row is not None and row.csrf_sha256 == csrf_sha256

    def login_rate_allowed(self, keys: tuple[bytes, bytes, bytes], now: datetime) -> bool:
        """Lock source, invitation, then global counters before invitation hashing/consume."""
        return self._rate_allowed(tuple(zip(keys, (10, 30, 100), strict=True)), now)

    def login_challenge_allowed(self, keys: tuple[bytes, bytes], now: datetime) -> bool:
        """Bound anonymous challenge creation by opaque source and global keys."""
        return self._rate_allowed(tuple(zip(keys, (60, 300), strict=True)), now)

    def _rate_allowed(self, keys: tuple[tuple[bytes, int], ...], now: datetime) -> bool:
        expiry = now + timedelta(minutes=15)
        for key, limit in keys:
            row = self._session.scalar(
                select(WebLoginRateWindowRow)
                .where(WebLoginRateWindowRow.rate_key_sha256 == key)
                .with_for_update()
            )
            if row is None:
                row = WebLoginRateWindowRow(
                    rate_key_sha256=key,
                    window_started_at=now,
                    expires_at=expiry,
                    attempt_count=0,
                )
                self._session.add(row)
                self._session.flush([row])
            if row.expires_at <= now:
                row.window_started_at = now
                row.expires_at = expiry
                row.attempt_count = 0
            if row.attempt_count >= limit:
                return False
            row.attempt_count += 1
        return True

    def login_receipt(self, operation_id: UUID, request_sha256: bytes, now: datetime) -> bool:
        return (
            self._session.scalar(
                select(WebTerminalReceiptRow.operation_id).where(
                    WebTerminalReceiptRow.operation_id == operation_id,
                    WebTerminalReceiptRow.action == "BROWSER_LOGIN",
                    WebTerminalReceiptRow.request_sha256 == request_sha256,
                    WebTerminalReceiptRow.terminal_at <= now,
                )
            )
            is not None
        )

    def rotate_if_due(
        self,
        token_sha256: bytes,
        expected_generation: int,
        next_token_sha256: bytes,
        next_csrf_sha256: bytes,
        now: datetime,
        allow_rotation: bool,
    ) -> tuple[WebActor, bool] | None:
        locked = self._locked_active_session_by_token(token_sha256, now)
        if locked is None:
            return None
        session, account = locked
        if session.token_generation != expected_generation:
            return None
        due = session.token_issued_at <= now - timedelta(minutes=15)
        if due and not allow_rotation:
            return None
        if due:
            self._session.add(
                WebSessionRotationEvidenceRow(
                    evidence_id=uuid4(),
                    web_session_id=session.web_session_id,
                    predecessor_token_sha256=token_sha256,
                    expires_at=now + timedelta(minutes=5),
                )
            )
            session.token_sha256 = next_token_sha256
            session.csrf_sha256 = next_csrf_sha256
            session.token_generation += 1
            session.token_issued_at = now
        if session.last_activity_at <= now - timedelta(minutes=5):
            session.last_activity_at = now
        return WebActor(
            session.server_instance_id,
            session.user_id,
            session.web_session_id,
            AccountRole(account.role),
            session.token_generation,
        ), due

    def logout_current(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str:
        self._lock_actor_authority(actor)
        receipt = self._locked_lifecycle_receipt(
            actor,
            operation_id,
            "LOGOUT_CURRENT_BROWSER",
            "WEB_SESSION",
            actor.web_session_id,
            reason_code,
            request_sha256,
        )
        if receipt is not None:
            return receipt.outcome
        session = self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == actor.web_session_id,
                WebSessionRow.user_id == actor.user_id,
                WebSessionRow.token_generation == actor.token_generation,
                WebSessionRow.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if session is None:
            raise WebAdminError("authentication_required")
        self._session.add(
            WebTerminalReceiptRow(
                operation_id=operation_id,
                server_instance_id=actor.server_instance_id,
                user_id=actor.user_id,
                web_session_id=actor.web_session_id,
                token_generation=actor.token_generation,
                token_sha256=session.token_sha256,
                action="LOGOUT_CURRENT_BROWSER",
                target_type="WEB_SESSION",
                target_id=actor.web_session_id,
                reason_code=reason_code,
                request_sha256=request_sha256,
                login_challenge_id=None,
                login_cookie_sha256=None,
                login_invitation_sha256=None,
                outcome="LOGGED_OUT",
                terminal_at=now,
                receipt_expires_at=session.absolute_expires_at + timedelta(minutes=5),
            )
        )
        session.revoked_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=actor.user_id,
                actor_device_id=None,
                action="web.logout_current",
                target_type="WEB_SESSION",
                target_id=actor.web_session_id,
                request_id=operation_id,
                reason_code=reason_code,
                metadata_sanitized={"outcome": "LOGGED_OUT"},
            )
        )
        return "LOGGED_OUT"

    def revoked_logout_receipt(
        self, token_sha256: bytes, operation_id: UUID, request_sha256: bytes, now: datetime
    ) -> str | None:
        return self.terminal_lifecycle_receipt(
            token_sha256, operation_id, "LOGOUT_CURRENT_BROWSER", request_sha256, now
        )

    def terminal_lifecycle_receipt(
        self,
        token_sha256: bytes,
        operation_id: UUID,
        action: str,
        request_sha256: bytes,
        now: datetime,
    ) -> str | None:
        return self._session.scalar(
            select(WebTerminalReceiptRow.outcome).where(
                WebTerminalReceiptRow.token_sha256 == token_sha256,
                WebTerminalReceiptRow.operation_id == operation_id,
                WebTerminalReceiptRow.action == action,
                WebTerminalReceiptRow.request_sha256 == request_sha256,
                WebTerminalReceiptRow.receipt_expires_at > now,
            )
        )

    def logout_all_browser(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str:
        self._lock_actor_authority(actor)
        receipt = self._locked_lifecycle_receipt(
            actor,
            operation_id,
            "LOGOUT_ALL_BROWSER",
            "WEB_USER",
            actor.user_id,
            reason_code,
            request_sha256,
        )
        if receipt is not None:
            return receipt.outcome
        current = self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == actor.web_session_id,
                WebSessionRow.user_id == actor.user_id,
                WebSessionRow.token_generation == actor.token_generation,
                WebSessionRow.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if current is None:
            raise WebAdminError("authentication_required")
        self._session.add(
            WebTerminalReceiptRow(
                operation_id=operation_id,
                server_instance_id=actor.server_instance_id,
                user_id=actor.user_id,
                web_session_id=actor.web_session_id,
                token_generation=actor.token_generation,
                token_sha256=current.token_sha256,
                action="LOGOUT_ALL_BROWSER",
                target_type="WEB_USER",
                target_id=actor.user_id,
                reason_code=reason_code,
                request_sha256=request_sha256,
                login_challenge_id=None,
                login_cookie_sha256=None,
                login_invitation_sha256=None,
                outcome="LOGGED_OUT_ALL",
                terminal_at=now,
                receipt_expires_at=current.absolute_expires_at + timedelta(minutes=5),
            )
        )
        for session in self._session.scalars(
            select(WebSessionRow)
            .where(WebSessionRow.user_id == actor.user_id, WebSessionRow.revoked_at.is_(None))
            .order_by(WebSessionRow.web_session_id)
            .with_for_update()
        ).all():
            session.revoked_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=actor.user_id,
                actor_device_id=None,
                action="web.logout_all_browser",
                target_type="WEB_USER",
                target_id=actor.user_id,
                request_id=operation_id,
                reason_code=reason_code,
                metadata_sanitized={"outcome": "LOGGED_OUT_ALL"},
            )
        )
        return "LOGGED_OUT_ALL"

    def revoke_browser_session(
        self,
        actor: WebActor,
        target_session_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str:
        self._lock_actor_authority(actor)
        receipt = self._locked_lifecycle_receipt(
            actor,
            operation_id,
            "REVOKE_BROWSER_SESSION",
            "WEB_SESSION",
            target_session_id,
            reason_code,
            request_sha256,
        )
        if receipt is not None:
            return receipt.outcome
        locked = {
            row.web_session_id: row
            for row in self._session.scalars(
                select(WebSessionRow)
                .where(
                    WebSessionRow.web_session_id.in_((actor.web_session_id, target_session_id)),
                    WebSessionRow.user_id == actor.user_id,
                    WebSessionRow.server_instance_id == actor.server_instance_id,
                )
                .order_by(WebSessionRow.web_session_id)
                .with_for_update()
            ).all()
        }
        current = locked.get(actor.web_session_id)
        if (
            current is None
            or current.token_generation != actor.token_generation
            or current.revoked_at is not None
        ):
            raise WebAdminError("authentication_required")
        target = locked.get(target_session_id)
        if target is None:
            raise WebAdminError("forbidden")
        outcome = "ALREADY_TERMINAL" if target.revoked_at is not None else "APPLIED"
        self._session.add(
            WebTerminalReceiptRow(
                operation_id=operation_id,
                server_instance_id=actor.server_instance_id,
                user_id=actor.user_id,
                web_session_id=actor.web_session_id,
                token_generation=actor.token_generation,
                token_sha256=current.token_sha256,
                action="REVOKE_BROWSER_SESSION",
                target_type="WEB_SESSION",
                target_id=target_session_id,
                reason_code=reason_code,
                request_sha256=request_sha256,
                login_challenge_id=None,
                login_cookie_sha256=None,
                login_invitation_sha256=None,
                outcome=outcome,
                terminal_at=target.revoked_at or now,
                receipt_expires_at=current.absolute_expires_at + timedelta(minutes=5),
            )
        )
        if target.revoked_at is None:
            target.revoked_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=actor.user_id,
                actor_device_id=None,
                action="web.browser_session_revoked",
                target_type="WEB_SESSION",
                target_id=target_session_id,
                request_id=operation_id,
                reason_code=reason_code,
                metadata_sanitized={"outcome": outcome},
            )
        )
        return outcome

    def list_browser_sessions(self, user_id: UUID, limit: int) -> tuple[WebSessionMetadata, ...]:
        self._active_admin_account(user_id)
        rows = self._session.scalars(
            select(WebSessionRow)
            .where(WebSessionRow.user_id == user_id)
            .order_by(WebSessionRow.web_session_id.desc())
            .limit(limit)
        ).all()
        return tuple(
            WebSessionMetadata(
                row.web_session_id,
                row.user_id,
                row.token_generation,
                row.issued_at,
                row.last_activity_at,
                row.idle_expires_at,
                row.absolute_expires_at,
                row.revoked_at,
            )
            for row in rows
        )

    def revoke_browser_session_local(
        self, user_id: UUID, web_session_id: UUID, operation_id: UUID, now: datetime
    ) -> bool:
        self._lock_instance()
        self._active_admin_account(user_id, lock=True)
        row = self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == web_session_id,
                WebSessionRow.user_id == user_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ValueError("browser_session_unavailable")
        changed = row.revoked_at is None
        if changed:
            row.revoked_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="SYSTEM",
                actor_user_id=None,
                actor_device_id=None,
                action="web.cli_session_revoked",
                target_type="WEB_SESSION",
                target_id=web_session_id,
                request_id=operation_id,
                reason_code="local_recovery",
                metadata_sanitized={"outcome": "APPLIED" if changed else "ALREADY_TERMINAL"},
            )
        )
        return changed

    def revoke_all_browser_sessions_local(
        self, user_id: UUID, operation_id: UUID, now: datetime
    ) -> int:
        self._lock_instance()
        self._active_admin_account(user_id, lock=True)
        rows = self._session.scalars(
            select(WebSessionRow)
            .where(WebSessionRow.user_id == user_id, WebSessionRow.revoked_at.is_(None))
            .order_by(WebSessionRow.web_session_id)
            .with_for_update()
        ).all()
        for row in rows:
            row.revoked_at = now
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="SYSTEM",
                actor_user_id=None,
                actor_device_id=None,
                action="web.cli_sessions_revoked_all",
                target_type="USER_ACCOUNT",
                target_id=user_id,
                request_id=operation_id,
                reason_code="local_recovery",
                metadata_sanitized={"outcome": "APPLIED", "count": len(rows)},
            )
        )
        return len(rows)

    def _lock_instance(self, server_instance_id: UUID | None = None) -> ServerInstanceRow:
        query = select(ServerInstanceRow)
        if server_instance_id is not None:
            query = query.where(ServerInstanceRow.server_instance_id == server_instance_id)
        row = self._session.scalar(query.limit(1).with_for_update())
        if row is None:
            raise ValueError("server_unavailable")
        return row

    def _active_admin_account(self, user_id: UUID, *, lock: bool = False) -> UserAccountRow:
        query = select(UserAccountRow).where(UserAccountRow.user_id == user_id)
        if lock:
            query = query.with_for_update()
        account = self._session.scalar(query)
        if (
            account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role not in {"OWNER", "ADMIN"}
        ):
            raise ValueError("forbidden")
        return account

    def _locked_actor_session(self, actor: WebActor) -> WebSessionRow | None:
        self._lock_actor_authority(actor)
        return self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == actor.web_session_id,
                WebSessionRow.server_instance_id == actor.server_instance_id,
                WebSessionRow.user_id == actor.user_id,
                WebSessionRow.token_generation == actor.token_generation,
                WebSessionRow.revoked_at.is_(None),
            )
            .with_for_update()
        )

    def _lock_actor_authority(self, actor: WebActor) -> UserAccountRow:
        self._lock_instance(actor.server_instance_id)
        return self._active_admin_account(actor.user_id, lock=True)

    def _locked_lifecycle_receipt(
        self,
        actor: WebActor,
        operation_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        reason_code: str | None,
        request_sha256: bytes,
    ) -> WebTerminalReceiptRow | None:
        receipt = self._session.scalar(
            select(WebTerminalReceiptRow)
            .where(WebTerminalReceiptRow.operation_id == operation_id)
            .with_for_update()
        )
        if receipt is None:
            return None
        if (
            receipt.server_instance_id != actor.server_instance_id
            or receipt.user_id != actor.user_id
            or receipt.web_session_id != actor.web_session_id
            or receipt.token_generation != actor.token_generation
            or receipt.action != action
            or receipt.target_type != target_type
            or receipt.target_id != target_id
            or receipt.reason_code != reason_code
            or receipt.request_sha256 != request_sha256
        ):
            raise WebAdminError("operation_conflict")
        return receipt

    def _locked_active_session_by_token(
        self, token_sha256: bytes, now: datetime
    ) -> tuple[WebSessionRow, UserAccountRow] | None:
        discovered = self._session.execute(
            select(
                WebSessionRow.web_session_id,
                WebSessionRow.server_instance_id,
                WebSessionRow.user_id,
            ).where(WebSessionRow.token_sha256 == token_sha256)
        ).one_or_none()
        if discovered is None:
            return None
        web_session_id, server_instance_id, user_id = discovered
        try:
            self._lock_instance(server_instance_id)
            account = self._active_admin_account(user_id, lock=True)
        except ValueError:
            return None
        session = self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == web_session_id,
                WebSessionRow.server_instance_id == server_instance_id,
                WebSessionRow.user_id == user_id,
                WebSessionRow.token_sha256 == token_sha256,
                WebSessionRow.revoked_at.is_(None),
                WebSessionRow.idle_expires_at > now,
                WebSessionRow.absolute_expires_at > now,
            )
            .with_for_update()
        )
        return None if session is None else (session, account)


__all__ = ("SqlAlchemyWebAdminRepository",)
