"""Evidence is independent of the PostgreSQL backup and retained without automatic expiry."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from autplay.domain.privacy_deletion import DeletionEvidence, DeletionRequestEvidence


class DeletionRequestLedger(Protocol):
    def owner_tag(self, user_id: UUID) -> str: ...

    def read(self) -> tuple[DeletionEvidence, ...]: ...

    def request_read(self) -> tuple[DeletionRequestEvidence, ...]: ...

    def request_coverage_started_at(self) -> datetime: ...

    def request_record(self, evidence: DeletionRequestEvidence) -> DeletionRequestEvidence: ...


class DeletionLedger(DeletionRequestLedger, Protocol):
    def prepare(self, user_id: UUID, request_id: UUID, accepted_at: datetime) -> DeletionEvidence:
        """Durably append noncancellable intent before any owner data is purged."""
        ...

    def complete(
        self, owner_tag: str, request_id: UUID, completed_at: datetime, removed_rows: int
    ) -> DeletionEvidence:
        """Record completion only after the database transaction and zero-owner verification."""
        ...
