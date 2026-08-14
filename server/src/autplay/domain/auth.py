"""Pure authentication and authorization values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID


class AccountRole(StrEnum):
    """Persisted account roles understood by the P03 runtime."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    USER = "USER"


class DevicePlatform(StrEnum):
    """Persisted device platforms understood by the P03 runtime."""

    ANDROID = "ANDROID"
    WEB = "WEB"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated account, device, and current session identity."""

    user_id: UUID
    device_id: UUID
    session_id: UUID
    role: AccountRole


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Integrity-checked access-token claims before database authorization."""

    user_id: UUID
    device_id: UUID
    session_id: UUID
    token_id: UUID
    issued_at: datetime
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceDescription:
    """Bounded metadata for the first owner device."""

    name: str
    platform: DevicePlatform
    app_version: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= 200:
            raise ValueError("device name must contain 1..200 characters")
        if not 1 <= len(self.app_version) <= 100:
            raise ValueError("app version must contain 1..100 characters")


@dataclass(frozen=True, slots=True)
class TokenPair:
    """Tokens deliberately returned to a trusted caller exactly once.

    Token values are excluded from representations so accidental structured
    logging of this result cannot disclose either bearer credential.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime
    user_id: UUID
    device_id: UUID
    session_id: UUID
    token_type: str = "Bearer"


@dataclass(frozen=True, slots=True)
class AuthSessionState:
    """Database session state needed by refresh-token commands."""

    session_id: UUID
    user_id: UUID
    device_id: UUID
    role: AccountRole
    account_status: str
    account_deleted_at: datetime | None
    device_revoked_at: datetime | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None

    def is_active_at(self, instant: datetime) -> bool:
        """Return whether all account, device, and session gates are active."""

        return (
            self.account_status == "ACTIVE"
            and self.account_deleted_at is None
            and self.device_revoked_at is None
            and self.revoked_at is None
            and self.expires_at > instant
        )


class AuthenticationError(RuntimeError):
    """Base class for user-safe authentication failures."""

    code: ClassVar[str] = "authentication_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidAccessTokenError(AuthenticationError):
    """The access token is invalid or no longer maps to an active session."""

    code = "invalid_access_token"


class InvalidRefreshTokenError(AuthenticationError):
    """The refresh token is invalid, expired, or no longer usable."""

    code = "invalid_refresh_token"


class RefreshTokenReplayError(AuthenticationError):
    """A known revoked refresh token was presented again."""

    code = "refresh_token_replay"


class OwnerAlreadyBootstrappedError(AuthenticationError):
    """The one-time local owner bootstrap has already been consumed."""

    code = "owner_already_bootstrapped"


class OwnedObjectNotFoundError(AuthenticationError):
    """An object is absent or not owned by the current principal."""

    code = "owned_object_not_found"


__all__ = (
    "AccessTokenClaims",
    "AccountRole",
    "AuthSessionState",
    "AuthenticationError",
    "DeviceDescription",
    "DevicePlatform",
    "InvalidAccessTokenError",
    "InvalidRefreshTokenError",
    "OwnedObjectNotFoundError",
    "OwnerAlreadyBootstrappedError",
    "Principal",
    "RefreshTokenReplayError",
    "TokenPair",
)
