"""Dependency-injected CPU worker process entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Mapping, Sequence
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.system import Uuid7Generator
from autplay.application.job_worker import (
    JobHandler,
    JobHandlerRegistry,
    JobWorker,
    JobWorkerSettings,
)
from autplay.domain.jobs import JobKey, RetryPolicy
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


def run_cpu_worker(worker: JobWorker, stop_event: threading.Event | None = None) -> None:
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
    try:
        worker.run_forever(process_stop)
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
        worker = build_cpu_worker(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            ids=Uuid7Generator(),
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
            if namespace.once:
                worker.run_once()
            else:
                run_cpu_worker(worker)
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


__all__ = ("SERVICE_NAME", "build_cpu_worker", "main", "run_cpu_worker")
