"""Verified private artifact and deterministic pooling evidence."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.domain.enrichment import (
    AcceleratorDevice,
    AcceleratorSelection,
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
)

from autplay_gpu.embedding import ModelArtifactError, VerifiedArtifactTrackEmbedder


def _selection() -> AcceleratorSelection:
    return AcceleratorSelection(
        "auto",
        AcceleratorDevice(
            "NVIDIA", 0, "GPU-fixture", "0000:01:00.0", "RTX", 12_288, 10_000, "8.6", "1"
        ),
        "test",
    )


def _model(payload: bytes) -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        uuid4(),
        "fixture",
        "1",
        "AUDIO_EMBEDDING",
        "fixture://model",
        "revision",
        "model.bin",
        "fixture",
        len(payload),
        hashlib.sha256(payload).digest(),
        b"m" * 32,
        b"p" * 32,
        "fixture-license",
        "fixture-runtime",
        "1",
        "fp32",
        16_000,
        10_000,
        "1",
        "mean-normalized",
        2,
        "BENCHMARK",
    )


class _Runtime:
    def infer(self, pcm_batches: Sequence[bytes]) -> tuple[tuple[float, float], ...]:
        return tuple((3.0, 4.0) for _ in pcm_batches)


def test_artifact_hash_and_pooling_are_enforced(tmp_path: Path) -> None:
    payload = b"reviewed-weights"
    artifact = (tmp_path / "model.bin").resolve()
    artifact.write_bytes(payload)
    selected: list[str] = []

    def loader(
        path: Path, selection: AcceleratorSelection, model: ApprovedEmbeddingModel
    ) -> _Runtime:
        del path, model
        selected.append(selection.device.device_uuid)
        return _Runtime()

    embedder = VerifiedArtifactTrackEmbedder(_model(payload), artifact, _selection(), loader)
    vector, tags = embedder.embed(
        (
            DecodedAudioSegment(0, 0, struct.pack("<f", 0.0)),
            DecodedAudioSegment(1, 10_000, struct.pack("<f", 1.0)),
        ),
        batch_size=1,
    )

    assert vector == pytest.approx((0.6, 0.8))
    assert tags == ()
    assert selected == ["GPU-fixture"]


def test_artifact_tamper_fails_before_runtime_load(tmp_path: Path) -> None:
    payload = b"reviewed-weights"
    artifact = (tmp_path / "model.bin").resolve()
    artifact.write_bytes(b"tampered-weights")
    with pytest.raises(ModelArtifactError, match="hash"):
        VerifiedArtifactTrackEmbedder(
            _model(payload), artifact, _selection(), lambda *args: _Runtime()
        )
