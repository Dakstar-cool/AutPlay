"""Durable, replayable retirement of files owned by an exited provider execution."""

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from autplay.domain.vault import OpaqueStorageKey


def provider_cleanup_id(execution_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"autplay:provider-cleanup:v1:{execution_id}")


@dataclass(frozen=True, slots=True)
class ProviderCleanupClaim:
    execution_id: UUID
    claim_id: UUID
    completed: bool = False

    def __post_init__(self) -> None:
        if self.claim_id != provider_cleanup_id(self.execution_id):
            raise ValueError("provider_cleanup_identity_invalid")

    @property
    def staging_key(self) -> OpaqueStorageKey:
        return OpaqueStorageKey(f"provider-{self.execution_id.hex}")

    @property
    def quarantine_key(self) -> OpaqueStorageKey:
        return OpaqueStorageKey(f"provider-cleanup-{self.claim_id.hex}")


class ProviderCleanupRepository(Protocol):
    def claim(self, execution_id: UUID) -> ProviderCleanupClaim | None: ...

    def complete(self, claim: ProviderCleanupClaim) -> None: ...


class ProviderCleanupStorage(Protocol):
    def retire(self, claim: ProviderCleanupClaim) -> None:
        """Preserve both final staging bytes and the entire execution scratch directory."""
        ...


class ProviderCleanupService:
    def __init__(
        self, repository: ProviderCleanupRepository, storage: ProviderCleanupStorage
    ) -> None:
        self._repository, self._storage = repository, storage

    def cleanup(self, execution_id: UUID) -> bool:
        claim = self._repository.claim(execution_id)
        if claim is None:
            return False
        if not claim.completed:
            # claim() has committed: no database transaction spans filesystem work.
            self._storage.retire(claim)
            self._repository.complete(claim)
        return True
