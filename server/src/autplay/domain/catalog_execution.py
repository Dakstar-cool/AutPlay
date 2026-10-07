"""Real authenticated ownership for one contained public catalog request."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CatalogExecutionTicket:
    execution_id: UUID
    owner_run_id: UUID
    user_id: UUID
    device_id: UUID
    session_id: UUID
    authority_generation: int
    request_id: UUID

    @property
    def kind(self) -> str:
        return "MUSIC_CATALOG"
