"""Single-concurrency metadata worker; global provider pacing also survives replicas."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.media.tools import ChromaprintTool, SubprocessExecutableRunner
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider, PublicMetadataHttp
from autplay.adapters.system import Uuid7Generator
from autplay.application.job_worker import JobWorkerSettings
from autplay.application.track_metadata import METADATA_JOB, TrackMetadataService
from autplay.application.track_metadata_worker import TrackMetadataHandler
from autplay.domain.jobs import RetryPolicy
from autplay.entrypoints.worker_cpu import build_cpu_worker, run_cpu_worker
from autplay.runtime.settings import load_api_settings


def main() -> None:
    settings = load_api_settings()
    engine = create_runtime_engine(settings)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    service = TrackMetadataService(sessions)

    @contextmanager
    def provider_gate() -> Iterator[None]:
        # A transaction lock releases on disconnect/crash. Holding it through the call
        # and delay ensures at most one request a second across worker processes.
        with sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(1835102836, 1)"))
            try:
                yield
            finally:
                time.sleep(1.1)

    handler = TrackMetadataHandler(
        service,
        FilesystemVaultStorage(settings.vault_root),
        FfmpegMetadataReader(SubprocessExecutableRunner()),
        MusicBrainzMetadataProvider(
            PublicMetadataHttp(
                provider_gate,
                proxy=settings.metadata_proxy.get_secret_value()
                if settings.metadata_proxy
                else None,
            )
        ),
        acoustid_key=settings.acoustid_client_key.get_secret_value(),
        fingerprint=ChromaprintTool(
            "fpcalc", algorithm_version="1.6.1", timeout_seconds=60, max_duration_seconds=3600
        ),
    )
    worker = build_cpu_worker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        ids=Uuid7Generator(),
        handlers={METADATA_JOB: handler},
        settings=JobWorkerSettings(retry_policy=RetryPolicy(max_attempts=6)),
    )
    try:
        run_cpu_worker(
            worker,
            discovery_dispatch=lambda: service.enqueue_missing(limit=100),
            cleanup_interval=timedelta(minutes=5),
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
