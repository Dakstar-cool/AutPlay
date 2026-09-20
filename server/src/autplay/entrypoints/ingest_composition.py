"""Worker-owned WORK/cleanup lifecycle; no direct filesystem/media fallback."""

import os
import threading
from collections.abc import Callable
from time import monotonic
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.filesystem.vault_process import ProcessTreeFactory
from autplay.adapters.linux_process_tree import LinuxCgroupTree
from autplay.adapters.postgresql.ingest_cleanup import PostgresIngestCleanupRepository
from autplay.adapters.postgresql.ingest_execution import PostgresIngestExecutionRepository
from autplay.adapters.postgresql.internal_io import internal_io_policy
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.runtime_database import create_resource_control_engine
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.application.ingest_cleanup import IngestCleanupService
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import VaultError, VaultLimits
from autplay.ports.discovery import AcquisitionBoundaryAuthorizer
from autplay.runtime.controlled_ingest import ControlledVaultIngestHandler
from autplay.runtime.ingest_io import IngestCleanupCoordinator, IngestProcessCoordinator
from autplay.runtime.settings import WorkerSettings


def worker_process_tree(settings: WorkerSettings) -> ProcessTreeFactory:
    if os.name == "nt":
        return WindowsJobTree
    root = settings.worker_cgroup_root
    if root is None:
        raise ResourceAdmissionError("resource_process_tree_unavailable")
    return lambda: LinuxCgroupTree(root)


class IngestWorkerRuntime:
    def __init__(
        self,
        settings: WorkerSettings,
        sessions: sessionmaker[Session],
        *,
        source_authorizer: AcquisitionBoundaryAuthorizer | None = None,
        on_drained: Callable[[], None] | None = None,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        self.tree_factory = worker_process_tree(settings)
        self.engine = create_resource_control_engine(settings)
        control = sessionmaker(self.engine, expire_on_commit=False)
        try:
            with control.begin() as session:
                lock_resource_admission(session)
                internal_io_policy(session)
        except BaseException:
            self.engine.dispose()
            raise
        self._lock, self._closing, self._disposed = threading.Lock(), False, False
        self._on_drained = on_drained
        self._after: UUID | None = None
        child_settings = IngestChildSettings(
            settings.vault_root,
            limits=VaultLimits(
                max_object_bytes=settings.vault_max_object_bytes,
                max_chunk_bytes=settings.vault_max_chunk_bytes,
                io_block_bytes=settings.vault_stream_block_bytes,
            ),
            tool_timeout_seconds=settings.vault_tool_timeout_seconds,
            tool_max_output_bytes=settings.vault_tool_max_output_bytes,
        )
        work_repository = PostgresIngestExecutionRepository(control)
        self.cleanup_repository = PostgresIngestCleanupRepository(control)
        self.work = IngestProcessCoordinator(
            work_repository,
            child_settings,
            tree_factory=self.tree_factory,
            on_drained=self._dispose_if_drained,
        )
        self.cleanup = IngestCleanupCoordinator(
            self.cleanup_repository,
            child_settings,
            tree_factory=self.tree_factory,
            on_drained=self._dispose_if_drained,
        )
        self.service = IngestCleanupService(self.cleanup_repository, self.cleanup)
        self.handler = ControlledVaultIngestHandler(
            work_repository,
            self.work,
            TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions)),
            self.service,
            cleanup_pending=self.cleanup.pending,
            source_authorizer=source_authorizer,
            minimum_free_bytes=settings.vault_low_disk_bytes,
            stop_requested=stop_requested,
        )

    def drain_cleanup(self) -> int:
        """Try one canonical intent per tick; keyset rotation avoids a blocked head."""
        pending = self.cleanup_repository.pending(maximum=1, after=self._after)
        if not pending:
            self._after = None
            return 0
        self._after = pending[0]
        try:
            return int(self.service.clean(pending[0]))
        except ResourceAdmissionError, SQLAlchemyError, VaultError, ChildProtocolError:
            return 0

    def request_stop(self) -> None:
        """Interrupt retained waits without disposing the caller's metadata pool."""
        self.work.shutdown(timeout=0)
        self.cleanup.shutdown(timeout=0)

    def _dispose_if_drained(self) -> None:
        with self._lock:
            if self._disposed or not self._closing or self.work.pending() or self.cleanup.pending():
                return
            self._disposed = True
        self.engine.dispose()
        if self._on_drained is not None:
            self._on_drained()

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not 0 <= timeout <= 5:
            raise ValueError("ingest_shutdown_invalid")
        with self._lock:
            self._closing = True
        deadline = monotonic() + timeout
        self.work.shutdown(timeout=0)
        self.cleanup.shutdown(timeout=0)
        self.work.shutdown(timeout=max(0, deadline - monotonic()))
        self.cleanup.shutdown(timeout=max(0, deadline - monotonic()))
        pending = self.work.pending() + self.cleanup.pending()
        self._dispose_if_drained()
        return pending
