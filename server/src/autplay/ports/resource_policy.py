"""Same-transaction quota policy authorization, receipts and audit persistence."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from autplay.domain.resource_policy import (
    QuotaAccountPage,
    QuotaChange,
    QuotaEditorSnapshot,
    QuotaPolicySnapshot,
)
from autplay.domain.web_admin import WebActor


class ResourcePolicyRepository(Protocol):
    def lock_actor(self, actor: WebActor, target: UUID | None, *, mutation: bool) -> datetime: ...
    def snapshot(self, target: UUID | None) -> QuotaPolicySnapshot: ...
    def editor(self, target: UUID | None, now: datetime) -> QuotaEditorSnapshot: ...
    def accounts(self, actor_id: UUID, after: UUID | None) -> QuotaAccountPage: ...
    def replay(self, actor: WebActor, change: QuotaChange) -> dict[str, object] | None: ...
    def save(self, change: QuotaChange, now: datetime) -> None: ...
    def receipt(
        self,
        actor: WebActor,
        change: QuotaChange,
        result: dict[str, object],
        now: datetime,
    ) -> None: ...
    def advance(self, now: datetime) -> None: ...


class ResourcePolicyUnitOfWork(Protocol):
    policy: ResourcePolicyRepository

    def __enter__(self) -> ResourcePolicyUnitOfWork: ...
    def commit(self) -> None: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class ResourcePolicyUnitOfWorkFactory(Protocol):
    def __call__(self) -> ResourcePolicyUnitOfWork: ...
