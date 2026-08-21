"""Typed persistence seam for the optional M6 browser authority."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from autplay.domain.web_admin import WebActor, WebSessionMetadata


class WebAdminRepository(Protocol):
    def cleanup_expired(self, limit: int) -> int: ...
    def issue_invitation(
        self,
        user_id: UUID,
        secret_sha256: bytes,
        issued_at: datetime,
        expires_at: datetime,
    ) -> tuple[UUID, UUID]: ...
    def begin_login(
        self,
        challenge_id: UUID,
        operation_id: UUID,
        cookie_sha256: bytes,
        nonce_sha256: bytes,
        expires_at: datetime,
    ) -> None: ...
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
    ) -> tuple[UUID, UUID, UUID, str] | None: ...
    def authenticate(
        self, token_sha256: bytes, now: datetime, mutation: bool
    ) -> WebActor | None: ...
    def validate_csrf(self, actor: WebActor, csrf_sha256: bytes, operation_id: UUID) -> bool: ...
    def login_rate_allowed(self, keys: tuple[bytes, bytes, bytes], now: datetime) -> bool: ...
    def login_challenge_allowed(self, keys: tuple[bytes, bytes], now: datetime) -> bool: ...
    def login_receipt(self, operation_id: UUID, request_sha256: bytes, now: datetime) -> bool: ...
    def rotate_if_due(
        self,
        token_sha256: bytes,
        expected_generation: int,
        next_token_sha256: bytes,
        next_csrf_sha256: bytes,
        now: datetime,
        allow_rotation: bool,
    ) -> tuple[WebActor, bool] | None: ...
    def logout_current(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str: ...
    def revoked_logout_receipt(
        self, token_sha256: bytes, operation_id: UUID, request_sha256: bytes, now: datetime
    ) -> str | None: ...
    def terminal_lifecycle_receipt(
        self,
        token_sha256: bytes,
        operation_id: UUID,
        action: str,
        request_sha256: bytes,
        now: datetime,
    ) -> str | None: ...
    def logout_all_browser(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str: ...
    def revoke_browser_session(
        self,
        actor: WebActor,
        target_session_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None,
        now: datetime,
    ) -> str: ...
    def list_browser_sessions(
        self, user_id: UUID, limit: int
    ) -> tuple[WebSessionMetadata, ...]: ...
    def revoke_browser_session_local(
        self, user_id: UUID, web_session_id: UUID, operation_id: UUID, now: datetime
    ) -> bool: ...
    def revoke_all_browser_sessions_local(
        self, user_id: UUID, operation_id: UUID, now: datetime
    ) -> int: ...


class WebAdminUnitOfWork(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    @property
    def web_admin(self) -> WebAdminRepository: ...
    def commit(self) -> None: ...


class WebAdminUnitOfWorkFactory(Protocol):
    def __call__(self) -> WebAdminUnitOfWork: ...


__all__ = ("WebActor", "WebAdminRepository", "WebAdminUnitOfWork", "WebAdminUnitOfWorkFactory")
