"""Single-concurrency contained metadata worker with durable global provider pacing."""

import argparse
import json
import sys
import threading
from collections.abc import Sequence
from contextlib import ExitStack
from datetime import timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.internal_io import internal_io_policy
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.system import Uuid7Generator
from autplay.application.job_worker import JobWorkerSettings
from autplay.application.track_metadata import METADATA_JOB, TrackMetadataService
from autplay.domain.jobs import RetryPolicy
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.training_consent import TrainingConsentEvidenceError
from autplay.entrypoints.metadata_composition import MetadataWorkerRuntime
from autplay.entrypoints.privacy_deletion import enforce_privacy_restore_guard
from autplay.entrypoints.worker_cpu import build_cpu_worker, run_cpu_worker, worker_stop_scope
from autplay.runtime.settings import SettingsLoadError, load_worker_settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--check-readiness", action="store_true")
    namespace = parser.parse_args(argv)
    try:
        settings = load_worker_settings()
    except SettingsLoadError:
        sys.stderr.write('{"event":"metadata_settings_invalid"}\n')
        return 2
    engine = create_runtime_engine(settings)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    process_stop, scope = threading.Event(), ExitStack()
    runtime: MetadataWorkerRuntime | None = None
    pending: tuple[UUID, ...] = ()
    result, deadline = 0, float("inf")
    try:
        enforce_privacy_restore_guard(settings, engine)
        if namespace.check_readiness:
            readiness = PostgreSQLReadinessProbe(engine).check()
            if not readiness.ready:
                sys.stderr.write(
                    json.dumps({"event": readiness.code or "service_not_ready"}) + "\n"
                )
                result = 3
            else:
                with sessions.begin() as session:
                    if internal_io_policy(session).workload_version < 2:
                        raise ResourceAdmissionError("metadata_budget_unconfigured")
                sys.stdout.write('{"status":"ready","service":"autplay-worker-metadata"}\n')
            return result
        runtime = MetadataWorkerRuntime(
            settings,
            sessions,
            on_drained=engine.dispose,
            stop_requested=process_stop.is_set,
        )
        service = TrackMetadataService(sessions)
        worker = build_cpu_worker(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            ids=Uuid7Generator(),
            handlers={METADATA_JOB: runtime.handler},
            worker_id=settings.worker_id,
            settings=JobWorkerSettings(
                lease_interval=timedelta(seconds=settings.lease_seconds),
                heartbeat_interval=timedelta(seconds=settings.heartbeat_seconds),
                idle_poll_interval=timedelta(seconds=settings.poll_interval_seconds),
                retry_policy=RetryPolicy(max_attempts=6),
            ),
        )
        scope.enter_context(
            worker_stop_scope(
                process_stop,
                runtime.request_stop,
                join_timeout=lambda: max(0, deadline - monotonic()),
            )
        )
        if namespace.once:
            if not process_stop.is_set():
                service.enqueue_missing(limit=100)
            if not process_stop.is_set():
                worker.run_once()
        else:
            run_cpu_worker(
                worker,
                process_stop,
                discovery_dispatch=lambda: service.enqueue_missing(limit=100),
                cleanup_interval=timedelta(minutes=5),
            )
    except (DeletionEvidenceError, TrainingConsentEvidenceError) as privacy_error:
        sys.stderr.write(json.dumps({"event": str(privacy_error)}) + "\n")
        result = 3
    except ResourceAdmissionError as error:
        sys.stderr.write(json.dumps({"event": error.code}) + "\n")
        result = 3
    except SQLAlchemyError:
        sys.stderr.write('{"event":"metadata_database_unavailable"}\n')
        result = 3
    finally:
        deadline = monotonic() + 5
        process_stop.set()
        try:
            if runtime is not None:
                pending = runtime.shutdown(timeout=max(0, deadline - monotonic()))
            else:
                engine.dispose()
        finally:
            scope.close()
        if pending:
            sys.stderr.write(
                json.dumps({"event": "metadata_processes_unconfirmed", "count": len(pending)})
                + "\n"
            )
    return 4 if pending else result


if __name__ == "__main__":
    raise SystemExit(main())
