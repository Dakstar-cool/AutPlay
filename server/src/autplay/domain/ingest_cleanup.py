"""Finalized staging cleanup has immutable authority independent of job/source leases."""

from dataclasses import dataclass
from uuid import UUID

from .vault import OpaqueStorageKey, VerifiedStagedFile


@dataclass(frozen=True, slots=True)
class IngestCleanupClaim:
    claim_id: UUID
    staging_key: OpaqueStorageKey
    expected: VerifiedStagedFile
    completed_execution_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class IngestCleanupTicket:
    execution_id: UUID
    owner_run_id: UUID
    claim_id: UUID
    staging_key: OpaqueStorageKey
    expected: VerifiedStagedFile

    @property
    def kind(self) -> str:
        return "VAULT_INGEST_CLEANUP"
