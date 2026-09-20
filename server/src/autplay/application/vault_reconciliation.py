"""Sequential bounded scan/claim/drain; observations never revoke writer ownership."""

from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from autplay.application.orphan_object_retirement import (
    OrphanObjectClaim,
    OrphanObjectOutcome,
    OrphanObjectRepository,
    OrphanObjectRetirementService,
    OrphanObjectStorage,
)
from autplay.application.vault_inventory import (
    MAX_INVENTORY_WORK,
    InventoryArea,
    InventoryCursor,
    InventoryOwnership,
    InventoryRepository,
)
from autplay.domain.resource_admission import ResourceAdmissionError


class ReconcileMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    APPLY = "APPLY"


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Aggregate observations/outcomes, without paths, hashes or resumable scan tokens."""

    work_units: int = 0
    inspected: int = 0
    orphan_candidates: int = 0
    claimed: int = 0
    quarantined: int = 0
    missing: int = 0
    deferred: int = 0
    protected: int = 0
    unregistered_staging: int = 0
    scan_complete: bool = False
    pass_complete: bool = False
    pending_claims: bool = False


class PendingOrphanRepository(OrphanObjectRepository, Protocol):
    def pending(
        self, *, maximum: int = 20, after: UUID | None = None
    ) -> tuple[OrphanObjectClaim, ...]: ...


class RetainedInventoryCursor(InventoryCursor, Protocol):
    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]: ...


class RetainedOrphanStorage(OrphanObjectStorage, Protocol):
    def pending(self) -> tuple[UUID, ...]: ...

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]: ...


class VaultReconciliationService:
    """One live pass of bounded steps, then exact scanner exit before retirement.

    Each instance is single-use. Restart drains durable claims, then scans afresh;
    a stopped scandir iterator is never represented as a durable continuation.
    Tracked CAS and all staging remain protected pending their writer protocols.
    """

    def __init__(
        self,
        *,
        cursor: RetainedInventoryCursor,
        inventory: InventoryRepository,
        claims: PendingOrphanRepository,
        storage: RetainedOrphanStorage,
    ) -> None:
        self._cursor, self._inventory = cursor, inventory
        self._claims, self._storage = claims, storage
        self._retirement = OrphanObjectRetirementService(claims, storage)
        self._report = ReconcileReport()
        self._started = False

    def run(
        self, *, mode: ReconcileMode, limit: int = 100, drain_only: bool = False
    ) -> ReconcileReport:
        with closing(self.steps(mode=mode, limit=limit, drain_only=drain_only)) as steps:
            for _report in steps:
                pass
        return self._report

    def steps(
        self, *, mode: ReconcileMode, limit: int = 100, drain_only: bool = False
    ) -> Generator[ReconcileReport]:
        """Keep this iterator alive across pages; close it explicitly when aborting."""
        if (
            not isinstance(mode, ReconcileMode)
            or type(limit) is not int
            or not 1 <= limit <= MAX_INVENTORY_WORK
            or type(drain_only) is not bool
            or (drain_only and mode != ReconcileMode.APPLY)
        ):
            raise ValueError("vault_reconcile_request_invalid")
        if self._started:
            raise ValueError("vault_reconcile_already_started")
        self._started = True
        try:
            if mode == ReconcileMode.APPLY:
                yield from self._drain(limit)
            if not drain_only:
                try:
                    yield from self._scan(mode, limit)
                finally:
                    # close() discards the retained cursor's pending result. No
                    # retirement may start before this exact owner acknowledges exit.
                    if self._cursor.shutdown():
                        raise ResourceAdmissionError("maintenance_exit_unconfirmed")
                if mode == ReconcileMode.APPLY:
                    yield from self._drain(limit)
            self._shutdown()
            self._report = replace(
                self._report,
                pass_complete=True,
                pending_claims=bool(self._claims.pending(maximum=1)),
            )
            yield self._report
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        pending = self._cursor.shutdown()
        pending += self._storage.shutdown()
        if pending:
            raise ResourceAdmissionError("maintenance_exit_unconfirmed")

    def _scan(self, mode: ReconcileMode, limit: int) -> Iterator[ReconcileReport]:
        while True:
            page = self._cursor.next_page(maximum=limit)
            if page.work_units > limit or (page.work_units == 0 and not page.exhausted):
                raise ValueError("vault_inventory_page_invalid")
            observations = self._inventory.observe(page)
            if tuple(item.entry for item in observations) != page.entries:
                raise ValueError("vault_inventory_observation_invalid")
            candidates = claimed = protected = unregistered = 0
            for item in observations:
                if item.ownership == InventoryOwnership.ORPHAN_OBJECT_CANDIDATE:
                    if item.entry.area != InventoryArea.OBJECT:
                        raise ValueError("vault_inventory_observation_invalid")
                    candidates += 1
                    if mode == ReconcileMode.APPLY:
                        claim = self._claims.claim(item.entry.key, uuid4())
                        claimed += claim is not None and not claim.completed
                elif item.ownership == InventoryOwnership.UNREGISTERED_STAGING:
                    unregistered += 1
                else:
                    protected += 1
            self._report = replace(
                self._report,
                work_units=self._report.work_units + page.work_units,
                inspected=self._report.inspected + len(page.entries),
                orphan_candidates=self._report.orphan_candidates + candidates,
                claimed=self._report.claimed + claimed,
                protected=self._report.protected + protected,
                unregistered_staging=self._report.unregistered_staging + unregistered,
                scan_complete=page.exhausted,
            )
            yield self._report
            if page.exhausted:
                return

    def _drain(self, limit: int) -> Iterator[ReconcileReport]:
        after = None
        while batch := self._claims.pending(maximum=limit, after=after):
            for claim in batch:
                # Pending receipts already carry canonical IDs. Never mint a new
                # claim when replaying a lost completion reply or a failed probe.
                outcome = self._finish(claim)
                self._report = replace(
                    self._report,
                    quarantined=self._report.quarantined + (outcome == OrphanObjectOutcome.RETIRED),
                    missing=self._report.missing + (outcome == OrphanObjectOutcome.MISSING),
                    deferred=self._report.deferred + (outcome is None),
                )
                after = claim.claim_id
                yield self._report

    def _finish(self, claim: OrphanObjectClaim) -> OrphanObjectOutcome | None:
        try:
            if self._retirement.retire(claim.storage_key, claim.claim_id):
                return OrphanObjectOutcome.RETIRED
        except ResourceAdmissionError as error:
            if error.code != "maintenance_storage_failed" or self._storage.pending():
                raise
        # Only a separate successful check-only execution can establish absence.
        # A timeout, busy owner or database failure is never converted to absence.
        try:
            if self._retirement.resolve_missing(claim.storage_key, claim.claim_id):
                return OrphanObjectOutcome.MISSING
        except ResourceAdmissionError as error:
            if error.code != "maintenance_storage_failed" or self._storage.pending():
                raise
            return None
        # Another coordinator may complete this exact claim between calls.
        receipt = self._claims.claim(claim.storage_key, claim.claim_id)
        return None if receipt is None else receipt.outcome
