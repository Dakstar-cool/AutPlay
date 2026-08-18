"""ONNX CUDA adapter shape, device and OOM classification evidence."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import numpy.typing as npt
import pytest
from autplay.application.enrichment import AcceleratorOutOfMemory
from autplay.domain.enrichment import ApprovedEmbeddingModel

from autplay_gpu.embedding import ModelArtifactError
from autplay_gpu.onnx_runtime import OnnxCudaRuntime


class _Node:
    def __init__(self, name: str, shape: list[int | str | None]) -> None:
        self.name = name
        self.shape = shape
        self.type = "tensor(float)"


class _Session:
    def __init__(self, *, dimension: int = 2, failure: RuntimeError | None = None) -> None:
        self.inputs: list[np.ndarray[Any, np.dtype[np.float32]]] = []
        self._dimension = dimension
        self._failure = failure

    def get_inputs(self) -> Sequence[Any]:
        return (_Node("waveform", [None, 4]),)

    def get_outputs(self) -> Sequence[Any]:
        return (_Node("embedding", [None, self._dimension]),)

    def get_providers(self) -> Sequence[str]:
        return ("CUDAExecutionProvider",)

    def run(
        self,
        output_names: Sequence[str],
        input_feed: dict[str, npt.NDArray[np.float32]],
    ) -> Sequence[object]:
        del output_names
        if self._failure is not None:
            raise self._failure
        batch = input_feed["waveform"]
        assert isinstance(batch, np.ndarray)
        self.inputs.append(batch)
        return (np.ones((batch.shape[0], self._dimension), dtype=np.float32),)


def _model(payload: bytes = b"onnx") -> ApprovedEmbeddingModel:
    return ApprovedEmbeddingModel(
        uuid4(),
        "fixture",
        "1",
        "AUDIO_EMBEDDING",
        "fixture://onnx",
        "1",
        "model.onnx",
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


def test_onnx_runtime_uses_selected_device_and_zero_pads_waveforms(tmp_path: Path) -> None:
    session = _Session()
    selected: list[int] = []

    def create_session(path: Path, index: int) -> _Session:
        del path
        selected.append(index)
        return session

    runtime = OnnxCudaRuntime(
        tmp_path / "model.onnx",
        device_index=7,
        model=_model(),
        session_factory=create_session,
    )

    result = runtime.infer(
        (
            struct.pack("<ff", 1.0, 2.0),
            struct.pack("<ffff", 3.0, 4.0, 5.0, 6.0),
        )
    )

    assert selected == [7]
    assert result == ((1.0, 1.0), (1.0, 1.0))
    assert session.inputs[0].tolist() == [[1.0, 2.0, 0.0, 0.0], [3.0, 4.0, 5.0, 6.0]]


def test_onnx_runtime_rejects_dimension_and_classifies_oom(tmp_path: Path) -> None:
    with pytest.raises(ModelArtifactError, match="dimension"):
        OnnxCudaRuntime(
            tmp_path / "model.onnx",
            device_index=0,
            model=_model(),
            session_factory=lambda path, index: _Session(dimension=3),
        )

    runtime = OnnxCudaRuntime(
        tmp_path / "model.onnx",
        device_index=0,
        model=_model(),
        session_factory=lambda path, index: _Session(
            failure=RuntimeError("CUDA error: out of memory")
        ),
    )
    with pytest.raises(AcceleratorOutOfMemory):
        runtime.infer((struct.pack("<f", 1.0),))
