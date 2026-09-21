"""Bounded positive inventory observations, never authority to infer missing bytes."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from autplay.application.orphan_object_retirement import (
    OrphanObjectClaim,
    OrphanObjectRetirementService,
)
from autplay.domain.vault import OpaqueStorageKey

MAX_INVENTORY_WORK = 100


class InventoryArea(StrEnum):
    OBJECT = "OBJECT"
    STAGING = "STAGING"


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    area: InventoryArea
    key: OpaqueStorageKey = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.area, InventoryArea) or (
            self.area == InventoryArea.OBJECT
            and (
                len(self.key.value) != 64
                or any(character not in "0123456789abcdef" for character in self.key.value)
            )
        ):
            raise ValueError("vault_inventory_entry_invalid")


@dataclass(frozen=True, slots=True)
class InventoryPage:
    entries: tuple[InventoryEntry, ...] = field(repr=False)
    work_units: int
    exhausted: bool

    def __post_init__(self) -> None:
        if (
            type(self.work_units) is not int
            or not 0 <= self.work_units <= MAX_INVENTORY_WORK
            or len(self.entries) > self.work_units
            or type(self.exhausted) is not bool
        ):
            raise ValueError("vault_inventory_page_invalid")


class InventoryOwnership(StrEnum):
    REGISTERED_OBJECT = "REGISTERED_OBJECT"
    ORPHAN_OBJECT_CANDIDATE = "ORPHAN_OBJECT_CANDIDATE"
    PROVIDER_STAGING = "PROVIDER_STAGING"
    UPLOAD_STAGING = "UPLOAD_STAGING"
    UNREGISTERED_STAGING = "UNREGISTERED_STAGING"


@dataclass(frozen=True, slots=True)
class InventoryObservation:
    entry: InventoryEntry = field(repr=False)
    ownership: InventoryOwnership


class InventoryCursor(Protocol):
    def next_page(self, *, maximum: int) -> InventoryPage:
        """Advance a live bounded cursor. Exhaustion is not an atomic snapshot."""
        ...

    def close(self) -> None: ...


class InventoryRepository(Protocol):
    def observe(self, page: InventoryPage) -> tuple[InventoryObservation, ...]:
        """Use a short transaction after enumeration; observations grant no ownership."""
        ...


@dataclass(frozen=True, slots=True)
class InventoryBatchResult:
    work_units: int
    observed: int
    orphan_candidates: int
    retired: int
    exhausted: bool


@dataclass(slots=True)
class _PendingBatch:
    page: InventoryPage
    claims: tuple[tuple[InventoryEntry, UUID], ...] | None = None
    position: int = 0
    retired: int = 0
    current_claim: OrphanObjectClaim | None = None


class BoundedVaultInventoryService:
    """Inspect positive entries and optionally use the existing claimed orphan path.

    Neither terminal jobs nor unknown staging authorize mutation. The injected
    cursor owns its position; closing/restarting it restarts enumeration. A
    production caller must supply a process-retained cursor before activation.
    """

    def __init__(
        self,
        cursor: InventoryCursor,
        repository: InventoryRepository,
        *,
        orphan_retirement: OrphanObjectRetirementService | None = None,
    ) -> None:
        self._cursor = cursor
        self._repository = repository
        self._orphan_retirement = orphan_retirement
        self._pending: _PendingBatch | None = None
        self._closed = False

    def run_batch(self, *, maximum: int = MAX_INVENTORY_WORK) -> InventoryBatchResult:
        if type(maximum) is not int or not 1 <= maximum <= MAX_INVENTORY_WORK:
            raise ValueError("vault_inventory_limit_invalid")
        if self._closed:
            raise ValueError("vault_inventory_closed")
        # No repository transaction may span directory traversal or retirement.
        if self._pending is None:
            self._pending = _PendingBatch(self._cursor.next_page(maximum=maximum))
        pending = self._pending
        page = pending.page
        if page.work_units > maximum:
            raise ValueError("vault_inventory_page_invalid")
        if pending.claims is None:
            observations = self._repository.observe(page)
            if tuple(item.entry for item in observations) != page.entries:
                raise ValueError("vault_inventory_observation_invalid")
            candidates = tuple(
                item.entry
                for item in observations
                if item.ownership == InventoryOwnership.ORPHAN_OBJECT_CANDIDATE
            )
            if any(item.area != InventoryArea.OBJECT for item in candidates):
                raise ValueError("vault_inventory_observation_invalid")
            pending.claims = tuple((item, uuid4()) for item in candidates)
        while pending.position < len(pending.claims):
            entry, claim_id = pending.claims[pending.position]
            if self._orphan_retirement is not None:
                if pending.current_claim is None:
                    pending.current_claim = self._orphan_retirement.claim(entry.key, claim_id)
                # claim() must recheck current object/replica registration. A
                # positive scan and its metadata observation can both be stale.
                # Retain the exact identity if a commit response is lost. A new
                # claim could otherwise mistake a later publication for this one.
                if pending.current_claim is not None:
                    pending.retired += self._orphan_retirement.retire(
                        entry.key, pending.current_claim.claim_id
                    )
            pending.current_claim = None
            pending.position += 1
        result = InventoryBatchResult(
            page.work_units, len(page.entries), len(pending.claims), pending.retired, page.exhausted
        )
        self._pending = None
        return result

    def close(self) -> None:
        self._closed = True
        self._cursor.close()
