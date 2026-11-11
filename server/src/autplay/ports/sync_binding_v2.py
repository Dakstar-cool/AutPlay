"""Read models for locked B1 authority and the existing cursor/revision owners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from autplay.domain.auth import AccessTokenClaims, Principal


@dataclass(frozen=True, slots=True, repr=False)
class DeviceActorEvidence:
    thumbprint: bytes | None
    current_key_valid: bool
    blocked: bool
    d1_platform_conflict: bool
    binding_history_conflict: bool


@dataclass(frozen=True, slots=True, repr=False)
class BindingCursor:
    user_id: UUID
    device_id: UUID
    journal_epoch: UUID | None
    last_pulled: int
    last_acked: int
    last_successful_sync_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class CapabilityRevision:
    value: int
    effective_digest: bytes
    identity_thumbprint: bytes
    device_thumbprint: bytes


class SyncBindingRepository(Protocol):
    """Application owns the transaction, M5 lock and all final pure checks."""

    def lock_cursor(self, device_id: UUID) -> BindingCursor | None: ...
    def has_prior_use(self, device_id: UUID) -> bool: ...
    def bind_unused(self, principal: Principal, epoch: UUID, now: datetime) -> None: ...


class BindingAccessAuthenticator(Protocol):
    def __call__(self, token: str) -> tuple[Principal, AccessTokenClaims]: ...
