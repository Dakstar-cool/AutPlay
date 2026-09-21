"""Replayable scratch retirement after immutable provider-to-Vault handoff."""

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5


def provider_scratch_id(execution_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"autplay:provider-scratch-retirement:v1:{execution_id}")


@dataclass(frozen=True, slots=True)
class ProviderScratchClaim:
    execution_id: UUID
    claim_id: UUID
    completed: bool = False

    def __post_init__(self) -> None:
        if self.claim_id != provider_scratch_id(self.execution_id):
            raise ValueError("provider_scratch_identity_invalid")


class ProviderScratchRepository(Protocol):
    def claim(self, execution_id: UUID) -> ProviderScratchClaim | None: ...

    def complete(self, claim: ProviderScratchClaim) -> None: ...


class ProviderScratchStorage(Protocol):
    def retire_scratch(self, claim: ProviderScratchClaim) -> None:
        """Retire the exact workspace; staging, quarantine and CAS are outside this grant."""
        ...


class ProviderScratchService:
    def __init__(
        self, repository: ProviderScratchRepository, storage: ProviderScratchStorage
    ) -> None:
        self._repository, self._storage = repository, storage

    def retire(self, execution_id: UUID) -> bool:
        claim = self._repository.claim(execution_id)
        if claim is None:
            return False
        if not claim.completed:
            # The immutable claim has committed before the filesystem can block.
            self._storage.retire_scratch(claim)
            self._repository.complete(claim)
        return True
