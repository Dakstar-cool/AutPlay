"""Dependency-injected CPU worker process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.jamendo import JamendoProvider
from autplay.adapters.postgresql.controlled_discovery import PostgresControlledDiscoveryRepository
from autplay.adapters.postgresql.discovery_automation_runtime import (
    SqlAlchemyDiscoveryAutomationRepository,
)
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.postgresql.web_admin import SqlAlchemyWebAdminRepository
from autplay.adapters.system import Uuid7Generator
from autplay.application.account_recovery import cleanup_expired_recovery_operations
from autplay.application.bulk_discovery import BulkDiscoveryService
from autplay.application.discovery_acquisition import (
    ControlledDiscoveryAcquisitionHandler,
    ManualDiscoveryBoundaryAuthorizer,
    StandardAnalysisHandler,
)
from autplay.application.discovery_automation import (
    DISCOVERY_SCAN_JOB,
    DiscoveryAutomationService,
    DiscoveryScanHandler,
)
from autplay.application.imports import ImportJobHandler
from autplay.application.job_worker import (
    JobHandler,
    JobHandlerRegistry,
    JobWorker,
    JobWorkerSettings,
)
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.application.profile_pairing import (
    cleanup_expired_device_admissions,
    cleanup_expired_pairing_receipts,
)
from autplay.application.public_access import cleanup_expired_public_access
from autplay.application.social import SocialService
from autplay.domain.jobs import JobKey, RetryPolicy
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.training_consent import TrainingConsentEvidenceError
from autplay.domain.vault import VaultLimits
from autplay.entrypoints.ingest_composition import IngestWorkerRuntime
from autplay.entrypoints.resource_composition import ResourceIoRuntime
from autplay.ports.ids import IdGenerator
from autplay.ports.transactions import JobUnitOfWorkFactory
from autplay.runtime.discovery_io import DiscoveryIoExecutor
from autplay.runtime.logging import configure_json_logging
from autplay.runtime.settings import SettingsLoadError, load_worker_settings

SERVICE_NAME = "autplay-worker-cpu"
_LOGGER = logging.getLogger("autplay.worker_cpu")


@contextmanager
def worker_stop_scope(
    stop: threading.Event,
    on_stop: Callable[[], None],
    *,
    join_timeout: Callable[[], float] = lambda: 1,
) -> Iterator[None]:
    """Observe signals while the main thread is inside any retained byte operation."""
    previous: dict[signal.Signals, signal._HANDLER] = {}

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    def observe() -> None:
        stop.wait()
        on_stop()

    observer = threading.Thread(target=observe, name="cpu-worker-stop", daemon=True)
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
    try:
        observer.start()
        yield
    finally:
        stop.set()
        if observer.ident is not None:
            observer.join(timeout=min(1, join_timeout()))
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def build_cpu_worker(
    *,
    uow_factory: JobUnitOfWorkFactory,
    ids: IdGenerator,
    handlers: Mapping[JobKey, JobHandler] | None = None,
    settings: JobWorkerSettings | None = None,
    worker_id: str | None = None,
) -> JobWorker:
    """Build a CPU-only worker with an opaque process-level identifier."""

    process_worker_id = worker_id if worker_id is not None else f"cpu-{ids.new()}"
    return JobWorker(
        uow_factory=uow_factory,
        worker_id=process_worker_id,
        registry=JobHandlerRegistry(handlers),
        settings=settings,
    )


def vault_ingest_handlers(handler: JobHandler) -> Mapping[JobKey, JobHandler]:
    """Return the single P06 CPU-only worker registration at priority three.

    Priority is persisted when the upload completion enqueues ``vault.ingest``;
    this registry deliberately contains no GPU or external-acquisition handler.
    """

    return {JobKey("vault.ingest", 1): handler}


def import_handlers(handler: ImportJobHandler) -> Mapping[JobKey, JobHandler]:
    """Return the P10 CPU-only resumable import registration."""

    return {JobKey("library.import", 1): handler}


def discovery_handlers(handler: JobHandler) -> Mapping[JobKey, JobHandler]:
    """Return the disabled-by-default manual A1B acquisition registration."""

    return {JobKey("discovery.acquire", 1): handler}


def discovery_scan_handlers(handler: DiscoveryScanHandler) -> Mapping[JobKey, JobHandler]:
    """Return the separately gated A1C release-scan registration."""

    return {DISCOVERY_SCAN_JOB: handler}


def standard_analysis_handlers(handler: StandardAnalysisHandler) -> Mapping[JobKey, JobHandler]:
    """Keep already-ingested baseline analysis drainable after provider disablement."""

    return {JobKey("audio.standard_analysis", 1): handler}


def run_cpu_worker(
    worker: JobWorker,
    stop_event: threading.Event | None = None,
    *,
    profile_receipt_cleanup: Callable[[], int] | None = None,
    web_admin_cleanup: Callable[[], int] | None = None,
    discovery_cleanup: Callable[[], int] | None = None,
    discovery_dispatch: Callable[[], int] | None = None,
    ingest_cleanup: Callable[[], int] | None = None,
    account_purge: Callable[[], int] | None = None,
    cleanup_interval: timedelta = timedelta(minutes=5),
    discovery_dispatch_interval: timedelta = timedelta(minutes=5),
) -> None:
    """Run until SIGINT/SIGTERM or a supplied cooperative stop event."""

    process_stop = stop_event or threading.Event()
    previous_handlers: dict[signal.Signals, signal._HANDLER] = {}

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        process_stop.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
    if cleanup_interval <= timedelta(0) or cleanup_interval > timedelta(hours=1):
        raise ValueError("profile receipt cleanup interval must be within (0, 1 hour]")
    if discovery_dispatch_interval != timedelta(minutes=5):
        raise ValueError("discovery scheduler interval must remain exactly five minutes")
    next_cleanup_at = 0.0
    next_discovery_dispatch_at = 0.0
    try:
        while not process_stop.is_set():
            current = monotonic()
            if current >= next_cleanup_at:
                if account_purge is not None:
                    try:
                        account_purge()
                    except DeletionEvidenceError, SQLAlchemyError:
                        _LOGGER.error("account_purge_unavailable")
                if profile_receipt_cleanup is not None:
                    try:
                        profile_receipt_cleanup()
                    except SQLAlchemyError:
                        _LOGGER.exception("profile_receipt_cleanup_failed")
                if web_admin_cleanup is not None:
                    try:
                        web_admin_cleanup()
                    except SQLAlchemyError:
                        _LOGGER.exception("web_admin_cleanup_failed")
                if discovery_cleanup is not None:
                    try:
                        discovery_cleanup()
                    except SQLAlchemyError:
                        _LOGGER.exception("discovery_cleanup_failed")
                next_cleanup_at = current + cleanup_interval.total_seconds()
            if current >= next_discovery_dispatch_at:
                if discovery_dispatch is not None:
                    try:
                        discovery_dispatch()
                    except SQLAlchemyError:
                        _LOGGER.exception("discovery_dispatch_failed")
                next_discovery_dispatch_at = current + discovery_dispatch_interval.total_seconds()
            if ingest_cleanup is not None:
                ingest_cleanup()
            tick = worker.run_once()
            if tick.outcome.value == "IDLE":
                process_stop.wait(worker.idle_poll_interval.total_seconds())
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate settings and run the CPU worker with an empty P03 registry."""

    parser = argparse.ArgumentParser(prog=SERVICE_NAME)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration without touching PostgreSQL",
    )
    mode.add_argument(
        "--check-readiness",
        action="store_true",
        help="verify PostgreSQL connectivity and the exact migration head",
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="verify readiness, execute one bounded poll iteration, and exit",
    )
    namespace = parser.parse_args(arguments)
    try:
        runtime_settings = load_worker_settings()
    except SettingsLoadError as error:
        sys.stderr.write(
            json.dumps({"event": error.code, "service": SERVICE_NAME}, separators=(",", ":")) + "\n"
        )
        return 2
    if namespace.check_config:
        sys.stdout.write('{"status":"ok","service":"autplay-worker-cpu"}\n')
        return 0

    configure_json_logging(service=SERVICE_NAME, level=runtime_settings.log_level)
    engine = create_runtime_engine(runtime_settings)
    ingest_runtime: IngestWorkerRuntime | None = None
    provider_runtime: ResourceIoRuntime | None = None
    process_stop = threading.Event()
    result = 0
    pending: tuple[UUID, ...] = ()
    stop_scope = ExitStack()
    try:
        from autplay.entrypoints.privacy_deletion import (
            build_privacy_deletion_service,
            enforce_privacy_restore_guard,
        )

        enforce_privacy_restore_guard(runtime_settings, engine)
        privacy_deletion = build_privacy_deletion_service(runtime_settings, engine)
        readiness = PostgreSQLReadinessProbe(engine).check()
        if not readiness.ready:
            sys.stderr.write(
                json.dumps(
                    {"event": readiness.code or "service_not_ready", "service": SERVICE_NAME},
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 3
        if namespace.check_readiness:
            sys.stdout.write('{"status":"ready","service":"autplay-worker-cpu"}\n')
            return 0

        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        vault_limits = VaultLimits(
            max_object_bytes=runtime_settings.vault_max_object_bytes,
            max_chunk_bytes=runtime_settings.vault_max_chunk_bytes,
            io_block_bytes=runtime_settings.vault_stream_block_bytes,
        )
        discovery: ManualDiscoveryService | None = None
        release_provider: JamendoProvider | None = None
        if runtime_settings.jamendo_enabled:
            client_id = runtime_settings.jamendo_client_id
            staging_root = runtime_settings.jamendo_staging_root
            if client_id is None or staging_root is None:
                raise RuntimeError("Jamendo worker configuration is unavailable")
            release_provider = JamendoProvider(
                client_id.get_secret_value(),
                timeout_seconds=runtime_settings.jamendo_timeout_seconds,
            )
            discovery = ManualDiscoveryService(
                release_provider,
                staging_root=staging_root,
                max_download_bytes=runtime_settings.jamendo_max_download_bytes,
                minimum_request_interval_seconds=(
                    runtime_settings.jamendo_minimum_request_interval_seconds
                ),
            )
        ingest_runtime = IngestWorkerRuntime(
            runtime_settings,
            sessions,
            on_drained=engine.dispose,
            stop_requested=process_stop.is_set,
            source_authorizer=(
                ManualDiscoveryBoundaryAuthorizer(
                    discovery,
                    sessions,
                    automatic_enabled=lambda: runtime_settings.discovery_automation_enabled,
                )
                if discovery is not None
                else None
            ),
        )
        handlers: dict[JobKey, JobHandler] = dict(vault_ingest_handlers(ingest_runtime.handler))
        handlers.update(import_handlers(ImportJobHandler(sessions)))
        handlers.update(standard_analysis_handlers(StandardAnalysisHandler(sessions)))
        if discovery is not None:
            client_id = runtime_settings.jamendo_client_id
            assert client_id is not None
            provider_runtime = ResourceIoRuntime(runtime_settings, maximum=1)
            provider_runtime.start()
            handlers.update(
                discovery_handlers(
                    ControlledDiscoveryAcquisitionHandler(
                        PostgresControlledDiscoveryRepository(
                            sessions,
                            limits=vault_limits,
                            automatic_enabled=lambda: runtime_settings.discovery_automation_enabled,
                        ),
                        provider_runtime.service,
                        DiscoveryIoExecutor(
                            provider_runtime.coordinator,
                            root=runtime_settings.vault_root,
                            limits=vault_limits,
                            tree_factory=ingest_runtime.tree_factory,
                            client_id=client_id.get_secret_value(),
                            max_download_bytes=runtime_settings.jamendo_max_download_bytes,
                        ),
                    )
                )
            )
        automation_repository = SqlAlchemyDiscoveryAutomationRepository(sessions)
        automation = DiscoveryAutomationService(automation_repository)
        if runtime_settings.discovery_automation_enabled:
            if release_provider is None:
                raise RuntimeError("discovery automation provider is unavailable")
            handlers.update(
                discovery_scan_handlers(
                    DiscoveryScanHandler(
                        automation_repository,
                        release_provider,
                        now=lambda: datetime.now(UTC),
                    )
                )
            )
        worker = build_cpu_worker(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            ids=Uuid7Generator(),
            handlers=handlers,
            worker_id=runtime_settings.worker_id,
            settings=JobWorkerSettings(
                lease_interval=timedelta(seconds=runtime_settings.lease_seconds),
                heartbeat_interval=timedelta(seconds=runtime_settings.heartbeat_seconds),
                idle_poll_interval=timedelta(seconds=runtime_settings.poll_interval_seconds),
                retry_policy=RetryPolicy(
                    max_attempts=runtime_settings.max_attempts,
                    base_delay=timedelta(seconds=runtime_settings.retry_base_seconds),
                    max_delay=timedelta(seconds=runtime_settings.retry_max_seconds),
                ),
            ),
        )
        try:

            def cleanup() -> int:
                return (
                    cleanup_expired_pairing_receipts(sessions, limit=10_000)
                    + cleanup_expired_recovery_operations(sessions, limit=10_000)
                    + cleanup_expired_device_admissions(sessions, limit=10_000)
                    + cleanup_expired_public_access(sessions, limit=10_000)
                )

            def web_cleanup() -> int:
                with sessions.begin() as session:
                    return SqlAlchemyWebAdminRepository(session).cleanup_expired(
                        10_000, datetime.now(UTC)
                    )

            def discovery_cleanup() -> int:
                return (
                    automation.cleanup_expired(now=datetime.now(UTC), limit=10_000)
                    + BulkDiscoveryService(sessions).cleanup_expired(
                        now=datetime.now(UTC), limit=10_000
                    )
                    + social_cleanup()
                )

            def social_cleanup() -> int:
                return SocialService(sessions, None).cleanup(datetime.now(UTC), limit=10_000)

            def discovery_dispatch() -> int:
                if not runtime_settings.discovery_automation_enabled:
                    return 0
                return automation.dispatch_due(now=datetime.now(UTC), limit=20)

            def stop_bytes() -> None:
                assert ingest_runtime is not None
                ingest_runtime.request_stop()
                if provider_runtime is not None:
                    provider_runtime.coordinator.request_stop()

            stop_scope.enter_context(
                worker_stop_scope(
                    process_stop,
                    stop_bytes,
                    join_timeout=lambda: max(0, deadline - monotonic()),
                )
            )
            if namespace.once and not process_stop.is_set():
                cleanup()
                web_cleanup()
                discovery_cleanup()
                discovery_dispatch()
                ingest_runtime.drain_cleanup()
                if not process_stop.is_set():
                    worker.run_once()
                if not process_stop.is_set():
                    ingest_runtime.drain_cleanup()
                if (
                    not process_stop.is_set()
                    and privacy_deletion is not None
                    and runtime_settings.account_purge_enabled
                ):
                    privacy_deletion.run_due()
            else:
                run_cpu_worker(
                    worker,
                    process_stop,
                    profile_receipt_cleanup=cleanup,
                    web_admin_cleanup=web_cleanup,
                    discovery_cleanup=discovery_cleanup,
                    discovery_dispatch=discovery_dispatch,
                    ingest_cleanup=ingest_runtime.drain_cleanup,
                    account_purge=privacy_deletion.run_due
                    if privacy_deletion is not None and runtime_settings.account_purge_enabled
                    else None,
                    cleanup_interval=timedelta(
                        seconds=runtime_settings.profile_receipt_cleanup_interval_seconds
                    ),
                )
        except SQLAlchemyError as error:
            _LOGGER.error(
                "worker_database_failure",
                exc_info=(type(error), error, error.__traceback__),
                extra={"error_code": "database_unavailable"},
            )
            result = 3
        except Exception as error:
            _LOGGER.error(
                "worker_runtime_failure",
                exc_info=(type(error), error, error.__traceback__),
                extra={"error_code": "worker_runtime_failure"},
            )
            result = 1
    except (DeletionEvidenceError, TrainingConsentEvidenceError) as privacy_error:
        sys.stderr.write(
            json.dumps(
                {"event": str(privacy_error), "service": SERVICE_NAME},
                separators=(",", ":"),
            )
            + "\n"
        )
        result = 3
    except ResourceAdmissionError as error:
        sys.stderr.write(
            json.dumps({"event": error.code, "service": SERVICE_NAME}, separators=(",", ":")) + "\n"
        )
        result = 3
    finally:
        deadline = monotonic() + 5
        process_stop.set()
        try:
            if provider_runtime is not None:
                pending += asyncio.run(
                    provider_runtime.shutdown(timeout=max(0, deadline - monotonic()))
                )
            if ingest_runtime is not None:
                pending += ingest_runtime.shutdown(timeout=max(0, deadline - monotonic()))
            else:
                engine.dispose()
        finally:
            stop_scope.close()
        if pending:
            _LOGGER.error("worker_processes_unconfirmed", extra={"pending_count": len(pending)})
    return 4 if pending else result


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SERVICE_NAME",
    "build_cpu_worker",
    "discovery_handlers",
    "discovery_scan_handlers",
    "import_handlers",
    "main",
    "run_cpu_worker",
    "standard_analysis_handlers",
    "vault_ingest_handlers",
)
