"""One retained metadata tree with durable provider pacing and bounded shutdown."""

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from uuid import UUID

from autplay.adapters.child_process import metadata_child_launch
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.metadata_process import ProcessMetadataHttp, ProcessMetadataWork
from autplay.adapters.filesystem.vault_process import (
    ChildLaunch,
    ProcessTreeFactory,
    RetainedVaultProcess,
)
from autplay.adapters.postgresql.metadata_execution import (
    MetadataStatus,
    PostgresMetadataExecutionRepository,
)
from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.domain.metadata_execution import MetadataExecutionTicket

from .ingest_io import _IngestCoordinator


@dataclass(frozen=True)
class MetadataWork:
    bytes: ProcessMetadataWork
    provider: MusicBrainzMetadataProvider
    running: MetadataStatus
    freeze_renewals: Callable[[], None]


class MetadataProcessCoordinator(_IngestCoordinator[MetadataExecutionTicket]):
    def __init__(
        self,
        repository: PostgresMetadataExecutionRepository,
        settings: IngestChildSettings,
        *,
        tree_factory: ProcessTreeFactory,
        proxy: str | None = None,
        on_drained: Callable[[], None] | None = None,
        launch: ChildLaunch | None = None,
    ) -> None:
        super().__init__(
            repository,
            settings,
            tree_factory=tree_factory,
            launch=launch or (lambda: metadata_child_launch(proxy=proxy)),
            on_drained=on_drained,
            error_prefix="metadata",
        )
        self._metadata_repository = repository

    def run[T](
        self,
        ticket: MetadataExecutionTicket,
        action: Callable[[MetadataWork], T],
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> T:
        def phases(
            child: RetainedVaultProcess[MetadataExecutionTicket],
            running: MetadataStatus,
            freeze: Callable[[], None],
        ) -> T:
            def begin(request: UUID) -> None:
                while True:
                    child.deadline.check()
                    if self._metadata_repository.provider_begin(running, request):
                        return
                    sleep(0.05)

            work = ProcessMetadataWork(
                child,
                self._settings,
                begin,
                lambda request: self._metadata_repository.provider_end(running, request),
            )
            work.begin()
            result = action(
                MetadataWork(
                    work, MusicBrainzMetadataProvider(ProcessMetadataHttp(work)), running, freeze
                )
            )
            freeze()
            work.finish()
            return result

        return self._execute(ticket, phases, check_cancelled=check_cancelled)
