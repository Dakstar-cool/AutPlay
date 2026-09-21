"""Injectable complete WORK handler; canonical cleanup remains separately drainable."""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from autplay.adapters.postgresql.vault_uow import TransactionalIngestRepository
from autplay.application.ingest_cleanup import IngestCleanupService
from autplay.application.job_worker import JobExecutionContext
from autplay.application.vault_ingest import IngestSession, VaultIngestHandler
from autplay.domain.discovery import AcquisitionAuthorizationReceipt
from autplay.domain.ingest_execution import IngestExecutionTicket
from autplay.domain.jobs import JobLease, LeaseFence, RetryableJobError, TerminalJobError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    VerifiedStagedFile,
)
from autplay.ports.discovery import AcquisitionBoundaryAuthorizer

from .ingest_io import IngestProcessCoordinator, IngestWork


class IngestPlanner(Protocol):
    def plan(
        self, upload_id: UUID, fence: LeaseFence, owner: UUID
    ) -> IngestExecutionTicket | None: ...
    def defer(self, fence: LeaseFence, upload_id: UUID, blockers: tuple[UUID, ...]) -> None: ...


class _BoundRepository:
    def __init__(self, repository: TransactionalIngestRepository, work: IngestWork) -> None:
        self._repository, self._work = repository, work

    def start_ingest(
        self, upload_session_id: UUID, job_id: UUID, *, fence: LeaseFence
    ) -> IngestSession | None:
        return self._repository.start_ingest(
            upload_session_id, job_id, fence=fence, execution=self._work.running
        )

    def prepare_commit(
        self,
        session: IngestSession,
        verified: VerifiedStagedFile,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
    ) -> str:
        return self._repository.prepare_commit(session, verified, metadata, evidence)

    def finalize_published(
        self,
        session: IngestSession,
        storage_key: OpaqueStorageKey,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
        *,
        reused: bool,
        authorization_receipt: AcquisitionAuthorizationReceipt | None = None,
    ) -> bool:
        self._work.freeze_renewals()
        return self._repository.finalize_published(
            session,
            storage_key,
            metadata,
            evidence,
            reused=reused,
            authorization_receipt=authorization_receipt,
        )

    def quarantine(self, session: IngestSession, code: str) -> None:
        self._work.freeze_renewals()
        self._repository.quarantine(session, code)


class ControlledVaultIngestHandler:
    """Production enablement still requires measured internal CPU admission."""

    def __init__(
        self,
        planner: IngestPlanner,
        coordinator: IngestProcessCoordinator,
        repository: TransactionalIngestRepository,
        cleanup: IngestCleanupService,
        *,
        cleanup_pending: Callable[[], tuple[UUID, ...]],
        source_authorizer: AcquisitionBoundaryAuthorizer | None = None,
        minimum_free_bytes: int = 0,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        self._planner, self._coordinator, self._repository = planner, coordinator, repository
        self._cleanup, self._cleanup_pending = cleanup, cleanup_pending
        self._stop_requested = stop_requested
        self._authorizer, self._minimum_free_bytes, self._owner = (
            source_authorizer,
            minimum_free_bytes,
            uuid4(),
        )

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        def check_cancelled() -> None:
            if self._stop_requested():
                raise RetryableJobError("worker.stopping")
            context.raise_if_cancelled()

        check_cancelled()
        raw = lease.payload.get("upload_session_id")
        if not isinstance(raw, str):
            raise TerminalJobError("vault.invalid_job_payload")
        try:
            upload_id = UUID(raw)
        except ValueError as error:
            raise TerminalJobError("vault.invalid_job_payload") from error
        try:
            ticket = self._planner.plan(upload_id, lease.fence, self._owner)
            if ticket is not None:

                def work(current: IngestWork) -> None:
                    VaultIngestHandler(
                        repository=_BoundRepository(self._repository, current),
                        storage=current.storage,
                        media=current.storage,
                        fingerprints=current.storage,
                        source_authorizer=self._authorizer,
                        minimum_free_bytes=self._minimum_free_bytes,
                    )(context, lease)

                self._coordinator.run(ticket, work, check_cancelled=check_cancelled)
        except ResourceAdmissionError as error:
            if error.code in {
                "ingest_execution_busy",
                "resource_execution_busy",
                "ingest_cleanup_busy",
                "ingest_io_deadline_expired",
                "internal_io_busy",
                "internal_io_budget_unconfigured",
                "internal_io_budget_mismatch",
            }:
                self._planner.defer(
                    lease.fence, upload_id, self._coordinator.pending() + self._cleanup_pending()
                )
            raise RetryableJobError(error.code) from error
        # Finalization owns a durable cleanup intent. A cleanup failure cannot
        # turn an already published upload into another publication attempt.
        try:
            self._cleanup.clean(upload_id)
        except Exception:
            context.checkpoint({"stage": "CLEANUP_DEFERRED"})
