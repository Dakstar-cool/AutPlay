"""M6 browser authority use cases; HTTP adapts opaque values to cookies/forms."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import (
    AuthenticatedWebSession,
    BrowserInvitation,
    WebActor,
    WebAdminError,
    WebSessionCredentials,
    WebSessionMetadata,
)
from autplay.ports.web_admin import WebAdminUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    challenge_id: UUID
    login_operation_id: UUID
    cookie: bytes
    nonce: bytes
    expires_at: datetime


class WebAdminService:
    def __init__(
        self, units: WebAdminUnitOfWorkFactory, csrf_secret: bytes = b"test-web-admin-csrf"
    ) -> None:
        if len(csrf_secret) < 16:
            raise ValueError("web admin CSRF secret must be at least 16 bytes")
        self._units = units
        self._csrf_secret = csrf_secret

    def cleanup_expired(self, limit: int = 10_000, *, now: datetime | None = None) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("web admin cleanup limit must be within 1..10000")
        with self._units() as unit:
            deleted = unit.web_admin.cleanup_expired(limit, _now(now))
            unit.commit()
            return deleted

    def issue_invitation(self, user_id: UUID, *, now: datetime | None = None) -> BrowserInvitation:
        now = _now(now)
        bearer = _random()
        with self._units() as unit:
            invitation_id, server_id = unit.web_admin.issue_invitation(
                user_id, _digest(bearer), now, now + timedelta(minutes=5)
            )
            unit.commit()
        return BrowserInvitation(
            invitation_id, server_id, user_id, now + timedelta(minutes=5), bearer
        )

    def begin_login(self, *, now: datetime | None = None) -> LoginChallenge:
        now = _now(now)
        challenge = LoginChallenge(
            uuid4(), uuid4(), _random(), _random(), now + timedelta(minutes=5)
        )
        with self._units() as unit:
            unit.web_admin.begin_login(
                challenge.challenge_id,
                challenge.login_operation_id,
                _digest(challenge.cookie),
                _digest(challenge.nonce),
                challenge.expires_at,
            )
            unit.commit()
        return challenge

    def login_challenge_rate_gate(self, source_key: bytes, *, now: datetime | None = None) -> None:
        """Bound anonymous challenge persistence without retaining a network address."""
        now = _now(now)
        keys = (
            _digest(b"challenge-source:" + source_key),
            _digest(b"challenge-global"),
        )
        with self._units() as unit:
            allowed = unit.web_admin.login_challenge_allowed(keys, now)
            unit.commit()
        if not allowed:
            raise WebAdminError("rate_limited")

    def login(
        self,
        challenge: LoginChallenge,
        invitation: bytes,
        request_sha256: bytes,
        *,
        now: datetime | None = None,
    ) -> WebSessionCredentials:
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        now = _now(now)
        bearer = _random()
        csrf = self._csrf(bearer, 0)
        with self._units() as unit:
            result = unit.web_admin.consume_login(
                challenge.challenge_id,
                challenge.login_operation_id,
                _digest(invitation),
                _digest(challenge.cookie),
                _digest(challenge.nonce),
                _digest(bearer),
                _digest(csrf),
                request_sha256,
                now,
                now + timedelta(minutes=30),
                now + timedelta(hours=12),
            )
            unit.commit()
        if result is None:
            raise WebAdminError("browser_invitation_unavailable")
        server_id, user_id, session_id, role = result
        return WebSessionCredentials(
            WebActor(server_id, user_id, session_id, AccountRole(role), 0),
            now + timedelta(hours=12),
            bearer,
            csrf,
        )

    def login_rate_gate(
        self,
        source_key: bytes,
        invitation: bytes,
        request_sha256: bytes,
        now: datetime | None = None,
    ) -> None:
        """Pre-hash, bounded rate gate; callers check terminal receipt before retrying login."""
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        now = _now(now)
        keys = (
            _digest(b"source:" + source_key),
            _digest(b"invitation:" + invitation),
            _digest(b"global"),
        )
        with self._units() as unit:
            allowed = unit.web_admin.login_rate_allowed(keys, now)
            unit.commit()
        if not allowed:
            raise WebAdminError("rate_limited")

    def login_retry_outcome(
        self, operation_id: UUID, request_sha256: bytes, *, now: datetime | None = None
    ) -> None:
        """Never replay a lost cookie bearer; a committed receipt is evidence, not authority."""
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        with self._units() as unit:
            committed = unit.web_admin.login_receipt(operation_id, request_sha256, _now(now))
            unit.commit()
        raise WebAdminError(
            "browser_login_outcome_unknown" if committed else "browser_invitation_unavailable"
        )

    def authenticate(
        self, bearer: bytes, *, mutation: bool, now: datetime | None = None
    ) -> AuthenticatedWebSession:
        with self._units() as unit:
            try:
                actor = unit.web_admin.authenticate(_digest(bearer), _now(now), mutation)
            except ValueError as error:
                if str(error) == "browser_session_rotation_required":
                    raise WebAdminError("browser_session_rotation_required") from error
                raise
            unit.commit()
        if actor is None:
            raise WebAdminError("authentication_required")
        return AuthenticatedWebSession(actor, self._csrf(bearer, actor.token_generation))

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False, now: datetime | None = None
    ) -> AuthenticatedWebSession:
        """Only a GET may rotate; a predecessor is never returned as authority after rotation."""
        now = _now(now)
        with self._units() as unit:
            current = unit.web_admin.authenticate(_digest(bearer), now, False)
            unit.commit()
        if current is None:
            raise WebAdminError("authentication_required")
        next_bearer = _random()
        with self._units() as unit:
            result = unit.web_admin.rotate_if_due(
                _digest(bearer),
                current.token_generation,
                _digest(next_bearer),
                _digest(self._csrf(next_bearer, current.token_generation + 1)),
                now,
                not head,
            )
            unit.commit()
        if result is None:
            raise WebAdminError("authentication_required")
        actor, rotated = result
        if not rotated:
            next_bearer = b""
            return AuthenticatedWebSession(actor, self._csrf(bearer, actor.token_generation))
        return AuthenticatedWebSession(
            actor, self._csrf(next_bearer, actor.token_generation), next_bearer
        )

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        with self._units() as unit:
            valid = unit.web_admin.validate_csrf(actor, _digest(csrf), operation_id)
            unit.commit()
        if not valid:
            raise WebAdminError("csrf_invalid")

    def logout_current(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        with self._units() as unit:
            unit.web_admin.logout_current(
                actor, operation_id, request_sha256, reason_code, _now(now)
            )
            unit.commit()

    def revoked_logout_retry(
        self,
        bearer: bytes,
        operation_id: UUID,
        request_sha256: bytes,
        *,
        now: datetime | None = None,
    ) -> None:
        with self._units() as unit:
            outcome = unit.web_admin.revoked_logout_receipt(
                _digest(bearer), operation_id, request_sha256, _now(now)
            )
            unit.commit()
        if outcome is None:
            raise WebAdminError("authentication_required")

    def revoked_lifecycle_retry(
        self,
        bearer: bytes,
        operation_id: UUID,
        action: str,
        request_sha256: bytes,
        *,
        now: datetime | None = None,
    ) -> str:
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        with self._units() as unit:
            outcome = unit.web_admin.terminal_lifecycle_receipt(
                _digest(bearer), operation_id, action, request_sha256, _now(now)
            )
            unit.commit()
        if outcome is None:
            raise WebAdminError("authentication_required")
        return outcome

    def logout_all_browser(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        with self._units() as unit:
            unit.web_admin.logout_all_browser(
                actor, operation_id, request_sha256, reason_code, _now(now)
            )
            unit.commit()

    def revoke_browser_session(
        self,
        actor: WebActor,
        target_session_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        if len(request_sha256) != 32:
            raise ValueError("request hash must contain exactly 32 bytes")
        with self._units() as unit:
            unit.web_admin.revoke_browser_session(
                actor,
                target_session_id,
                operation_id,
                request_sha256,
                reason_code,
                _now(now),
            )
            unit.commit()

    def list_browser_sessions(
        self, user_id: UUID, *, limit: int = 100
    ) -> tuple[WebSessionMetadata, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("browser session list limit must be within 1..100")
        with self._units() as unit:
            rows = unit.web_admin.list_browser_sessions(user_id, limit)
            unit.commit()
        return rows

    def revoke_browser_session_local(
        self,
        user_id: UUID,
        web_session_id: UUID,
        operation_id: UUID,
        *,
        now: datetime | None = None,
    ) -> bool:
        with self._units() as unit:
            changed = unit.web_admin.revoke_browser_session_local(
                user_id, web_session_id, operation_id, _now(now)
            )
            unit.commit()
        return changed

    def revoke_all_browser_sessions_local(
        self, user_id: UUID, operation_id: UUID, *, now: datetime | None = None
    ) -> int:
        with self._units() as unit:
            changed = unit.web_admin.revoke_all_browser_sessions_local(
                user_id, operation_id, _now(now)
            )
            unit.commit()
        return changed

    def _csrf(self, bearer: bytes, generation: int) -> bytes:
        return hmac.digest(
            self._csrf_secret,
            b"AutPlay M6 CSRF v1\n" + bearer + generation.to_bytes(8, "big"),
            "sha256",
        )


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _random() -> bytes:
    return secrets.token_urlsafe(32).encode("ascii")


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


__all__ = ("LoginChallenge", "WebAdminService")
