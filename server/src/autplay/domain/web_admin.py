"""M6 browser-only identities; never interchangeable with Android principals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from autplay.domain.auth import AccountRole


@dataclass(frozen=True, slots=True)
class WebActor:
    server_instance_id: UUID
    user_id: UUID
    web_session_id: UUID
    role: AccountRole
    token_generation: int


class WebAdminError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WebSessionAction(StrEnum):
    """Browser-session lifecycle actions with durable terminal receipts."""

    LOGOUT_CURRENT_BROWSER = "LOGOUT_CURRENT_BROWSER"
    LOGOUT_ALL_BROWSER = "LOGOUT_ALL_BROWSER"
    REVOKE_INITIATING_BROWSER_SESSION = "REVOKE_INITIATING_BROWSER_SESSION"


@dataclass(frozen=True, slots=True)
class BrowserInvitation:
    """One-time CLI-delivered invitation; bearer stays non-representable."""

    invitation_id: UUID
    server_instance_id: UUID
    user_id: UUID
    expires_at: datetime
    bearer: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class WebSessionCredentials:
    """Opaque cookie and CSRF values returned only at a successful transition."""

    actor: WebActor
    expires_at: datetime
    bearer: bytes = field(repr=False)
    csrf: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedWebSession:
    """Authority result; CSRF is derived, never persisted in clear text."""

    actor: WebActor
    csrf: bytes = field(repr=False)
    rotated_bearer: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class TerminalWebReceipt:
    """Fixed lifecycle outcome that is never an authority grant."""

    operation_id: UUID
    action: WebSessionAction
    outcome: str
    terminal_at: datetime


@dataclass(frozen=True, slots=True)
class WebSessionMetadata:
    """Bounded local-recovery metadata; cookie hashes are intentionally absent."""

    web_session_id: UUID
    user_id: UUID
    token_generation: int
    issued_at: datetime
    last_activity_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None


__all__ = (
    "AuthenticatedWebSession",
    "BrowserInvitation",
    "TerminalWebReceipt",
    "WebActor",
    "WebAdminError",
    "WebSessionAction",
    "WebSessionCredentials",
    "WebSessionMetadata",
)
