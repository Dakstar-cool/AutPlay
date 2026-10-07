"""Authenticated one-shot catalog HTTP with retained process exit acknowledgement."""

import re
from collections.abc import Callable
from threading import BoundedSemaphore
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from autplay.adapters.child_process import catalog_child_launch
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.filesystem.vault_process import (
    ChildLaunch,
    ProcessTreeFactory,
    RetainedVaultProcess,
)
from autplay.adapters.postgresql.catalog_execution import (
    CatalogStatus,
    PostgresCatalogExecutionRepository,
)
from autplay.adapters.public_track_metadata import PublicMetadataHttp
from autplay.domain.auth import Principal
from autplay.domain.catalog_execution import CatalogExecutionTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.ports.track_metadata import MetadataProviderError

from .ingest_io import _IngestCoordinator


class CatalogProcessCoordinator(_IngestCoordinator[CatalogExecutionTicket]):
    """Keep this instance for the API lifespan; shutdown must drain before pool disposal."""

    def __init__(
        self,
        repository: PostgresCatalogExecutionRepository,
        *,
        tree_factory: ProcessTreeFactory,
        proxy: str | None = None,
        maximum: int = 2,
        on_drained: Callable[[], None] | None = None,
        launch: ChildLaunch | None = None,
    ) -> None:
        super().__init__(
            repository,
            None,
            tree_factory=tree_factory,
            maximum=maximum,
            launch=launch or (lambda: catalog_child_launch(proxy=proxy)),
            on_drained=on_drained,
            error_prefix="metadata",
        )
        self._catalog_repository = repository
        self._owner_run_id = uuid4()
        self._requests = BoundedSemaphore(maximum)

    def http(self, principal: Principal) -> PublicMetadataHttp:
        return _CatalogHttp(self, principal)

    def get(self, principal: Principal, url: str) -> bytes | None:
        if not self._requests.acquire(blocking=False):
            raise MetadataProviderError("metadata_provider_busy", retry_after_seconds=2)
        try:
            with self._lock:
                if self._closing or len(self._entries) >= self._maximum:
                    raise MetadataProviderError("metadata_provider_busy", retry_after_seconds=2)
            return self._get(principal, url)
        finally:
            self._requests.release()

    def _get(self, principal: Principal, url: str) -> bytes | None:
        def request(
            child: RetainedVaultProcess[CatalogExecutionTicket],
            running: CatalogStatus,
            freeze: Callable[[], None],
        ) -> bytes | None:
            del running
            child.go(
                {
                    "version": 1,
                    "execution_id": str(child.ticket.execution_id),
                    "request_id": str(child.ticket.request_id),
                    "url": url,
                }
            )
            tag, document, payload = child.catalog_result()
            if tag == b"E":
                code, retry, seconds = (
                    document.get("code"),
                    document.get("retryable"),
                    document.get("retry_after_seconds"),
                )
                if (
                    payload is not None
                    or set(document) != {"code", "retryable", "retry_after_seconds"}
                    or not isinstance(code, str)
                    or re.fullmatch(r"metadata_[a-z_]{1,80}", code) is None
                    or type(retry) is not bool
                    or type(seconds) is not int
                    or not 1 <= seconds <= 86400
                ):
                    raise ChildProtocolError()
                raise MetadataProviderError(code, retryable=retry, retry_after_seconds=seconds)
            if document:
                raise ChildProtocolError()
            freeze()
            return payload

        try:
            ticket = self._catalog_repository.plan(principal, self._owner_run_id)
            result = self._execute(ticket, request)
            # Even data already received cannot cross a subsequently revoked session.
            self._catalog_repository.require_current(ticket)
            return result
        except ResourceAdmissionError as error:
            raise MetadataProviderError(
                "metadata_provider_busy"
                if error.code.endswith("_busy")
                else "metadata_provider_unavailable",
                retry_after_seconds=2,
            ) from error
        except (SQLAlchemyError, ChildProtocolError, OSError) as error:
            raise MetadataProviderError("metadata_provider_unavailable") from error


class _CatalogHttp(PublicMetadataHttp):
    def __init__(self, coordinator: CatalogProcessCoordinator, principal: Principal) -> None:
        # Do not construct an HTTP client (or a default gate) in the API process.
        self._coordinator, self._principal = coordinator, principal

    def get(self, url: str, *, artwork: bool = False, post: bytes | None = None) -> bytes | None:
        if artwork or post is not None:
            raise MetadataProviderError("metadata_url_rejected", retryable=False)
        return self._coordinator.get(self._principal, url)
