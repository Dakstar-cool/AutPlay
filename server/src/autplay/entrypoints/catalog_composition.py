"""Optional API catalog runtime with contained requests and deferred control-pool disposal."""

import os
import threading
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from autplay.adapters.filesystem.vault_process import ProcessTreeFactory
from autplay.adapters.linux_process_tree import LinuxCgroupTree
from autplay.adapters.postgresql.catalog_execution import PostgresCatalogExecutionRepository
from autplay.adapters.postgresql.models.catalog_execution import CatalogExecutionRow
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.adapters.postgresql.runtime_database import create_resource_control_engine
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.catalog_io import CatalogProcessCoordinator
from autplay.runtime.settings import ApiSettings


def catalog_process_tree(settings: ApiSettings) -> ProcessTreeFactory:
    if os.name == "nt":
        return WindowsJobTree
    root = settings.catalog_cgroup_root
    if root is None:
        raise ResourceAdmissionError("metadata_catalog_containment_unavailable")
    return lambda: LinuxCgroupTree(root)


def _check_containment(factory: ProcessTreeFactory) -> None:
    """Prove a disposable empty tree can be created and removed, never issue GO."""
    try:
        tree = factory()
        try:
            if tree.seal_if_empty() is None:
                raise ResourceAdmissionError("metadata_catalog_containment_unavailable")
        finally:
            tree.close_after_exit()
    except (OSError, ResourceAdmissionError) as error:
        raise ResourceAdmissionError("metadata_catalog_containment_unavailable") from error


class CatalogRuntime:
    def __init__(
        self,
        settings: ApiSettings,
        *,
        tree_factory: ProcessTreeFactory | None = None,
    ) -> None:
        factory = tree_factory or catalog_process_tree(settings)
        _check_containment(factory)
        self._tree_factory = factory
        self.engine = create_resource_control_engine(settings)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._lock, self._closing, self._disposed = threading.Lock(), False, False
        try:
            self.work = CatalogProcessCoordinator(
                PostgresCatalogExecutionRepository(self._sessions),
                tree_factory=factory,
                proxy=settings.metadata_proxy.get_secret_value()
                if settings.metadata_proxy
                else None,
                on_drained=self._dispose_if_drained,
            )
        except BaseException:
            self.engine.dispose()
            raise

    def check(self) -> ReadinessResult:
        # Other healthy API replicas may own a live row. An expired unclosed row,
        # including PREPARED after ambiguous spawn, requires trusted offline drain.
        try:
            _check_containment(self._tree_factory)
            with self._sessions() as session:
                retained = session.scalar(
                    select(CatalogExecutionRow.execution_id)
                    .where(
                        CatalogExecutionRow.closed_at.is_(None),
                        (
                            (CatalogExecutionRow.deadline_at <= func.clock_timestamp())
                            | (CatalogExecutionRow.io_deadline_at <= func.clock_timestamp())
                        ),
                    )
                    .limit(1)
                )
        except SQLAlchemyError, ResourceAdmissionError:
            return ReadinessResult(False, "music_catalog", "metadata_catalog_unavailable")
        return ReadinessResult(
            retained is None,
            "music_catalog",
            None if retained is None else "metadata_catalog_exit_unconfirmed",
        )

    def _dispose_if_drained(self) -> None:
        with self._lock:
            if self._disposed or not self._closing or self.work.pending():
                return
            self._disposed = True
        self.engine.dispose()

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        with self._lock:
            self._closing = True
        pending = self.work.shutdown(timeout=timeout)
        self._dispose_if_drained()
        return pending
