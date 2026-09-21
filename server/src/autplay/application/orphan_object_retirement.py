"""Durable ownership of an orphan CAS key before filesystem retirement."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from autplay.domain.vault import OpaqueStorageKey


class OrphanObjectOutcome(StrEnum):
    RETIRED = "RETIRED"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class OrphanObjectClaim:
    claim_id: UUID
    storage_key: OpaqueStorageKey
    outcome: OrphanObjectOutcome | None = None

    def __post_init__(self) -> None:
        if len(self.storage_key.value) != 64 or any(
            character not in "0123456789abcdef" for character in self.storage_key.value
        ):
            raise ValueError("orphan_object_key_invalid")

    @property
    def completed(self) -> bool:
        return self.outcome is not None

    @property
    def quarantine_key(self) -> OpaqueStorageKey:
        return OpaqueStorageKey(f"orphan-{self.claim_id.hex}")


class OrphanObjectRepository(Protocol):
    def claim(self, storage_key: OpaqueStorageKey, claim_id: UUID) -> OrphanObjectClaim | None: ...

    def complete(self, claim: OrphanObjectClaim, execution_id: UUID) -> None: ...

    def resolve_missing(self, claim: OrphanObjectClaim, execution_id: UUID) -> None: ...


class OrphanObjectStorage(Protocol):
    def retire_orphan_object(self, claim: OrphanObjectClaim) -> UUID:
        """Return the acknowledged successful maintenance execution, never a kill request."""
        ...

    def confirm_orphan_missing(self, claim: OrphanObjectClaim) -> UUID:
        """Return an acknowledged check-only execution proving both exact paths absent."""
        ...


class OrphanObjectRetirementService:
    def __init__(self, repository: OrphanObjectRepository, storage: OrphanObjectStorage) -> None:
        self._repository, self._storage = repository, storage

    def claim(self, storage_key: OpaqueStorageKey, claim_id: UUID) -> OrphanObjectClaim | None:
        """Return canonical ownership for callers that retain a retryable work item.

        An already active claim may have a different ID. Retain that returned ID
        before filesystem work so a lost completion reply replays the same claim.
        """
        return self._repository.claim(storage_key, claim_id)

    def retire(self, storage_key: OpaqueStorageKey, claim_id: UUID) -> bool:
        claim = self.claim(storage_key, claim_id)
        if claim is None:
            return False
        if claim.completed:
            return claim.outcome == OrphanObjectOutcome.RETIRED
        execution_id = self._storage.retire_orphan_object(claim)
        self._repository.complete(claim, execution_id)
        return True

    def resolve_missing(self, storage_key: OpaqueStorageKey, claim_id: UUID) -> bool:
        """Resolve only a fresh absence check; retirement failure alone is not evidence.

        Callers retrying joined work must retain the canonical ID returned by claim().
        """
        claim = self.claim(storage_key, claim_id)
        if claim is None:
            return False
        if claim.completed:
            return claim.outcome == OrphanObjectOutcome.MISSING
        execution_id = self._storage.confirm_orphan_missing(claim)
        self._repository.resolve_missing(claim, execution_id)
        return True
