"""Owned metadata runtime with an independent control pool and deferred disposal."""

import threading
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.postgresql.internal_io import internal_io_policy
from autplay.adapters.postgresql.metadata_execution import PostgresMetadataExecutionRepository
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.runtime_database import create_resource_control_engine
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import VaultLimits
from autplay.runtime.controlled_metadata import ControlledMetadataHandler
from autplay.runtime.metadata_io import MetadataProcessCoordinator
from autplay.runtime.settings import WorkerSettings

from .ingest_composition import worker_process_tree


class MetadataWorkerRuntime:
    def __init__(
        self,
        settings: WorkerSettings,
        sessions: sessionmaker[Session],
        *,
        on_drained: Callable[[], None] | None = None,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        tree_factory = worker_process_tree(settings)
        self.engine = create_resource_control_engine(settings)
        control = sessionmaker(self.engine, expire_on_commit=False)
        try:
            with control.begin() as session:
                lock_resource_admission(session)
                if internal_io_policy(session).workload_version < 2:
                    raise ResourceAdmissionError("metadata_budget_unconfigured")
            self._lock, self._closing, self._disposed = threading.Lock(), False, False
            self._on_drained = on_drained
            repository = PostgresMetadataExecutionRepository(control)
            self.work = MetadataProcessCoordinator(
                repository,
                IngestChildSettings(
                    settings.vault_root,
                    limits=VaultLimits(
                        max_object_bytes=settings.vault_max_object_bytes,
                        max_chunk_bytes=settings.vault_max_chunk_bytes,
                        io_block_bytes=settings.vault_stream_block_bytes,
                    ),
                    tool_timeout_seconds=settings.vault_tool_timeout_seconds,
                    tool_max_output_bytes=settings.vault_tool_max_output_bytes,
                ),
                tree_factory=tree_factory,
                proxy=settings.metadata_proxy.get_secret_value()
                if settings.metadata_proxy
                else None,
                on_drained=self._dispose_if_drained,
            )
            self.handler = ControlledMetadataHandler(
                sessions,
                repository,
                self.work,
                acoustid_key=settings.acoustid_client_key.get_secret_value(),
                stop_requested=stop_requested,
            )
        except BaseException:
            self.engine.dispose()
            raise

    def request_stop(self) -> None:
        self.work.shutdown(timeout=0)

    def _dispose_if_drained(self) -> None:
        with self._lock:
            if self._disposed or not self._closing or self.work.pending():
                return
            self._disposed = True
        self.engine.dispose()
        if self._on_drained is not None:
            self._on_drained()

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        with self._lock:
            self._closing = True
        pending = self.work.shutdown(timeout=timeout)
        self._dispose_if_drained()
        return pending
