"""Independent consent intent evidence, retained outside PostgreSQL restore generations."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class TrainingConsentEvidenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("training_consent_evidence_unavailable")


@dataclass(frozen=True)
class TrainingConsentIntent:
    owner_tag: str
    operation_id: UUID
    actor_tag: str | None
    request_sha256: str
    previous_policy_sha256: str
    decision: str
    revision: int
    changed_at: datetime


@dataclass(frozen=True)
class TrainingConsentHistory:
    operations: Mapping[UUID, TrainingConsentIntent]
    latest: Mapping[str, TrainingConsentIntent]


@dataclass(frozen=True)
class SonaEventErasureIntent:
    owner_tag: str
    source_event_id: UUID
    source_server_sequence: int
    source_watermark: int
    erasure_event_id: UUID
    erased_at: datetime
    ledger_sequence: int = 0
    ledger_digest: str = ""
    # Version-2 negative evidence, captured only from a verified feedback descendant.
    feedback_parent_source_event_id: UUID | None = None
    feedback_parent_ledger_sequence: int = 0
    feedback_parent_ledger_digest: str = ""
    source_event_type: str | None = None


@dataclass(frozen=True)
class CompactedErasureFloor:
    """Independent owner floor; contains no expired event or aggregate identity."""

    owner_tag: str
    floor_sequence: int
    recorded_at: datetime
    ledger_sequence: int = 0
    ledger_digest: str = ""
