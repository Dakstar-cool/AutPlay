"""Verified artifact loading and deterministic segment pooling for GPU backends."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from autplay.domain.enrichment import (
    AcceleratorSelection,
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
)


class ModelArtifactError(RuntimeError):
    """A private model artifact does not match its approved registry identity."""


class GpuInferenceRuntime(Protocol):
    """Small backend seam implemented by a separately reviewed CUDA runtime adapter."""

    def infer(self, pcm_batches: Sequence[bytes]) -> Sequence[Sequence[float]]: ...


type RuntimeLoader = Callable[
    [Path, AcceleratorSelection, ApprovedEmbeddingModel], GpuInferenceRuntime
]


class VerifiedArtifactTrackEmbedder:
    """Load only hash-approved weights and produce deterministic normalized embeddings."""

    def __init__(
        self,
        model: ApprovedEmbeddingModel,
        artifact_path: Path,
        selection: AcceleratorSelection,
        loader: RuntimeLoader,
    ) -> None:
        if not artifact_path.is_absolute() or not artifact_path.is_file():
            raise ModelArtifactError("approved model artifact is unavailable")
        if artifact_path.stat().st_size != model.artifact_byte_size:
            raise ModelArtifactError("approved model artifact size does not match registry")
        if _sha256(artifact_path) != model.weights_sha256:
            raise ModelArtifactError("approved model artifact hash does not match registry")
        self._model = model
        self._runtime = loader(artifact_path, selection, model)

    @property
    def model_id(self) -> UUID:
        """Return the immutable registry identity of the loaded weights."""

        return self._model.embedding_model_id

    @property
    def weights_sha256(self) -> bytes:
        """Return the hash verified before the runtime saw the artifact."""

        return self._model.weights_sha256

    def embed(
        self, segments: Sequence[DecodedAudioSegment], *, batch_size: int
    ) -> tuple[tuple[float, ...], tuple[tuple[str, float], ...]]:
        """Infer in bounded batches, mean-pool segments and L2-normalize once."""

        if not segments or len(segments) > 256 or not 1 <= batch_size <= 256:
            raise ValueError("embedding request is not bounded")
        pooled = [0.0] * self._model.dimension
        observed = 0
        for offset in range(0, len(segments), batch_size):
            batch = segments[offset : offset + batch_size]
            outputs = self._runtime.infer(tuple(item.pcm_f32le for item in batch))
            if len(outputs) != len(batch):
                raise ModelArtifactError("GPU runtime returned the wrong batch size")
            for vector in outputs:
                if len(vector) != self._model.dimension or any(
                    not math.isfinite(value) for value in vector
                ):
                    raise ModelArtifactError("GPU runtime returned an invalid embedding")
                for index, value in enumerate(vector):
                    pooled[index] += value
                observed += 1
        norm = math.sqrt(sum((value / observed) ** 2 for value in pooled))
        if not math.isfinite(norm) or norm <= 0:
            raise ModelArtifactError("GPU runtime returned a zero or invalid pooled embedding")
        vector = tuple((value / observed) / norm for value in pooled)
        return vector, ()


def _sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.digest()


__all__ = (
    "GpuInferenceRuntime",
    "ModelArtifactError",
    "VerifiedArtifactTrackEmbedder",
)
