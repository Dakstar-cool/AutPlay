"""Composition boundary for the isolated loopback Sona shadow worker."""

from __future__ import annotations

from dataclasses import dataclass

from autplay.domain.enrichment import AcceleratorSelection

from .settings import GpuWorkerSettings
from .sona_artifacts import SonaArtifactStore, VerifiedSonaArtifact
from .sona_runtime import SonaOnnxCudaRuntime


class SonaWorkerCompositionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ComposedSonaWorker:
    runtime: SonaOnnxCudaRuntime
    artifact: VerifiedSonaArtifact


def compose_sona_shadow_worker(
    gpu: GpuWorkerSettings, selection: AcceleratorSelection
) -> ComposedSonaWorker:
    """Verify exact graph provenance before CUDA session construction."""

    if not gpu.sona_configured:
        raise SonaWorkerCompositionError("gpu_sona_model_not_configured")
    artifact_sha256 = gpu.sona_artifact_sha256
    model_manifest_sha256 = gpu.sona_model_manifest_sha256
    tokenizer_sha256 = gpu.sona_tokenizer_sha256
    if artifact_sha256 is None or model_manifest_sha256 is None or tokenizer_sha256 is None:
        raise SonaWorkerCompositionError("gpu_sona_identity_incomplete")
    artifact = SonaArtifactStore(gpu.model_cache_root).resolve(
        artifact_sha256=artifact_sha256,
        model_manifest_sha256=model_manifest_sha256,
        tokenizer_sha256=tokenizer_sha256,
    )
    runtime = SonaOnnxCudaRuntime(artifact.payload, device_index=selection.device.index)
    return ComposedSonaWorker(runtime, artifact)


__all__ = (
    "ComposedSonaWorker",
    "SonaWorkerCompositionError",
    "compose_sona_shadow_worker",
)
