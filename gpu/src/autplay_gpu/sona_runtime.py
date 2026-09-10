"""Multi-input ONNX CUDA runtime for the shared-encoder Sona-Lite graph."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
import onnxruntime as ort  # type: ignore[import-untyped]
from autplay.application.enrichment import AcceleratorOutOfMemory
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    SONA_RANKING_HEADS,
    SonaGeneratedCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
)

from .embedding import ModelArtifactError
from .sona_tensors import pack_sona_requests

SONA_RUNTIME_NAME = "SONA_LITE_ONNX_CUDA"
SONA_RUNTIME_REVISION = "1"
_INPUT_NAMES = (
    "history_sids",
    "history_actions",
    "history_origins",
    "history_age_buckets",
    "history_mask",
    "candidate_sids",
    "candidate_mask",
    "seed",
)
_OUTPUT_NAMES = (
    "generated_sids",
    "generated_log_probabilities",
    "ranking_head_scores",
    "ranking_scores",
)


class _NodeArgument(Protocol):
    name: str
    shape: Sequence[int | str | None]
    type: str


class _InferenceSession(Protocol):
    def get_inputs(self) -> Sequence[_NodeArgument]: ...

    def get_outputs(self) -> Sequence[_NodeArgument]: ...

    def get_providers(self) -> Sequence[str]: ...

    def disable_fallback(self) -> None: ...

    def run(
        self,
        output_names: Sequence[str],
        input_feed: dict[str, npt.NDArray[np.generic]],
    ) -> Sequence[object]: ...


type SonaSessionFactory = Callable[[bytes, int], _InferenceSession]


class SonaOnnxCudaRuntime:
    """Run one bounded Sona graph whose encoder state feeds generation and ranking."""

    def __init__(
        self,
        artifact: bytes,
        *,
        device_index: int,
        session_factory: SonaSessionFactory | None = None,
    ) -> None:
        try:
            self._session = (session_factory or _create_session)(artifact, device_index)
        except AcceleratorOutOfMemory:
            raise
        except RuntimeError as error:
            if _is_oom(error):
                raise AcceleratorOutOfMemory from error
            raise ModelArtifactError("Sona ONNX CUDA session initialization failed") from error
        providers = self._session.get_providers()
        if not providers or providers[0] != "CUDAExecutionProvider":
            raise ModelArtifactError("Sona CUDA execution provider is unavailable")
        inputs = {value.name: value for value in self._session.get_inputs()}
        outputs = {value.name: value for value in self._session.get_outputs()}
        if tuple(inputs) != _INPUT_NAMES or tuple(outputs) != _OUTPUT_NAMES:
            raise ModelArtifactError("Sona ONNX input/output contract is invalid")
        if any(inputs[name].type != "tensor(int64)" for name in _INPUT_NAMES):
            raise ModelArtifactError("Sona ONNX inputs must use int64 tensors")
        if outputs["generated_sids"].type != "tensor(int64)" or any(
            outputs[name].type != "tensor(float)" for name in _OUTPUT_NAMES[1:]
        ):
            raise ModelArtifactError("Sona ONNX outputs use invalid tensor types")

    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        """Right-pad one request, execute both heads, and return bounded finite output."""

        feed = pack_sona_requests((request,)).as_feed()
        try:
            raw = self._session.run(_OUTPUT_NAMES, feed)
        except RuntimeError as error:
            if _is_oom(error):
                raise AcceleratorOutOfMemory from error
            raise ModelArtifactError("Sona ONNX CUDA inference failed") from error
        if len(raw) != len(_OUTPUT_NAMES):
            raise ModelArtifactError("Sona ONNX returned an invalid output count")
        generated_sids = np.asarray(raw[0])
        generated_log_probabilities = np.asarray(raw[1])
        ranking_head_scores = np.asarray(raw[2])
        ranking_scores = np.asarray(raw[3])
        generated_count = generated_sids.shape[1] if generated_sids.ndim == 3 else -1
        candidate_count = len(request.candidates)
        if (
            generated_sids.shape != (1, generated_count, 3)
            or generated_sids.dtype != np.int64
            or generated_log_probabilities.shape != (1, generated_count)
            or generated_log_probabilities.dtype != np.float32
            or ranking_head_scores.shape != (1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS))
            or ranking_head_scores.dtype != np.float32
            or ranking_scores.shape != (1, SONA_MAX_CANDIDATES)
            or ranking_scores.dtype != np.float32
        ):
            raise ModelArtifactError("Sona ONNX returned invalid output shapes")
        if (
            generated_count != 1
            or not np.all(generated_sids > 0)
            or not all(
                np.isfinite(value).all()
                for value in (
                    generated_log_probabilities,
                    ranking_head_scores,
                    ranking_scores,
                )
            )
        ):
            raise ModelArtifactError("Sona ONNX returned unbounded or non-finite output")
        generated = tuple(
            SonaGeneratedCandidate(
                SonaSemanticId(*(int(value) for value in generated_sids[0, index])),
                float(generated_log_probabilities[0, index]),
            )
            for index in range(generated_count)
        )
        ranked = tuple(
            sorted(
                (
                    SonaRankedCandidate(
                        request.candidates[index].recording_id,
                        cast(
                            tuple[float, float, float, float],
                            tuple(float(value) for value in ranking_head_scores[0, index]),
                        ),
                        float(ranking_scores[0, index]),
                    )
                    for index in range(candidate_count)
                ),
                key=lambda value: (-value.combined_score, value.recording_id.hex),
            )
        )
        return SonaInferenceOutput(request.request_sha256, generated, ranked)


def _create_session(artifact: bytes, device_index: int) -> _InferenceSession:
    ort.preload_dlls(directory="")
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.use_deterministic_compute = True
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    try:
        session = cast(
            _InferenceSession,
            ort.InferenceSession(
                artifact,
                sess_options=options,
                providers=[
                    (
                        "CUDAExecutionProvider",
                        {"device_id": str(device_index), "do_copy_in_default_stream": "1"},
                    )
                ],
            ),
        )
        session.disable_fallback()
        return session
    except Exception as error:
        if _is_oom(error):
            raise AcceleratorOutOfMemory from error
        raise RuntimeError("sona_onnx_session_initialization_failed") from error


def _is_oom(error: BaseException) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in ("out of memory", "cudaerrormemoryallocation", "cublas_status_alloc_failed")
    )


__all__ = (
    "SONA_RUNTIME_NAME",
    "SONA_RUNTIME_REVISION",
    "SonaOnnxCudaRuntime",
)
