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
