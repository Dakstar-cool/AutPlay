"""Hash-addressed private model cache resolution evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.domain.enrichment import ApprovedEmbeddingModel

from autplay_gpu.artifacts import ModelArtifactStore
from autplay_gpu.embedding import ModelArtifactError


def _model(payload: bytes) -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        uuid4(),
        "fixture",
        "1",
        "AUDIO_EMBEDDING",
        "fixture://model",
        "1",
        "ignored.onnx",
        "ONNX",
        len(payload),
        hashlib.sha256(payload).digest(),
        b"m" * 32,
        b"p" * 32,
        "fixture",
        "ONNX_RUNTIME_CUDA",
        "1.26.0",
        "FP32",
        16_000,
        10_000,
        "1",
        "mean",
        2,
        "BENCHMARK",
    )


def test_artifact_store_uses_only_registry_hash(tmp_path: Path) -> None:
    payload = b"reviewed-model"
    model = _model(payload)
    root = tmp_path.resolve()
    path = root / "objects" / model.weights_sha256.hex()[:2] / model.weights_sha256.hex()
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)

    assert ModelArtifactStore(root).resolve(model) == path


def test_artifact_store_rejects_missing_hash_object(tmp_path: Path) -> None:
    with pytest.raises(ModelArtifactError, match="unavailable"):
        ModelArtifactStore(tmp_path.resolve()).resolve(_model(b"missing"))


def test_artifact_store_rejects_symlinked_hash_path(tmp_path: Path) -> None:
    payload = b"reviewed-model"
    model = _model(payload)
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    digest = model.weights_sha256.hex()
    external_artifact = outside / digest
    external_artifact.write_bytes(payload)
    link = root / "objects" / digest[:2]
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(ModelArtifactError, match="unavailable"):
        ModelArtifactStore(root.resolve()).resolve(model)
