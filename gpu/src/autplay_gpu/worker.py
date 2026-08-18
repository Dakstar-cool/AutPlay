"""Production composition for the isolated single-concurrency enrichment worker."""

from __future__ import annotations

import signal
import threading
from datetime import timedelta
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.enrichment import SqlAlchemyEnrichmentRuntime
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.system import Uuid7Generator
from autplay.application.enrichment import EMBEDDING_JOB_KEY, EmbeddingJobHandler
from autplay.application.job_worker import JobHandlerRegistry, JobWorker, JobWorkerSettings
from autplay.domain.enrichment import AcceleratorSelection
from autplay.domain.jobs import RetryPolicy
from autplay.runtime.settings import WorkerSettings
from sqlalchemy.orm import Session, sessionmaker

from .artifacts import ModelArtifactStore
from .embedding import RuntimeLoader, VerifiedArtifactTrackEmbedder
from .onnx_runtime import RUNTIME_NAME, RUNTIME_REVISION, onnx_cuda_loader
from .preprocessing import FfmpegSegmentPreprocessor
from .settings import GpuWorkerSettings
from .sources import PostgresFilesystemAudioSourceResolver

_RUNTIME_LOADERS: Final = MappingProxyType({(RUNTIME_NAME, RUNTIME_REVISION): onnx_cuda_loader})


class GpuWorkerCompositionError(RuntimeError):
    """Stable startup failure before any durable job is claimed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def compose_gpu_worker(
    *,
    sessions: sessionmaker[Session],
    selection: AcceleratorSelection,
    gpu: GpuWorkerSettings,
    runtime: WorkerSettings,
    runtime_loaders: dict[tuple[str, str], RuntimeLoader] | None = None,
) -> JobWorker:
    """Verify one configured model and compose the actual durable GPU worker."""

    if gpu.model_id is None:
        raise GpuWorkerCompositionError("gpu_model_not_configured")
    enrichment = SqlAlchemyEnrichmentRuntime(sessions)
    model = enrichment.get(gpu.model_id)
    if model is None or model.status in {"BLOCKED", "RETIRED"}:
        raise GpuWorkerCompositionError("gpu_model_not_eligible")
    if model.task != "AUDIO_EMBEDDING":
        raise GpuWorkerCompositionError("gpu_model_task_unsupported")
    loaders = _RUNTIME_LOADERS if runtime_loaders is None else runtime_loaders
    loader = loaders.get((model.runtime, model.runtime_revision))
    if loader is None:
        raise GpuWorkerCompositionError("gpu_runtime_adapter_unavailable")
    artifact = ModelArtifactStore(gpu.model_cache_root).resolve(model)
    embedder = VerifiedArtifactTrackEmbedder(model, artifact, selection, loader)
    preprocessor = FfmpegSegmentPreprocessor(
        PostgresFilesystemAudioSourceResolver(sessions, runtime.vault_root),
        timeout_seconds=runtime.vault_tool_timeout_seconds,
    )
    handler = EmbeddingJobHandler(
        jobs=enrichment,
        models=enrichment,
        preprocessor=preprocessor,
        embedder=embedder,
        writer=enrichment,
        initial_batch_size=gpu.initial_batch_size,
        maximum_oom_reductions=gpu.maximum_oom_reductions,
    )
    worker_id = runtime.worker_id or f"gpu-{Uuid7Generator().new()}"
    return JobWorker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        worker_id=worker_id,
        registry=JobHandlerRegistry({EMBEDDING_JOB_KEY: handler}),
        settings=JobWorkerSettings(
            lease_interval=timedelta(seconds=runtime.lease_seconds),
            heartbeat_interval=timedelta(seconds=runtime.heartbeat_seconds),
            idle_poll_interval=timedelta(seconds=runtime.poll_interval_seconds),
            retry_policy=RetryPolicy(
                max_attempts=runtime.max_attempts,
                base_delay=timedelta(seconds=runtime.retry_base_seconds),
                max_delay=timedelta(seconds=runtime.retry_max_seconds),
            ),
        ),
    )


def run_gpu_worker(worker: JobWorker, stop_event: threading.Event | None = None) -> None:
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


__all__ = (
    "EMBEDDING_JOB_KEY",
    "GpuWorkerCompositionError",
    "compose_gpu_worker",
    "run_gpu_worker",
)
