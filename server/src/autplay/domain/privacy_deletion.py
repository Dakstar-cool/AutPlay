"""Independent deletion evidence without raw account identifiers or payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class DeletionEvidenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("deletion_evidence_unavailable")


@dataclass(frozen=True, slots=True)
class DeletionEvidence:
    owner_tag: str
    request_id: UUID
    accepted_at: datetime
    completed_at: datetime | None = None
    protect_until: datetime | None = None
    removed_rows: int | None = None


@dataclass(frozen=True, slots=True)
class DeletionRequestEvidence:
    owner_tag: str
    request_id: UUID
    request_sha256: str
    receipt_sha256: str
    decision: str
    decided_at: datetime
    cancel_operation_id: UUID | None = None
    cancel_request_sha256: str | None = None
    cancelled_at: datetime | None = None
