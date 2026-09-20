"""Internal maintenance capacity is independent of account transfer authority."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .resource_execution import ExecutionState, ProcessIdentity
from .vault import OpaqueStorageKey


class MaintenanceAction(StrEnum):
    CLEANUP = "CLEANUP"
    SCRATCH = "SCRATCH"
    ORPHAN_OBJECT = "ORPHAN_OBJECT"
    ORPHAN_MISSING = "ORPHAN_MISSING"
    INVENTORY = "INVENTORY"
    UPLOAD_CLEANUP = "UPLOAD_CLEANUP"


@dataclass(frozen=True, slots=True)
class MaintenanceTicket:
    execution_id: UUID
    owner_run_id: UUID
    provider_execution_id: UUID | None
    claim_id: UUID
    action: MaintenanceAction
    storage_key: OpaqueStorageKey | None = None

    def __post_init__(self) -> None:
        if self.action == MaintenanceAction.INVENTORY:
            if (
                self.provider_execution_id is not None
                or self.storage_key is not None
                or self.claim_id != self.execution_id
            ):
                raise ValueError("maintenance_target_invalid")
        elif self.action in (
            MaintenanceAction.ORPHAN_OBJECT,
            MaintenanceAction.ORPHAN_MISSING,
            MaintenanceAction.UPLOAD_CLEANUP,
        ):
            if self.provider_execution_id is not None or self.storage_key is None:
                raise ValueError("maintenance_target_invalid")
        elif self.provider_execution_id is None or self.storage_key is not None:
            raise ValueError("maintenance_target_invalid")

    @property
    def kind(self) -> str:
        return "VAULT_MAINTENANCE"


@dataclass(frozen=True, slots=True)
class MaintenanceStatus:
    ticket: MaintenanceTicket
    state: ExecutionState
    child: ProcessIdentity | None
