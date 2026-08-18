"""Deterministic and bounded FFmpeg segment adapter evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.domain.enrichment import ApprovedEmbeddingModel, EmbeddingJobTarget

from autplay_gpu.preprocessing import (
    AudioPreprocessingError,
    FfmpegSegmentPreprocessor,
    VerifiedAudioSource,
)


class _Resolver:
    def __init__(self, path: Path, duration_ms: int = 25_000) -> None:
        self.source = VerifiedAudioSource(path, duration_ms, 4, b"v" * 32)

    def resolve(self, target: EmbeddingJobTarget) -> VerifiedAudioSource:
        del target
        return self.source


def _model(*, segment_duration_ms: int = 10_000, sample_rate: int = 10) -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        embedding_model_id=uuid4(),
        model_key="fixture",
        version="1",
        task="audio-embedding",
        source="fixture://model",
        source_revision="revision",
        artifact_filename="model.bin",
        artifact_format="fixture",
        artifact_byte_size=1,
        weights_sha256=b"w" * 32,
        manifest_sha256=b"m" * 32,
        preprocessing_sha256=b"p" * 32,
        license_id="fixture",
        runtime="fixture",
        runtime_revision="1",
        inference_precision="fp32",
        input_sample_rate_hz=sample_rate,
        segment_duration_ms=segment_duration_ms,
        preprocessing_version="1",
        pooling_strategy="mean",
        dimension=2,
        status="BENCHMARK",
    )


def _target(model: ApprovedEmbeddingModel) -> EmbeddingJobTarget:
    return EmbeddingJobTarget(
        uuid4(), "AUDIO_EMBEDDING", uuid4(), uuid4(), model.embedding_model_id, b"w" * 32, b"p" * 32
    )


def test_even_segment_plan_and_commands_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source.flac"
    source.write_bytes(b"test")
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], timeout: float, maximum: int) -> bytes:
        assert timeout == 5
        commands.append(tuple(command))
        return bytes(maximum)

    model = _model()
    preprocessor = FfmpegSegmentPreprocessor(
        _Resolver(source),
        executable="ffmpeg-fixture",
        maximum_segments=3,
        timeout_seconds=5,
        runner=runner,
    )
    first = preprocessor.decode(_target(model), model)
    second = preprocessor.decode(_target(model), model)

    assert [item.start_ms for item in first] == [0, 7_500, 15_000]
    assert first == second
    assert len(commands) == 6
    assert all("-nostdin" in command and "pipe:1" in command for command in commands)


def test_missing_source_and_oversized_model_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.flac"
    model = _model()
    with pytest.raises(AudioPreprocessingError, match="unavailable"):
        FfmpegSegmentPreprocessor(_Resolver(missing)).decode(_target(model), model)

    source = tmp_path / "source.flac"
    source.write_bytes(hashlib.sha256(b"source").digest())
    oversized = _model(segment_duration_ms=300_000, sample_rate=48_000)
    with pytest.raises(AudioPreprocessingError, match="resource bound"):
        FfmpegSegmentPreprocessor(_Resolver(source, 300_000)).decode(_target(oversized), oversized)
