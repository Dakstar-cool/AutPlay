"""Resume canonical finalized-staging intents without restarting publication."""

from typing import Protocol
from uuid import UUID, uuid4

from autplay.domain.ingest_cleanup import IngestCleanupClaim, IngestCleanupTicket


class IngestCleanupRepository(Protocol):
    def get(self, claim_id: UUID) -> IngestCleanupClaim | None: ...
    def pending(self, *, maximum: int = 100, after: UUID | None = None) -> tuple[UUID, ...]: ...
    def complete(self, claim: IngestCleanupClaim, execution_id: UUID) -> None: ...


class IngestCleanupRunner(Protocol):
    def run(self, ticket: IngestCleanupTicket) -> UUID: ...


class IngestCleanupService:
    def __init__(self, repository: IngestCleanupRepository, runner: IngestCleanupRunner) -> None:
        self._repository, self._runner, self._owner = repository, runner, uuid4()

    def clean(self, upload_id: UUID) -> bool:
        claim = self._repository.get(upload_id)
        if claim is None:
            return False
        if claim.completed_execution_id is not None:
            return True
        ticket = IngestCleanupTicket(
            uuid4(), self._owner, claim.claim_id, claim.staging_key, claim.expected
        )
        execution_id = self._runner.run(ticket)
        self._repository.complete(claim, execution_id)
        return True
