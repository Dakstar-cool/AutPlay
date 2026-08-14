"""Authentication persistence and cryptography boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from autplay.domain.auth import AccessTokenClaims, AuthSessionState, Principal


@dataclass(frozen=True, slots=True)
class RefreshCredential:
    """One opaque refresh token and its persistence-safe digest."""

    token: str = field(repr=False)
    sha256: bytes

    def __post_init__(self) -> None:
        if len(self.sha256) != 32:
            raise ValueError("refresh credential digest must contain 32 bytes")


@dataclass(frozen=True, slots=True)
class NewOwnerBundle:
    """Rows created by the one-time local owner bootstrap."""

    user_id: UUID
    display_name: str
    device_id: UUID
    device_name: str
    platform: str
    app_version: str
    session_id: UUID
    refresh_token_hash: bytes
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NewSession:
    """A new refresh-token generation bound to one existing device."""

    session_id: UUID
    user_id: UUID
    device_id: UUID
    refresh_token_hash: bytes
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Sanitized append-only audit event requested by an auth use case."""

    occurred_at: datetime
    actor_type: str
    action: str
    target_type: str
    actor_user_id: UUID | None = None
    actor_device_id: UUID | None = None
    target_id: UUID | None = None
    request_id: UUID | None = None
    reason_code: str | None = None
    metadata_sanitized: dict[str, str | int | bool | None] = field(default_factory=dict)


class PasswordHasher(Protocol):
    """Slow password hashing boundary; no login flow is enabled in P03."""

    def hash_password(self, password: str) -> str:
        """Return an encoded password hash with a fresh random salt."""

        ...

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        """Return whether a password matches without leaking parse failures."""

        ...

    def needs_rehash(self, encoded_hash: str) -> bool:
        """Return whether a valid encoded hash uses obsolete parameters."""

        ...


class AccessTokenCodec(Protocol):
    """Issue and strictly validate short-lived access tokens."""

    def issue(
        self,
        principal: Principal,
        *,
        token_id: UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        """Issue an access token for one current session."""

        ...

    def decode(self, token: str, *, now: datetime) -> AccessTokenClaims:
        """Validate a token and return integrity-checked claims."""

        ...


class RefreshTokenCodec(Protocol):
    """Generate opaque refresh credentials and hash presented values."""

    def issue(self) -> RefreshCredential:
        """Return a new 256-bit opaque token and SHA-256 digest."""

        ...

    def digest(self, token: str) -> bytes | None:
        """Return a canonical token digest, or ``None`` for malformed input."""

        ...


class AuthRepository(Protocol):
    """Transaction-bound account/device/session persistence operations."""

    def acquire_owner_bootstrap_lock(self) -> None:
        """Serialize all one-time owner bootstrap attempts."""

        ...

    def any_account_exists(self) -> bool:
        """Return whether the one-time bootstrap has already been consumed."""

        ...

    def create_owner_bundle(self, bundle: NewOwnerBundle) -> None:
        """Insert the first owner, device, and session generation."""

        ...

    def get_session_by_refresh_hash_for_update(
        self, refresh_token_hash: bytes
    ) -> AuthSessionState | None:
        """Lock and return a session, including revoked generations."""

        ...

    def revoke_session(self, session_id: UUID, *, revoked_at: datetime) -> None:
        """Revoke one session without removing its refresh-token digest."""

        ...

    def create_session(self, session: NewSession) -> None:
        """Insert a new refresh-token generation."""

        ...

    def revoke_active_sessions_for_device(
        self, user_id: UUID, device_id: UUID, *, revoked_at: datetime
    ) -> int:
        """Revoke all active session generations for one owned device."""

        ...

    def revoke_active_sessions_for_user(self, user_id: UUID, *, revoked_at: datetime) -> int:
        """Revoke every active session generation for one user."""

        ...

    def lock_owned_device(self, user_id: UUID, device_id: UUID) -> bool:
        """Lock an owned device, returning false for missing or cross-user IDs."""

        ...

    def revoke_device(self, user_id: UUID, device_id: UUID, *, revoked_at: datetime) -> None:
        """Revoke one already locked owned device."""

        ...

    def load_active_principal(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> Principal | None:
        """Reload all account/device/session authorization gates."""

        ...

    def add_audit_event(self, event: AuditRecord) -> None:
        """Append a sanitized authentication audit event."""

        ...


class AuthUnitOfWork(Protocol):
    """One caller-owned authentication transaction."""

    @property
    def auth(self) -> AuthRepository:
        """Return the transaction-bound authentication repository."""

        ...

    def __enter__(self) -> Self:
        """Open transaction resources."""

        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Roll back uncommitted work and release resources."""

        ...

    def commit(self) -> None:
        """Commit the current transaction."""

        ...

    def rollback(self) -> None:
        """Roll back the current transaction."""

        ...


class AuthUnitOfWorkFactory(Protocol):
    """Create isolated authentication units of work."""

    def __call__(self) -> AuthUnitOfWork:
        """Return one unopened unit of work."""

        ...


__all__ = (
    "AccessTokenCodec",
    "AuditRecord",
    "AuthRepository",
    "AuthUnitOfWork",
    "AuthUnitOfWorkFactory",
    "NewOwnerBundle",
    "NewSession",
    "PasswordHasher",
    "RefreshCredential",
    "RefreshTokenCodec",
)
