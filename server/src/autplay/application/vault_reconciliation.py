"""Bounded, idempotent Vault reconciliation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from autplay.domain.vault import VaultInventory
from autplay.ports.vault import VaultStorage


class ReconcileMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    APPLY = "APPLY"


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Aggregate-only report which never exposes paths or hashes."""

    inspected: int
    repaired: int
    quarantined: int
    remaining: int


class ReconciliationRepository(Protocol):
    def reconcile_inventory(
        self,
        inventory: VaultInventory,
        storage: VaultStorage,
        *,
        apply: bool,
        limit: int,
    ) -> ReconcileReport: ...


class VaultReconciliationService:
    """Run a bounded inventory/DB comparison; final committed bytes are never GC'd."""

    def __init__(self, *, repository: ReconciliationRepository, storage: VaultStorage) -> None:
        self._repository = repository
        self._storage = storage

    def run(self, *, mode: ReconcileMode, limit: int = 100) -> ReconcileReport:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between one and 1000")
        inventory = self._storage.inventory()
        return self._repository.reconcile_inventory(
            inventory, self._storage, apply=mode is ReconcileMode.APPLY, limit=limit
        )


__all__ = (
    "ReconcileMode",
    "ReconcileReport",
    "ReconciliationRepository",
    "VaultReconciliationService",
)
