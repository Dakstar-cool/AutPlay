"""Pinned ONNX Runtime CUDA adapter for approved single-input audio encoders."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
import onnxruntime as ort  # type: ignore[import-untyped]
from autplay.application.enrichment import AcceleratorOutOfMemory
from autplay.domain.enrichment import AcceleratorSelection, ApprovedEmbeddingModel

from .embedding import GpuInferenceRuntime, ModelArtifactError

RUNTIME_NAME = "ONNX_RUNTIME_CUDA"
RUNTIME_REVISION = "1.26.0"


class _NodeArgument(Protocol):
    name: str
    shape: Sequence[int | str | None]
    type: str


class _InferenceSession(Protocol):
    def get_inputs(self) -> Sequence[_NodeArgument]: ...

    def get_outputs(self) -> Sequence[_NodeArgument]: ...

    def get_providers(self) -> Sequence[str]: ...

    def run(
        self,
        output_names: Sequence[str],
        input_feed: dict[str, npt.NDArray[np.float32]],
    ) -> Sequence[object]: ...


type SessionFactory = Callable[[Path, int], _InferenceSession]


class OnnxCudaRuntime(GpuInferenceRuntime):
    """Execute one reviewed waveform-to-embedding ONNX graph on the selected CUDA device."""

    def __init__(
        self,
        artifact_path: Path,
        *,
        device_index: int,
        model: ApprovedEmbeddingModel,
        session_factory: SessionFactory | None = None,
    ) -> None:
        try:
            self._session = (session_factory or _create_session)(artifact_path, device_index)
        except AcceleratorOutOfMemory:
            raise
        except RuntimeError as error:
            if _is_oom(error):
                raise AcceleratorOutOfMemory from error
            raise ModelArtifactError("ONNX CUDA session initialization failed") from error
        if "CUDAExecutionProvider" not in self._session.get_providers():
            raise ModelArtifactError("ONNX CUDA execution provider is unavailable")
        inputs = tuple(self._session.get_inputs())
        outputs = tuple(self._session.get_outputs())
        if len(inputs) != 1 or inputs[0].type != "tensor(float)" or len(inputs[0].shape) != 2:
            raise ModelArtifactError("ONNX audio encoder must have one rank-two float input")
        if len(outputs) != 1 or outputs[0].type != "tensor(float)" or len(outputs[0].shape) != 2:
            raise ModelArtifactError("ONNX audio encoder must have one rank-two float output")
        output_dimension = outputs[0].shape[1]
        if isinstance(output_dimension, int) and output_dimension != model.dimension:
            raise ModelArtifactError("ONNX output dimension does not match registry")
        input_samples = inputs[0].shape[1]
        self._fixed_input_samples = input_samples if isinstance(input_samples, int) else None
        self._input_name = inputs[0].name
        self._output_name = outputs[0].name
        self._dimension = model.dimension

    def infer(self, pcm_batches: Sequence[bytes]) -> tuple[tuple[float, ...], ...]:
        """Pad bounded float32 waveforms, execute CUDA and return finite vectors."""

        if not pcm_batches or len(pcm_batches) > 256:
            raise ValueError("ONNX inference batch is not bounded")
        waveforms = tuple(np.frombuffer(pcm, dtype="<f4") for pcm in pcm_batches)
        if any(samples.size < 1 for samples in waveforms):
            raise ValueError("ONNX inference waveform is empty")
        sample_count = self._fixed_input_samples or max(samples.size for samples in waveforms)
        if sample_count < 1 or any(samples.size > sample_count for samples in waveforms):
            raise ModelArtifactError("PCM segment exceeds the ONNX input shape")
        batch = np.zeros((len(waveforms), sample_count), dtype=np.float32)
        for index, samples in enumerate(waveforms):
            batch[index, : samples.size] = samples
        try:
            raw_outputs = self._session.run([self._output_name], {self._input_name: batch})
        except RuntimeError as error:
            if _is_oom(error):
                raise AcceleratorOutOfMemory from error
            raise ModelArtifactError("ONNX CUDA inference failed") from error
        if len(raw_outputs) != 1:
            raise ModelArtifactError("ONNX runtime returned an invalid output count")
        output = np.asarray(raw_outputs[0])
        if output.shape != (len(waveforms), self._dimension) or output.dtype != np.float32:
            raise ModelArtifactError("ONNX runtime returned an invalid embedding shape")
        if not np.isfinite(output).all():
            raise ModelArtifactError("ONNX runtime returned a non-finite embedding")
        return tuple(tuple(float(value) for value in row) for row in output)


def onnx_cuda_loader(
    artifact_path: Path,
    selection: AcceleratorSelection,
    model: ApprovedEmbeddingModel,
) -> GpuInferenceRuntime:
    """Load only the pinned allowlisted ONNX CUDA runtime contract."""

    if (
        model.runtime != RUNTIME_NAME
        or model.runtime_revision != RUNTIME_REVISION
        or model.artifact_format != "ONNX"
        or model.inference_precision != "FP32"
    ):
        raise ModelArtifactError("model registry runtime is not supported by this image")
    return OnnxCudaRuntime(
        artifact_path,
        device_index=selection.device.index,
        model=model,
    )


def _create_session(artifact_path: Path, device_index: int) -> _InferenceSession:
    ort.preload_dlls(directory="")
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    try:
        return cast(
            _InferenceSession,
            ort.InferenceSession(
                artifact_path,
                sess_options=options,
                providers=[
                    (
                        "CUDAExecutionProvider",
                        {
                            "device_id": str(device_index),
                            "do_copy_in_default_stream": "1",
                        },
                    )
                ],
            ),
        )
    except Exception as error:
        # ONNX Runtime uses several pybind exception types for provider startup.
        # Preserve the cause without exposing its machine-specific text to callers.
        if _is_oom(error):
            raise AcceleratorOutOfMemory from error
        raise RuntimeError("onnx_session_initialization_failed") from error


def _is_oom(error: BaseException) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "out of memory",
            "cudaerrormemoryallocation",
            "cublas_status_alloc_failed",
        )
    )


__all__ = (
    "RUNTIME_NAME",
    "RUNTIME_REVISION",
    "OnnxCudaRuntime",
    "onnx_cuda_loader",
)
