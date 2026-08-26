"""Dependency-injected CPU worker process entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.jamendo import JamendoProvider
from autplay.adapters.media.tools import (
    ChromaprintTool,
    FfmpegDecodeValidator,
    FfprobeInspector,
    ValidatedMediaInspector,
)
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.adapters.postgresql.web_admin import SqlAlchemyWebAdminRepository
from autplay.adapters.system import Uuid7Generator
from autplay.application.bulk_discovery import BulkDiscoveryService
from autplay.application.discovery_acquisition import (
    DiscoveryAcquisitionHandler,
    StandardAnalysisHandler,
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
from autplay.application.social import SocialService
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.jobs import JobKey, RetryPolicy
from autplay.domain.vault import VaultLimits
from autplay.ports.ids import IdGenerator
from autplay.ports.transactions import JobUnitOfWorkFactory
from autplay.runtime.logging import configure_json_logging
from autplay.runtime.settings import SettingsLoadError, load_worker_settings

SERVICE_NAME = "autplay-worker-cpu"
_LOGGER = logging.getLogger("autplay.worker_cpu")


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


def vault_ingest_handlers(handler: VaultIngestHandler) -> Mapping[JobKey, JobHandler]:
    """Return the single P06 CPU-only worker registration at priority three.

    Priority is persisted when the upload completion enqueues ``vault.ingest``;
    this registry deliberately contains no GPU or external-acquisition handler.
    """

    return {JobKey("vault.ingest", 1): handler}


def import_handlers(handler: ImportJobHandler) -> Mapping[JobKey, JobHandler]:
    """Return the P10 CPU-only resumable import registration."""

    return {JobKey("library.import", 1): handler}


def discovery_handlers(handler: DiscoveryAcquisitionHandler) -> Mapping[JobKey, JobHandler]:
    """Return the disabled-by-default manual A1B acquisition registration."""

    return {JobKey("discovery.acquire", 1): handler}


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
    cleanup_interval: timedelta = timedelta(minutes=5),
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
    next_cleanup_at = 0.0
    try:
        while not process_stop.is_set():
            current = monotonic()
            if current >= next_cleanup_at:
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
    try:
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
        vault_storage = FilesystemVaultStorage(runtime_settings.vault_root, limits=vault_limits)
        inspector = ValidatedMediaInspector(
            FfmpegDecodeValidator(
                "ffmpeg",
                timeout_seconds=runtime_settings.vault_tool_timeout_seconds,
                max_output_bytes=runtime_settings.vault_tool_max_output_bytes,
            ),
            FfprobeInspector(
                "ffprobe",
                timeout_seconds=runtime_settings.vault_tool_timeout_seconds,
                max_output_bytes=runtime_settings.vault_tool_max_output_bytes,
            ),
        )
        ingest = VaultIngestHandler(
            repository=TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions)),
            storage=vault_storage,
            media=inspector,
            fingerprints=ChromaprintTool(
                "fpcalc",
                algorithm_version="1.6.1",
                timeout_seconds=runtime_settings.vault_tool_timeout_seconds,
                max_output_bytes=runtime_settings.vault_tool_max_output_bytes,
            ),
            minimum_free_bytes=runtime_settings.vault_low_disk_bytes,
        )
        handlers: dict[JobKey, JobHandler] = dict(vault_ingest_handlers(ingest))
        handlers.update(import_handlers(ImportJobHandler(sessions)))
        handlers.update(standard_analysis_handlers(StandardAnalysisHandler(sessions)))
        if runtime_settings.jamendo_enabled:
            client_id = runtime_settings.jamendo_client_id
            staging_root = runtime_settings.jamendo_staging_root
            if client_id is None or staging_root is None:
                raise RuntimeError("Jamendo worker configuration is unavailable")
            discovery = ManualDiscoveryService(
                JamendoProvider(
                    client_id.get_secret_value(),
                    timeout_seconds=runtime_settings.jamendo_timeout_seconds,
                ),
                staging_root=staging_root,
                max_download_bytes=runtime_settings.jamendo_max_download_bytes,
                minimum_request_interval_seconds=(
                    runtime_settings.jamendo_minimum_request_interval_seconds
                ),
            )
            handlers.update(
                discovery_handlers(
                    DiscoveryAcquisitionHandler(
                        sessions,
                        discovery=discovery,
                        storage=vault_storage,
                        limits=vault_limits,
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
                return cleanup_expired_pairing_receipts(
                    sessions, limit=10_000
                ) + cleanup_expired_device_admissions(sessions, limit=10_000)

            def web_cleanup() -> int:
                with sessions.begin() as session:
                    return SqlAlchemyWebAdminRepository(session).cleanup_expired(
                        10_000, datetime.now(UTC)
                    )

            def discovery_cleanup() -> int:
                return (
                    BulkDiscoveryService(sessions).cleanup_expired(
                        now=datetime.now(UTC),
                        limit=10_000,
                    )
                    + social_cleanup()
                )

            def social_cleanup() -> int:
                return SocialService(sessions, None).cleanup(datetime.now(UTC), limit=10_000)

            if namespace.once:
                cleanup()
                web_cleanup()
                discovery_cleanup()
                worker.run_once()
            else:
                run_cpu_worker(
                    worker,
                    profile_receipt_cleanup=cleanup,
                    web_admin_cleanup=web_cleanup,
                    discovery_cleanup=discovery_cleanup,
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
            return 3
        except Exception as error:
            _LOGGER.error(
                "worker_runtime_failure",
                exc_info=(type(error), error, error.__traceback__),
                extra={"error_code": "worker_runtime_failure"},
            )
            return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SERVICE_NAME",
    "build_cpu_worker",
    "discovery_handlers",
    "import_handlers",
    "main",
    "run_cpu_worker",
    "standard_analysis_handlers",
    "vault_ingest_handlers",
)
