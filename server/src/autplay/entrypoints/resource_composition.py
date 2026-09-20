"""Explicit ownership for a dedicated admission pool and retained Vault processes."""

from __future__ import annotations

import threading
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from autplay.adapters.postgresql.resource_admission import (
    SqlAlchemyResourceAdmissionUnitOfWorkFactory,
)
from autplay.adapters.postgresql.runtime_database import (
    RuntimeSettings,
    create_resource_control_engine,
)
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.runtime.vault_io import VaultIoCoordinator


class ResourceIoRuntime:
    """Keep control connections alive while any exact execution still needs closure.

    Composition must enable this runtime only with all byte/worker paths enforced.
    Construction alone does not start threads, connect to a DB or enable routes.
    """

    def __init__(self, settings: RuntimeSettings, *, maximum: int = 16) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 64:
            raise ValueError("invalid local resource process bound")
        self.engine = create_resource_control_engine(settings)
        self.service = ResourceAdmissionService(
            SqlAlchemyResourceAdmissionUnitOfWorkFactory(
                sessionmaker(self.engine, expire_on_commit=False)
            )
        )
        self._dispose_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._disposed = threading.Event()
        self._started = False
        self.coordinator = VaultIoCoordinator(
            self.service, maximum=maximum, on_drained=self._dispose
        )

    @property
    def disposed(self) -> bool:
        return self._disposed.is_set()

    def _dispose(self) -> None:
        with self._dispose_lock:
            if not self._disposed.is_set():
                self.engine.dispose()
                self._disposed.set()

    def start(self) -> None:
        with self._start_lock:
            if self._started or self.disposed:
                raise RuntimeError("resource runtime cannot start twice or after shutdown")
            self._started = True
            try:
                self.coordinator.start()
            except BaseException:
                self._dispose()
                raise

    async def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        pending = await self.coordinator.shutdown(timeout=timeout)
        if not pending:
            self._dispose()
        # Otherwise the same coordinator disposes this pool only after all retained
        # worker transactions and durable exit acknowledgements actually settle.
        return pending
