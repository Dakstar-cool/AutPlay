"""Owner bootstrap and device-session authentication use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from autplay.domain.auth import (
    AccountRole,
    DeviceDescription,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    OwnedObjectNotFoundError,
    OwnerAlreadyBootstrappedError,
    Principal,
    RefreshTokenReplayError,
    TokenPair,
)
from autplay.ports.auth import (
    AccessTokenCodec,
    AuditRecord,
    AuthUnitOfWorkFactory,
    NewOwnerBundle,
    NewSession,
    RefreshCredential,
    RefreshTokenCodec,
)
from autplay.ports.clock import Clock
from autplay.ports.ids import IdGenerator

MAX_ACCESS_TOKEN_TTL = timedelta(minutes=15)
MAX_REFRESH_TOKEN_TTL = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class BootstrapOwnerCommand:
    """Inputs accepted only by the local administrative entrypoint."""

    display_name: str
    device: DeviceDescription
    request_id: UUID | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.display_name) <= 200:
            raise ValueError("owner display name must contain 1..200 characters")


@dataclass(frozen=True, slots=True)
class AuthService:
    """Application transaction boundary for owner and device sessions."""

    unit_of_work_factory: AuthUnitOfWorkFactory
    clock: Clock
    ids: IdGenerator
    access_tokens: AccessTokenCodec
    refresh_tokens: RefreshTokenCodec
    access_token_ttl: timedelta
    refresh_token_ttl: timedelta

    def __post_init__(self) -> None:
        if not timedelta(0) < self.access_token_ttl <= MAX_ACCESS_TOKEN_TTL:
            raise ValueError("access token TTL must be within 1 second..15 minutes")
        if not self.access_token_ttl < self.refresh_token_ttl <= MAX_REFRESH_TOKEN_TTL:
            raise ValueError("refresh token TTL must exceed access TTL and be at most 90 days")

    def bootstrap_owner(self, command: BootstrapOwnerCommand) -> TokenPair:
        """Atomically create the first owner, device, and usable session."""

        now = _aware(self.clock.now())
        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.auth
            repository.acquire_owner_bootstrap_lock()
            if repository.any_account_exists():
                raise OwnerAlreadyBootstrappedError

            user_id = self.ids.new()
            device_id = self.ids.new()
            session_id = self.ids.new()
            refresh = self.refresh_tokens.issue()
            refresh_expires_at = now + self.refresh_token_ttl
            principal = Principal(user_id, device_id, session_id, AccountRole.OWNER)
            pair = self._issue_pair(
                principal,
                refresh=refresh,
                issued_at=now,
                refresh_expires_at=refresh_expires_at,
            )
            repository.create_owner_bundle(
                NewOwnerBundle(
                    user_id=user_id,
                    display_name=command.display_name,
                    device_id=device_id,
                    device_name=command.device.name,
                    platform=command.device.platform.value,
                    app_version=command.device.app_version,
                    session_id=session_id,
                    refresh_token_hash=refresh.sha256,
                    issued_at=now,
                    expires_at=refresh_expires_at,
                )
            )
            repository.add_audit_event(
                AuditRecord(
                    occurred_at=now,
                    actor_type="SYSTEM",
                    actor_user_id=user_id,
                    actor_device_id=device_id,
                    action="auth.owner_bootstrapped",
                    target_type="USER_ACCOUNT",
                    target_id=user_id,
                    request_id=command.request_id,
                )
            )
            unit_of_work.commit()
        return pair

    def authenticate_access(self, token: str) -> Principal:
        """Decode an access token and reload every mutable authorization gate."""

        now = _aware(self.clock.now())
        claims = self.access_tokens.decode(token, now=now)
        with self.unit_of_work_factory() as unit_of_work:
            principal = unit_of_work.auth.load_active_principal(
                user_id=claims.user_id,
                device_id=claims.device_id,
                session_id=claims.session_id,
                now=now,
            )
        if principal is None:
            raise InvalidAccessTokenError
        return principal

    def rotate_refresh(self, refresh_token: str, *, request_id: UUID | None = None) -> TokenPair:
        """Rotate one refresh generation and detect reuse of revoked generations."""

        digest = self.refresh_tokens.digest(refresh_token)
        if digest is None:
            raise InvalidRefreshTokenError

        now = _aware(self.clock.now())
        replay_detected = False
        inactive_session = False
        pair: TokenPair | None = None

        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.auth
            stored = repository.get_session_by_refresh_hash_for_update(digest)
            if stored is None:
                raise InvalidRefreshTokenError

            if stored.revoked_at is not None:
                repository.revoke_active_sessions_for_device(
                    stored.user_id,
                    stored.device_id,
                    revoked_at=now,
                )
                repository.add_audit_event(
                    AuditRecord(
                        occurred_at=now,
                        actor_type=_audit_actor_type(stored.role),
                        actor_user_id=stored.user_id,
                        actor_device_id=stored.device_id,
                        action="auth.refresh_replay_detected",
                        target_type="USER_SESSION",
                        target_id=stored.session_id,
                        request_id=request_id,
                        reason_code="KNOWN_REVOKED_GENERATION",
                    )
                )
                unit_of_work.commit()
                replay_detected = True
            elif not stored.is_active_at(now):
                repository.revoke_session(stored.session_id, revoked_at=now)
                repository.add_audit_event(
                    AuditRecord(
                        occurred_at=now,
                        actor_type=_audit_actor_type(stored.role),
                        actor_user_id=stored.user_id,
                        actor_device_id=stored.device_id,
                        action="auth.refresh_rejected",
                        target_type="USER_SESSION",
                        target_id=stored.session_id,
                        request_id=request_id,
                        reason_code="INACTIVE_SESSION",
                    )
                )
                unit_of_work.commit()
                inactive_session = True
            else:
                repository.revoke_session(stored.session_id, revoked_at=now)
                new_session_id = self.ids.new()
                replacement = self.refresh_tokens.issue()
                # Every generation keeps the initial grant's absolute expiry;
                # rotation cannot extend a stolen long-lived credential.
                refresh_expires_at = stored.expires_at
                principal = Principal(
                    stored.user_id,
                    stored.device_id,
                    new_session_id,
                    stored.role,
                )
                pair = self._issue_pair(
                    principal,
                    refresh=replacement,
                    issued_at=now,
                    refresh_expires_at=refresh_expires_at,
                )
                repository.create_session(
                    NewSession(
                        session_id=new_session_id,
                        user_id=stored.user_id,
                        device_id=stored.device_id,
                        refresh_token_hash=replacement.sha256,
                        issued_at=now,
                        expires_at=refresh_expires_at,
                    )
                )
                repository.add_audit_event(
                    AuditRecord(
                        occurred_at=now,
                        actor_type=_audit_actor_type(stored.role),
                        actor_user_id=stored.user_id,
                        actor_device_id=stored.device_id,
                        action="auth.refresh_rotated",
                        target_type="USER_SESSION",
                        target_id=new_session_id,
                        request_id=request_id,
                    )
                )
                unit_of_work.commit()

        # Replay must be raised only after its revocation transaction commits.
        if replay_detected:
            raise RefreshTokenReplayError
        if inactive_session:
            raise InvalidRefreshTokenError
        if pair is None:
            raise RuntimeError("refresh rotation completed without a result")
        return pair

    def logout(self, principal: Principal, *, request_id: UUID | None = None) -> None:
        """Revoke only the authenticated current session."""

        now = _aware(self.clock.now())
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.auth.revoke_session(principal.session_id, revoked_at=now)
            unit_of_work.auth.add_audit_event(
                _principal_audit(
                    principal,
                    now=now,
                    action="auth.session_logged_out",
                    target_type="USER_SESSION",
                    target_id=principal.session_id,
                    request_id=request_id,
                )
            )
            unit_of_work.commit()

    def logout_all(self, principal: Principal, *, request_id: UUID | None = None) -> int:
        """Revoke all sessions for the authenticated user, but not devices."""

        now = _aware(self.clock.now())
        with self.unit_of_work_factory() as unit_of_work:
            revoked = unit_of_work.auth.revoke_active_sessions_for_user(
                principal.user_id, revoked_at=now
            )
            unit_of_work.auth.add_audit_event(
                _principal_audit(
                    principal,
                    now=now,
                    action="auth.all_sessions_logged_out",
                    target_type="USER_ACCOUNT",
                    target_id=principal.user_id,
                    request_id=request_id,
                )
            )
            unit_of_work.commit()
        return revoked

    def revoke_device(
        self,
        principal: Principal,
        device_id: UUID,
        *,
        request_id: UUID | None = None,
    ) -> int:
        """Revoke an owned device and all its sessions without cross-user leakage."""

        now = _aware(self.clock.now())
        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.auth
            if not repository.lock_owned_device(principal.user_id, device_id):
                raise OwnedObjectNotFoundError
            repository.revoke_device(principal.user_id, device_id, revoked_at=now)
            revoked_sessions = repository.revoke_active_sessions_for_device(
                principal.user_id,
                device_id,
                revoked_at=now,
            )
            repository.add_audit_event(
                _principal_audit(
                    principal,
                    now=now,
                    action="auth.device_revoked",
                    target_type="DEVICE",
                    target_id=device_id,
                    request_id=request_id,
                )
            )
            unit_of_work.commit()
        return revoked_sessions

    def _issue_pair(
        self,
        principal: Principal,
        *,
        refresh: RefreshCredential,
        issued_at: datetime,
        refresh_expires_at: datetime,
    ) -> TokenPair:
        access_expires_at = issued_at + self.access_token_ttl
        access_token = self.access_tokens.issue(
            principal,
            token_id=self.ids.new(),
            issued_at=issued_at,
            expires_at=access_expires_at,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh.token,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            user_id=principal.user_id,
            device_id=principal.device_id,
            session_id=principal.session_id,
        )


def _aware(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return instant


def _audit_actor_type(role: AccountRole) -> str:
    return "ADMIN" if role in {AccountRole.OWNER, AccountRole.ADMIN} else "USER"


def _principal_audit(
    principal: Principal,
    *,
    now: datetime,
    action: str,
    target_type: str,
    target_id: UUID,
    request_id: UUID | None,
) -> AuditRecord:
    return AuditRecord(
        occurred_at=now,
        actor_type=_audit_actor_type(principal.role),
        actor_user_id=principal.user_id,
        actor_device_id=principal.device_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


__all__ = ("AuthService", "BootstrapOwnerCommand")
