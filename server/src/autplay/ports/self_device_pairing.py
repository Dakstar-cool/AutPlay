"""Transactional persistence for account-bound self-service pairing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from autplay.domain.auth import Principal
from autplay.domain.device_self_v2 import (
    CurrentSourceAuthoritySnapshot,
    SelfCeremonyV2,
    SelfCommandV2,
)
from autplay.domain.self_device_pairing import (
    PairingAccount,
    PairingBinding,
    PairingCommand,
    PairingIdentity,
    SelfDevicePairing,
)


class SelfPairingRepository(Protocol):
    def database_now(self) -> datetime: ...
    def find(self, ceremony_id: UUID, *, lock: bool = False) -> SelfDevicePairing | None: ...
    def lock_identity(self, server_id: UUID) -> PairingIdentity: ...
    def lock_account(self, user_id: UUID) -> PairingAccount: ...
    def actor_family(self, actor: Principal, now: datetime) -> UUID: ...
    def require_source(self, ceremony: SelfDevicePairing, now: datetime) -> None: ...
    def require_unique(self, field: str, value: UUID, ceremony_id: UUID) -> None: ...
    def pending_count(self, user_id: UUID, now: datetime) -> int: ...
    def save(self, ceremony: SelfDevicePairing) -> None: ...
    def command(self, operation_id: UUID) -> PairingCommand | None: ...
    def save_command(self, command: PairingCommand) -> None: ...
    def create_binding(self, ceremony: SelfDevicePairing, now: datetime) -> PairingBinding: ...
    def active_binding(self, ceremony: SelfDevicePairing, now: datetime) -> PairingBinding: ...
    def audit(
        self, ceremony: SelfDevicePairing, action: str, operation_id: UUID, now: datetime
    ) -> None: ...
    def rate_gate(self, key: bytes, maximum: int, now: datetime) -> bool: ...
    def cleanup(self, now: datetime, limit: int) -> int: ...


class SelfPairingUnitOfWork(Protocol):
    pairing: SelfPairingRepository

    def __enter__(self) -> SelfPairingUnitOfWork: ...
    def commit(self) -> None: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class SelfPairingUnitOfWorkFactory(Protocol):
    def __call__(self) -> SelfPairingUnitOfWork: ...


class SelfPairingRepositoryV2(SelfPairingRepository, Protocol):
    """Versioned snapshots share v1 locks, quotas and cleanup, never its parser."""

    def find(self, ceremony_id: UUID, *, lock: bool = False) -> SelfCeremonyV2 | None: ...
    def command(self, operation_id: UUID) -> SelfCommandV2 | None: ...
    def source_key(self, actor: Principal) -> tuple[str, bytes, bytes]: ...
    def current_source_authority(
        self,
        ceremony: SelfCeremonyV2,
        *,
        actor: Principal | None = None,
    ) -> CurrentSourceAuthoritySnapshot:
        """Load immutable facts under account/device/session locks held until commit."""
        ...

    def require_recipient_key(self, ceremony: SelfCeremonyV2) -> None: ...
    def create_binding(
        self,
        ceremony: SelfDevicePairing,
        now: datetime,
        *,
        before_write: Callable[[], datetime] | None = None,
    ) -> PairingBinding: ...
    def reserve(
        self,
        identifiers: dict[str, UUID],
        *,
        ceremony_id: UUID,
        command_id: UUID | None = None,
    ) -> None: ...


class SelfPairingUnitOfWorkV2(Protocol):
    pairing: SelfPairingRepositoryV2

    def __enter__(self) -> SelfPairingUnitOfWorkV2: ...
    def commit(self) -> None: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class SelfPairingUnitOfWorkFactoryV2(Protocol):
    def __call__(self) -> SelfPairingUnitOfWorkV2: ...
