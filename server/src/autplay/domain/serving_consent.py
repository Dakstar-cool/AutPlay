"""Independent R1C serving-purpose consent intent, outside PostgreSQL restore state."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

SONA_SERVING_PURPOSE = "SONA_R1C_DESCENDANT_MODEL_SERVING_V1"


class ServingConsentEvidenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("serving_consent_evidence_unavailable")


@dataclass(frozen=True)
class ServingConsentIntent:
    owner_tag: str
    operation_id: UUID
    actor_tag: str | None
    request_sha256: str
    previous_policy_sha256: str
    decision: str
    generation: int
    changed_at: datetime
    purpose: str = SONA_SERVING_PURPOSE


@dataclass(frozen=True)
class ServingConsentHistory:
    operations: Mapping[UUID, ServingConsentIntent]
    latest: Mapping[tuple[str, str], ServingConsentIntent]
