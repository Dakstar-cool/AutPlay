"""Bind metadata publication to the exact owned execution and audio snapshot."""

from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.postgresql.metadata_execution import PostgresMetadataExecutionRepository
from autplay.application.job_worker import JobExecutionContext
from autplay.application.track_metadata import TrackMetadataService
from autplay.application.track_metadata_worker import TrackMetadataHandler
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.resource_admission import ResourceAdmissionError

from .metadata_io import MetadataProcessCoordinator, MetadataWork


class ControlledMetadataHandler:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        repository: PostgresMetadataExecutionRepository,
        coordinator: MetadataProcessCoordinator,
        *,
        acoustid_key: str = "",
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        self._sessions, self._repository, self._coordinator = sessions, repository, coordinator
        self._key, self._stop, self._owner = acoustid_key, stop_requested, uuid4()

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        def check_cancelled() -> None:
            if self._stop():
                raise RetryableJobError("worker.stopping")
            context.raise_if_cancelled()

        check_cancelled()
        raw, generation = lease.payload.get("user_track_ref_id"), lease.payload.get("generation")
        if not isinstance(raw, str) or type(generation) is not int or generation <= 0:
            raise TerminalJobError("metadata_invalid_job_payload")
        try:
            ref_id = UUID(raw)
        except ValueError as error:
            raise TerminalJobError("metadata_invalid_job_payload") from error
        ticket = self._repository.plan(ref_id, generation, context.fence, self._owner)

        def action(work: MetadataWork) -> None:
            TrackMetadataHandler(
                TrackMetadataService(self._sessions, execution=work.running),
                work.bytes,
                work.provider,
                audio=ticket.audio,
                freeze_renewals=work.freeze_renewals,
                acoustid_key=self._key,
            )(context, lease)

        try:
            self._coordinator.run(ticket, action, check_cancelled=check_cancelled)
        except ResourceAdmissionError as error:
            if error.code in {
                "metadata_execution_busy",
                "metadata_budget_unconfigured",
                "internal_io_busy",
                "internal_io_budget_unconfigured",
                "internal_io_budget_mismatch",
            }:
                self._repository.defer(ticket)
            raise RetryableJobError(error.code) from error
        except ChildProtocolError as error:
            raise RetryableJobError("metadata_child_failed") from error
