"""PostgreSQL transactions for passkeys and their existing M6 sessions."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.domain.web_passkeys import (
    PasskeyCeremony,
    PasskeyEvidence,
    PasskeyMetadata,
    VerifiedPasskey,
    VerifiedPasskeyAssertion,
)
from autplay.ports.web_passkeys import WebPasskeyRepository, WebPasskeyUnitOfWork

from .models import AuditEventRow
from .models.account import UserAccountRow
from .models.profile_pairing import ServerInstanceRow
from .models.web_admin import WebSessionRow, WebTerminalReceiptRow
from .models.web_passkeys import (
    WebPasskeyCeremonyRow,
    WebPasskeyRevocationRow,
    WebPasskeyRow,
)


class SqlAlchemyWebPasskeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_account(self, server_id: UUID, user_id: UUID) -> AccountRole:
        instance = self._session.scalar(
            select(ServerInstanceRow)
            .where(
                ServerInstanceRow.server_instance_id == server_id,
            )
            .with_for_update()
        )
        account = self._session.scalar(
            select(UserAccountRow).where(UserAccountRow.user_id == user_id).with_for_update()
        )
        if (
            instance is None
            or account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role not in {"OWNER", "ADMIN"}
        ):
            raise WebAdminError("passkey_invalid")
        return AccountRole(account.role)

    def lock_admin(self, actor: WebActor, now: datetime) -> None:
        role = self.lock_account(actor.server_instance_id, actor.user_id)
        row = self._session.scalar(
            select(WebSessionRow)
            .where(
                WebSessionRow.web_session_id == actor.web_session_id,
                WebSessionRow.user_id == actor.user_id,
                WebSessionRow.server_instance_id == actor.server_instance_id,
                WebSessionRow.token_generation == actor.token_generation,
                WebSessionRow.revoked_at.is_(None),
                WebSessionRow.idle_expires_at > now,
                WebSessionRow.absolute_expires_at > now,
                WebSessionRow.token_issued_at > now - timedelta(minutes=15),
            )
            .with_for_update()
        )
        if row is None or role != actor.role:
            raise WebAdminError("authentication_required")

    def credentials(self, user_id: UUID) -> tuple[PasskeyEvidence, ...]:
        rows = self._session.scalars(
            select(WebPasskeyRow)
            .where(
                WebPasskeyRow.user_id == user_id,
                WebPasskeyRow.revoked_at.is_(None),
            )
            .order_by(WebPasskeyRow.created_at, WebPasskeyRow.passkey_id)
            .limit(8)
        ).all()
        return tuple(_evidence(row) for row in rows)

    def find_credential(self, credential_id: bytes, *, lock: bool) -> PasskeyEvidence:
        query = (
            select(WebPasskeyRow)
            .where(
                WebPasskeyRow.credential_id == credential_id,
                WebPasskeyRow.revoked_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        row = self._session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise WebAdminError("passkey_invalid")
        return _evidence(row)

    def add_ceremony(self, ceremony: PasskeyCeremony, now: datetime) -> PasskeyCeremony:
        previous_id = self._session.scalar(
            select(WebPasskeyCeremonyRow.ceremony_id).where(
                WebPasskeyCeremonyRow.operation_id == ceremony.operation_id,
            )
        )
        if previous_id is not None:
            previous = self.ceremony(previous_id)
            if (
                previous.binding_sha256 != ceremony.binding_sha256
                or previous.purpose != ceremony.purpose
                or previous.origin != ceremony.origin
                or previous.rp_id != ceremony.rp_id
                or previous.expires_at <= now
                or previous.completed_request_sha256 is not None
            ):
                raise WebAdminError("operation_conflict")
            return previous
        if ceremony.user_id is not None:
            count = (
                self._session.scalar(
                    select(func.count())
                    .select_from(WebPasskeyCeremonyRow)
                    .where(
                        WebPasskeyCeremonyRow.user_id == ceremony.user_id,
                        WebPasskeyCeremonyRow.expires_at > now,
                        WebPasskeyCeremonyRow.consumed_at.is_(None),
                    )
                )
                or 0
            )
            if count >= 3:
                raise WebAdminError("rate_limited")
        self._session.add(WebPasskeyCeremonyRow(**asdict(ceremony), created_at=now))
        return ceremony

    def ceremony(self, ceremony_id: UUID) -> PasskeyCeremony:
        row = self._session.scalar(
            select(WebPasskeyCeremonyRow)
            .where(
                WebPasskeyCeremonyRow.ceremony_id == ceremony_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise WebAdminError("passkey_invalid")
        return PasskeyCeremony(
            row.ceremony_id,
            row.operation_id,
            row.purpose,
            row.challenge,
            row.binding_sha256,
            row.origin,
            row.rp_id,
            row.expires_at,
            row.user_handle,
            row.user_id,
            row.web_session_id,
            row.token_generation,
            row.completed_request_sha256,
            row.result_id,
        )

    def consume(
        self,
        ceremony_id: UUID,
        request_hash: bytes,
        result_id: UUID,
        now: datetime,
    ) -> None:
        row = self._session.get(WebPasskeyCeremonyRow, ceremony_id)
        if row is None or row.consumed_at is not None:
            raise WebAdminError("passkey_invalid")
        row.consumed_at = now
        row.completed_request_sha256 = request_hash
        row.result_id = result_id

    def register(
        self,
        actor: WebActor,
        ceremony: PasskeyCeremony,
        verified: VerifiedPasskey,
        label: str,
        now: datetime,
    ) -> UUID:
        if len(self.credentials(actor.user_id)) >= 8:
            raise WebAdminError("passkey_limit")
        existing = self._session.scalar(
            select(WebPasskeyRow.passkey_id).where(
                WebPasskeyRow.credential_id == verified.credential_id,
            )
        )
        if existing is not None or ceremony.user_handle is None:
            raise WebAdminError("passkey_invalid")
        passkey_id = uuid4()
        self._session.add(
            WebPasskeyRow(
                passkey_id=passkey_id,
                server_instance_id=actor.server_instance_id,
                user_id=actor.user_id,
                user_handle=ceremony.user_handle,
                credential_id=verified.credential_id,
                public_key=verified.public_key,
                sign_count=verified.sign_count,
                backup_eligible=verified.backup_eligible,
                backed_up=verified.backed_up,
                label=label,
                created_at=now,
            )
        )
        self._audit(actor.user_id, passkey_id, ceremony.operation_id, "web.passkey_registered", now)
        return passkey_id

    def update_counter(
        self,
        passkey: PasskeyEvidence,
        assertion: VerifiedPasskeyAssertion,
        now: datetime,
    ) -> None:
        row = self._session.get(WebPasskeyRow, passkey.passkey_id)
        if row is None or row.revoked_at is not None or row.sign_count != passkey.sign_count:
            raise WebAdminError("passkey_invalid")
        row.sign_count = assertion.sign_count
        row.backed_up = assertion.backed_up
        row.last_used_at = now

    def issue_session(
        self,
        passkey: PasskeyEvidence,
        operation_id: UUID,
        token_hash: bytes,
        csrf_hash: bytes,
        now: datetime,
    ) -> UUID:
        count = (
            self._session.scalar(
                select(func.count())
                .select_from(WebSessionRow)
                .where(
                    WebSessionRow.user_id == passkey.user_id,
                    WebSessionRow.revoked_at.is_(None),
                    WebSessionRow.absolute_expires_at > now,
                    WebSessionRow.idle_expires_at > now,
                )
            )
            or 0
        )
        if count >= 8:
            raise WebAdminError("passkey_session_limit")
        session_id = uuid4()
        self._session.add(
            WebSessionRow(
                web_session_id=session_id,
                family_id=session_id,
                server_instance_id=passkey.server_instance_id,
                user_id=passkey.user_id,
                token_generation=0,
                token_sha256=token_hash,
                csrf_sha256=csrf_hash,
                issued_at=now,
                token_issued_at=now,
                last_activity_at=now,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=12),
                passkey_id=passkey.passkey_id,
            )
        )
        self._audit(passkey.user_id, passkey.passkey_id, operation_id, "web.passkey_login", now)
        return session_id

    def list_metadata(self, user_id: UUID) -> tuple[PasskeyMetadata, ...]:
        rows = self._session.scalars(
            select(WebPasskeyRow)
            .where(WebPasskeyRow.user_id == user_id)
            .order_by(
                WebPasskeyRow.revoked_at.is_not(None),
                WebPasskeyRow.created_at.desc(),
                WebPasskeyRow.passkey_id,
            )
            .limit(32)
        ).all()
        return tuple(
            PasskeyMetadata(
                row.passkey_id,
                row.label,
                row.created_at,
                row.last_used_at,
                row.revoked_at,
            )
            for row in rows
        )

    def revoke(
        self,
        user_id: UUID,
        passkey_id: UUID,
        operation_id: UUID,
        actor_binding: bytes,
        now: datetime,
        actor: WebActor | None = None,
        request_hash: bytes | None = None,
    ) -> None:
        discovered = self._session.get(WebPasskeyRow, passkey_id)
        if discovered is None or discovered.user_id != user_id:
            raise WebAdminError("passkey_invalid")
        self._session.scalar(
            select(ServerInstanceRow)
            .where(
                ServerInstanceRow.server_instance_id == discovered.server_instance_id,
            )
            .with_for_update()
        )
        self._session.scalar(
            select(UserAccountRow)
            .where(
                UserAccountRow.user_id == user_id,
            )
            .with_for_update()
        )
        previous = self._session.get(WebPasskeyRevocationRow, operation_id)
        if previous is not None:
            if (previous.user_id, previous.passkey_id, previous.actor_binding) != (
                user_id,
                passkey_id,
                actor_binding,
            ):
                raise WebAdminError("operation_conflict")
            return
        if self._session.get(WebTerminalReceiptRow, operation_id) is not None:
            raise WebAdminError("operation_conflict")
        row = self._session.scalar(
            select(WebPasskeyRow)
            .where(
                WebPasskeyRow.passkey_id == passkey_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise WebAdminError("passkey_invalid")
        row.revoked_at = row.revoked_at or now
        if actor is not None and request_hash is not None:
            current = self._session.get(WebSessionRow, actor.web_session_id)
            if current is not None:
                self._session.add(
                    WebTerminalReceiptRow(
                        operation_id=operation_id,
                        server_instance_id=actor.server_instance_id,
                        user_id=user_id,
                        web_session_id=actor.web_session_id,
                        token_generation=actor.token_generation,
                        token_sha256=current.token_sha256,
                        action="REVOKE_WEB_PASSKEY",
                        target_type="WEB_PASSKEY",
                        target_id=passkey_id,
                        reason_code=None,
                        request_sha256=request_hash,
                        login_challenge_id=None,
                        login_cookie_sha256=None,
                        login_invitation_sha256=None,
                        outcome="PASSKEY_REVOKED",
                        terminal_at=now,
                        receipt_expires_at=current.absolute_expires_at + timedelta(minutes=5),
                    )
                )
        self._session.execute(
            update(WebSessionRow)
            .where(
                WebSessionRow.passkey_id == passkey_id,
                WebSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        self._session.add(
            WebPasskeyRevocationRow(
                operation_id=operation_id,
                user_id=user_id,
                passkey_id=passkey_id,
                actor_binding=actor_binding,
                created_at=now,
            )
        )
        self._audit(user_id, passkey_id, operation_id, "web.passkey_revoked", now)

    def cleanup(self, now: datetime, limit: int) -> int:
        ids = self._session.scalars(
            select(WebPasskeyCeremonyRow.ceremony_id)
            .where(
                WebPasskeyCeremonyRow.expires_at <= now - timedelta(days=1),
            )
            .order_by(WebPasskeyCeremonyRow.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        if ids:
            self._session.execute(
                delete(WebPasskeyCeremonyRow).where(
                    WebPasskeyCeremonyRow.ceremony_id.in_(ids),
                )
            )
        return len(ids)

    def _audit(
        self,
        user_id: UUID,
        target_id: UUID,
        operation_id: UUID,
        action: str,
        now: datetime,
    ) -> None:
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="ADMIN",
                actor_user_id=user_id,
                actor_device_id=None,
                action=action,
                target_type="WEB_PASSKEY",
                target_id=target_id,
                request_id=operation_id,
                reason_code=None,
                metadata_sanitized={"outcome": "APPLIED"},
            )
        )


def _evidence(row: WebPasskeyRow) -> PasskeyEvidence:
    return PasskeyEvidence(
        row.passkey_id,
        row.server_instance_id,
        row.user_id,
        row.user_handle,
        row.credential_id,
        row.public_key,
        row.sign_count,
        row.backup_eligible,
    )


class SqlAlchemyWebPasskeyUnitOfWork:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._session: Session | None = None
        self.passkeys: WebPasskeyRepository

    def __enter__(self) -> SqlAlchemyWebPasskeyUnitOfWork:
        self._session = self._sessions()
        self.passkeys = SqlAlchemyWebPasskeyRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("passkey unit of work inactive")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._session is not None:
            self._session.rollback()
            self._session.close()
            self._session = None


class SqlAlchemyWebPasskeyUnitOfWorkFactory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def __call__(self) -> WebPasskeyUnitOfWork:
        return SqlAlchemyWebPasskeyUnitOfWork(self._sessions)
