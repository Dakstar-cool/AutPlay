"""Transactional passkey persistence, sharing existing M6 browser session authority."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor
from autplay.domain.web_passkeys import (
    PasskeyCeremony,
    PasskeyEvidence,
    PasskeyMetadata,
    VerifiedPasskey,
    VerifiedPasskeyAssertion,
)


class WebPasskeyRepository(Protocol):
    def lock_admin(self, actor: WebActor, now: datetime) -> None: ...
    def lock_account(self, server_id: UUID, user_id: UUID) -> AccountRole: ...
    def credentials(self, user_id: UUID) -> tuple[PasskeyEvidence, ...]: ...
    def find_credential(self, credential_id: bytes, *, lock: bool) -> PasskeyEvidence: ...
    def add_ceremony(self, ceremony: PasskeyCeremony, now: datetime) -> PasskeyCeremony: ...
    def ceremony(self, ceremony_id: UUID) -> PasskeyCeremony: ...
    def consume(
        self,
        ceremony_id: UUID,
        request_hash: bytes,
        result_id: UUID,
        now: datetime,
    ) -> None: ...
    def register(
        self,
        actor: WebActor,
        ceremony: PasskeyCeremony,
        verified: VerifiedPasskey,
        label: str,
        now: datetime,
    ) -> UUID: ...
    def update_counter(
        self,
        passkey: PasskeyEvidence,
        assertion: VerifiedPasskeyAssertion,
        now: datetime,
    ) -> None: ...
    def issue_session(
        self,
        passkey: PasskeyEvidence,
        operation_id: UUID,
        token_hash: bytes,
        csrf_hash: bytes,
        now: datetime,
    ) -> UUID: ...
    def list_metadata(self, user_id: UUID) -> tuple[PasskeyMetadata, ...]: ...
    def revoke(
        self,
        user_id: UUID,
        passkey_id: UUID,
        operation_id: UUID,
        actor_binding: bytes,
        now: datetime,
        actor: WebActor | None = None,
        request_hash: bytes | None = None,
    ) -> None: ...
    def cleanup(self, now: datetime, limit: int) -> int: ...


class WebPasskeyUnitOfWork(Protocol):
    passkeys: WebPasskeyRepository

    def __enter__(self) -> WebPasskeyUnitOfWork: ...
    def commit(self) -> None: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class WebPasskeyUnitOfWorkFactory(Protocol):
    def __call__(self) -> WebPasskeyUnitOfWork: ...
