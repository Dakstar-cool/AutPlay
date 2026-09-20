"""Isolated CPU acquisition worker sharing the normal PostgreSQL/Vault boundaries."""

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.system import Uuid7Generator
from autplay.application.internet_music import (
    INTERNET_ACQUIRE_JOB,
    InternetMusicHandler,
    InternetMusicService,
)
from autplay.application.job_worker import JobWorkerSettings
from autplay.domain.jobs import RetryPolicy
from autplay.entrypoints.composition import build_vault_http_service
from autplay.entrypoints.privacy_deletion import enforce_privacy_restore_guard
from autplay.entrypoints.worker_cpu import build_cpu_worker, run_cpu_worker
from autplay.runtime.settings import load_api_settings


def main() -> None:
    settings = load_api_settings()
    if not settings.internet_music_enabled:
        raise RuntimeError("music_search_disabled")
    engine = create_runtime_engine(settings)
    try:
        enforce_privacy_restore_guard(settings, engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        service = InternetMusicService(sessions, build_vault_http_service(settings, engine))
        worker = build_cpu_worker(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            ids=Uuid7Generator(),
            handlers={INTERNET_ACQUIRE_JOB: InternetMusicHandler(service)},
            settings=JobWorkerSettings(retry_policy=RetryPolicy(max_attempts=12)),
        )
        run_cpu_worker(worker)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
