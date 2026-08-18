"""Actual GPU worker composition is gated before any durable claim."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from autplay.application.job_worker import JobWorker
from autplay.domain.enrichment import (
    AcceleratorDevice,
    AcceleratorSelection,
    ApprovedEmbeddingModel,
)
from autplay.runtime.settings import WorkerSettings

from autplay_gpu.settings import GpuWorkerSettings
from autplay_gpu.worker import GpuWorkerCompositionError, compose_gpu_worker


class _InferenceRuntime:
    def infer(self, pcm_batches: Sequence[bytes]) -> tuple[tuple[float, float], ...]:
        return tuple((1.0, 0.0) for _ in pcm_batches)


def _selection() -> AcceleratorSelection:
    return AcceleratorSelection(
        "auto",
        AcceleratorDevice(
            "NVIDIA",
            4,
            "GPU-future-fixture",
            "0000:04:00.0",
            "future-device",
            24_576,
            20_000,
            "9.0",
            "fixture",
        ),
        "highest-compatible-compute-vram",
    )


def _model(payload: bytes) -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        uuid4(),
        "fixture",
        "1",
        "AUDIO_EMBEDDING",
        "fixture://model",
        "1",
        "model.fixture",
        "FIXTURE",
        len(payload),
        hashlib.sha256(payload).digest(),
        b"m" * 32,
        b"p" * 32,
        "fixture",
        "FIXTURE_RUNTIME",
        "1",
        "FP32",
        16_000,
        10_000,
        "1",
        "mean",
        2,
        "BENCHMARK",
    )


def _runtime(vault_root: Path) -> WorkerSettings:
    return WorkerSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://fixture:fixture@localhost:5432/fixture",
            "vault_root": vault_root,
            "worker_id": "gpu-fixture",
        }
    )


def test_composition_wires_reviewed_model_into_single_job_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"reviewed-runtime-artifact"
    model = _model(payload)
    cache = tmp_path / "models"
    artifact = cache / "objects" / model.weights_sha256.hex()[:2] / model.weights_sha256.hex()
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(payload)

    class _Registry:
        def __init__(self, sessions: object) -> None:
            del sessions

        def get(self, model_id: object) -> ApprovedEmbeddingModel | None:
            return model if model_id == model.embedding_model_id else None

    monkeypatch.setattr("autplay_gpu.worker.SqlAlchemyEnrichmentRuntime", _Registry)
    sessions = cast(Any, lambda: None)
    worker = compose_gpu_worker(
        sessions=sessions,
        selection=_selection(),
        gpu=GpuWorkerSettings(model_id=model.embedding_model_id, model_cache_root=cache.resolve()),
        runtime=_runtime(tmp_path.resolve()),
        runtime_loaders={
            (model.runtime, model.runtime_revision): lambda path, selection, selected: (
                _InferenceRuntime()
            )
        },
    )

    assert isinstance(worker, JobWorker)


def test_composition_rejects_missing_model_before_database_claim(tmp_path: Path) -> None:
    with pytest.raises(GpuWorkerCompositionError, match="gpu_model_not_configured"):
        compose_gpu_worker(
            sessions=cast(Any, lambda: None),
            selection=_selection(),
            gpu=GpuWorkerSettings(model_cache_root=(tmp_path / "models").resolve()),
            runtime=_runtime(tmp_path.resolve()),
        )


def test_composition_rejects_unsupported_model_task_before_worker_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"reviewed-runtime-artifact"
    model = _model(payload)
    unsupported = replace(model, task="AUDIO_TAGS")

    class _Registry:
        def __init__(self, sessions: object) -> None:
            del sessions

        def get(self, model_id: object) -> ApprovedEmbeddingModel | None:
            return unsupported if model_id == unsupported.embedding_model_id else None

    monkeypatch.setattr("autplay_gpu.worker.SqlAlchemyEnrichmentRuntime", _Registry)
    with pytest.raises(GpuWorkerCompositionError, match="gpu_model_task_unsupported"):
        compose_gpu_worker(
            sessions=cast(Any, lambda: None),
            selection=_selection(),
            gpu=GpuWorkerSettings(
                model_id=unsupported.embedding_model_id,
                model_cache_root=(tmp_path / "models").resolve(),
            ),
            runtime=_runtime(tmp_path.resolve()),
        )
