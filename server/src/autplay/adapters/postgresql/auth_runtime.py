"""SQLAlchemy authentication repository and unit of work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import exists, select, text, update
from sqlalchemy.orm import Session

from autplay.domain.auth import AccountRole, AuthSessionState, Principal
from autplay.ports.auth import (
    AuditRecord,
    AuthRepository,
    AuthUnitOfWork,
    NewOwnerBundle,
    NewSession,
)

from .models import AuditEventRow, DeviceRow, UserAccountRow, UserSessionRow
from .models.types import JsonValue

# Stable signed int64 key spelling "AUTPLAY" plus the phase number. It only
# coordinates owner bootstrap transactions and has no authorization meaning.
OWNER_BOOTSTRAP_ADVISORY_LOCK = 0x415554504C415903


class SqlAlchemyAuthRepository:
    """Account/device/session operations bound to one SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acquire_owner_bootstrap_lock(self) -> None:
        """Acquire a transaction-scoped lock before checking the empty account table."""

        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": OWNER_BOOTSTRAP_ADVISORY_LOCK},
        )

    def any_account_exists(self) -> bool:
        """Return whether any account row, including disabled/deleted, exists."""

        return bool(self._session.scalar(select(exists().select_from(UserAccountRow))))

    def create_owner_bundle(self, bundle: NewOwnerBundle) -> None:
        """Stage the first OWNER, device, and session in the same transaction."""

        # These frozen mappings intentionally have no ORM relationships, so
        # flush each dependency level explicitly. All three flushes remain in
        # the caller's single transaction and roll back together on failure.
        owner = UserAccountRow(
            user_id=bundle.user_id,
            display_name=bundle.display_name,
            role="OWNER",
            status="ACTIVE",
            created_at=bundle.issued_at,
            updated_at=bundle.issued_at,
        )
        self._session.add(owner)
        self._session.flush([owner])

        device = DeviceRow(
            device_id=bundle.device_id,
            user_id=bundle.user_id,
            device_name=bundle.device_name,
            platform=bundle.platform,
            app_version=bundle.app_version,
            created_at=bundle.issued_at,
            updated_at=bundle.issued_at,
            last_seen_at=bundle.issued_at,
        )
        self._session.add(device)
        self._session.flush([device])

        session = _new_session_row(
            NewSession(
                session_id=bundle.session_id,
                user_id=bundle.user_id,
                device_id=bundle.device_id,
                refresh_token_hash=bundle.refresh_token_hash,
                issued_at=bundle.issued_at,
                expires_at=bundle.expires_at,
            )
        )
        self._session.add(session)
        self._session.flush([session])

    def get_session_by_refresh_hash_for_update(
        self, refresh_token_hash: bytes
    ) -> AuthSessionState | None:
        """Lock account, device, then session in the global auth lock order."""

        # The digest lookup first discovers immutable foreign-key identities.
        # Locking each row explicitly afterwards avoids relying on a join plan's
        # unspecified row-lock order. Logout-all uses the same account-first
        # order, preventing a replacement generation from appearing after it.
        identity = self._session.execute(
            select(
                UserSessionRow.session_id,
                UserSessionRow.user_id,
                UserSessionRow.device_id,
            ).where(UserSessionRow.refresh_token_hash == refresh_token_hash)
        ).one_or_none()
        if identity is None:
            return None

        session_id, user_id, device_id = identity
        account = self._session.scalar(
            select(UserAccountRow).where(UserAccountRow.user_id == user_id).with_for_update()
        )
        if account is None:
            return None
        device = self._session.scalar(
            select(DeviceRow)
            .where(DeviceRow.user_id == user_id, DeviceRow.device_id == device_id)
            .with_for_update()
        )
        if device is None:
            return None
        session = self._session.scalar(
            select(UserSessionRow)
            .where(
                UserSessionRow.session_id == session_id,
                UserSessionRow.user_id == user_id,
                UserSessionRow.device_id == device_id,
                UserSessionRow.refresh_token_hash == refresh_token_hash,
            )
            .with_for_update()
        )
        if session is None:
            return None
        return AuthSessionState(
            session_id=session.session_id,
            user_id=session.user_id,
            device_id=session.device_id,
            role=AccountRole(account.role),
            account_status=account.status,
            account_deleted_at=account.deleted_at,
            device_revoked_at=device.revoked_at,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
            revoked_at=session.revoked_at,
            session_mode=session.session_mode,
        )

    def revoke_session(self, session_id: UUID, *, revoked_at: datetime) -> None:
        """Revoke one generation while preserving its hash for replay detection."""

        self._session.execute(
            update(UserSessionRow)
            .where(
                UserSessionRow.session_id == session_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    def create_session(self, session: NewSession) -> None:
        """Stage a replacement refresh-token generation."""

        self._session.add(_new_session_row(session))
        self._session.flush()

    def revoke_active_sessions_for_device(
        self, user_id: UUID, device_id: UUID, *, revoked_at: datetime
    ) -> int:
        """Revoke every active generation for one user/device pair."""

        # Device-row locking serializes rotation, replay response, and device
        # revoke so an active generation cannot be inserted after this update.
        self._session.execute(
            select(DeviceRow.device_id)
            .where(DeviceRow.user_id == user_id, DeviceRow.device_id == device_id)
            .with_for_update()
        ).one_or_none()
        revoked_session_ids = self._session.scalars(
            update(UserSessionRow)
            .where(
                UserSessionRow.user_id == user_id,
                UserSessionRow.device_id == device_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .returning(UserSessionRow.session_id)
        ).all()
        return len(revoked_session_ids)

    def revoke_active_sessions_for_user(self, user_id: UUID, *, revoked_at: datetime) -> int:
        """Revoke every active generation belonging to one user."""

        self._session.execute(
            select(UserAccountRow.user_id)
            .where(UserAccountRow.user_id == user_id)
            .with_for_update()
        ).one_or_none()
        revoked_session_ids = self._session.scalars(
            update(UserSessionRow)
            .where(UserSessionRow.user_id == user_id, UserSessionRow.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
            .returning(UserSessionRow.session_id)
        ).all()
        return len(revoked_session_ids)

    def lock_owned_device(self, user_id: UUID, device_id: UUID) -> bool:
        """Lock an owned device without revealing cross-user existence."""

        row = self._session.execute(
            select(DeviceRow.device_id)
            .where(DeviceRow.user_id == user_id, DeviceRow.device_id == device_id)
            .with_for_update()
        ).one_or_none()
        return row is not None

    def revoke_device(self, user_id: UUID, device_id: UUID, *, revoked_at: datetime) -> None:
        """Revoke one owned device idempotently."""

        self._session.execute(
            update(DeviceRow)
            .where(
                DeviceRow.user_id == user_id,
                DeviceRow.device_id == device_id,
                DeviceRow.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    def load_active_principal(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> Principal | None:
        """Read the database-authoritative role and all current revocation gates."""

        row = self._session.execute(
            select(UserAccountRow.role)
            .join(DeviceRow, DeviceRow.user_id == UserAccountRow.user_id)
            .join(
                UserSessionRow,
                (UserSessionRow.user_id == UserAccountRow.user_id)
                & (UserSessionRow.device_id == DeviceRow.device_id),
            )
            .where(
                UserAccountRow.user_id == user_id,
                UserAccountRow.status == "ACTIVE",
                UserAccountRow.deleted_at.is_(None),
                DeviceRow.device_id == device_id,
                DeviceRow.revoked_at.is_(None),
                UserSessionRow.session_id == session_id,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
            )
        ).one_or_none()
        if row is None:
            return None
        return Principal(user_id, device_id, session_id, AccountRole(str(row[0])))

    def add_audit_event(self, event: AuditRecord) -> None:
        """Stage an audit row containing only explicitly sanitized metadata."""

        metadata = cast(JsonValue, dict(event.metadata_sanitized))
        self._session.add(
            AuditEventRow(
                occurred_at=event.occurred_at,
                actor_type=event.actor_type,
                actor_user_id=event.actor_user_id,
                actor_device_id=event.actor_device_id,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                request_id=event.request_id,
                reason_code=event.reason_code,
                metadata_sanitized=metadata,
            )
        )


class SqlAlchemyAuthUnitOfWork:
    """Short-lived SQLAlchemy Session implementing the auth transaction port."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._repository: SqlAlchemyAuthRepository | None = None
        self._committed = False

    @property
    def auth(self) -> AuthRepository:
        """Return the repository after the unit of work has been opened."""

        if self._repository is None:
            raise RuntimeError("authentication unit of work is not open")
        return self._repository

    def __enter__(self) -> SqlAlchemyAuthUnitOfWork:
        """Open one isolated SQLAlchemy Session."""

        if self._session is not None:
            raise RuntimeError("authentication unit of work cannot be re-entered")
        self._session = self._session_factory()
        self._repository = SqlAlchemyAuthRepository(self._session)
        self._committed = False
        return self

    def commit(self) -> None:
        """Commit staged account, device, session, and audit writes."""

        if self._session is None:
            raise RuntimeError("authentication unit of work is not open")
        self._session.commit()
        self._committed = True

    def rollback(self) -> None:
        """Roll back the current transaction."""

        if self._session is None:
            raise RuntimeError("authentication unit of work is not open")
        self._session.rollback()
        self._committed = False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Always roll back uncommitted work and close the Session."""

        if self._session is None:
            return None
        try:
            if exc_type is not None or not self._committed:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
            self._repository = None
            self._committed = False
        return None


class SqlAlchemyAuthUnitOfWorkFactory:
    """Create unopened SQLAlchemy authentication units of work."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AuthUnitOfWork:
        """Return one isolated authentication unit of work."""

        return SqlAlchemyAuthUnitOfWork(self._session_factory)


def _new_session_row(session: NewSession) -> UserSessionRow:
    if len(session.refresh_token_hash) != 32:
        raise ValueError("refresh-token hash must contain exactly 32 bytes")
    return UserSessionRow(
        session_id=session.session_id,
        user_id=session.user_id,
        device_id=session.device_id,
        refresh_token_hash=session.refresh_token_hash,
        issued_at=session.issued_at,
        expires_at=session.expires_at,
        last_rotated_at=session.issued_at,
    )


__all__ = (
    "OWNER_BOOTSTRAP_ADVISORY_LOCK",
    "SqlAlchemyAuthRepository",
    "SqlAlchemyAuthUnitOfWork",
    "SqlAlchemyAuthUnitOfWorkFactory",
)
