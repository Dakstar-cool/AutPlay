"""Sona-Lite multi-input ONNX contract and shared-request output evidence."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import numpy as np
import numpy.typing as npt
import pytest
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    SONA_MAX_HISTORY_EVENTS,
    SonaAction,
    SonaCandidate,
    SonaHistoryEvent,
    SonaInferenceRequest,
    SonaOrigin,
    SonaSemanticId,
)

from autplay_gpu import sona_runtime
from autplay_gpu.embedding import ModelArtifactError
from autplay_gpu.sona_runtime import SonaOnnxCudaRuntime

OWNER = UUID("00000000-0000-7000-8000-000000000001")
TRACK_A = UUID("00000000-0000-7000-8000-000000000301")
TRACK_B = UUID("00000000-0000-7000-8000-000000000302")


class _Node:
    def __init__(self, name: str, tensor_type: str) -> None:
        self.name = name
        self.type = tensor_type
        self.shape: list[int | str | None] = []


class _Session:
    def __init__(self) -> None:
        self.feed: dict[str, npt.NDArray[np.generic]] | None = None

    def get_inputs(self) -> Sequence[Any]:
        return tuple(
            _Node(name, "tensor(int64)")
            for name in (
                "history_sids",
                "history_actions",
                "history_origins",
                "history_age_buckets",
                "history_mask",
                "candidate_sids",
                "candidate_mask",
                "seed",
            )
        )

    def get_outputs(self) -> Sequence[Any]:
        return (
            _Node("generated_sids", "tensor(int64)"),
            _Node("generated_log_probabilities", "tensor(float)"),
            _Node("ranking_head_scores", "tensor(float)"),
            _Node("ranking_scores", "tensor(float)"),
        )

    def get_providers(self) -> Sequence[str]:
        return ("CUDAExecutionProvider",)

    def run(
        self,
        output_names: Sequence[str],
        input_feed: dict[str, npt.NDArray[np.generic]],
    ) -> Sequence[object]:
        del output_names
        self.feed = input_feed
        heads = np.zeros((1, SONA_MAX_CANDIDATES, 4), dtype=np.float32)
        heads[0, 0] = (0.1, 0.2, -0.3, 0.4)
        heads[0, 1] = (0.5, 0.6, -0.1, 0.2)
        scores = np.zeros((1, SONA_MAX_CANDIDATES), dtype=np.float32)
        scores[0, :2] = (0.25, 0.75)
        return (
            np.asarray([[[7, 8, 9]]], dtype=np.int64),
            np.asarray([[-0.1]], dtype=np.float32),
            heads,
            scores,
        )


def _request() -> SonaInferenceRequest:
    return SonaInferenceRequest(
        owner_user_id=OWNER,
        temporal_snapshot_id=UUID(int=10),
        baseline_snapshot_id=UUID(int=11),
        cutoff_at_ms=1_000,
        interaction_watermark=2,
        tokenizer_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        seed=19,
        history=(
            SonaHistoryEvent(
                UUID(int=1),
                TRACK_A,
                SonaSemanticId(1, 2, 3),
                SonaAction.ORGANIC_LISTEN,
                SonaOrigin.ORGANIC,
                4,
                900,
                1,
            ),
        ),
        candidates=(
            SonaCandidate(TRACK_A, SonaSemanticId(1, 2, 3)),
            SonaCandidate(TRACK_B, SonaSemanticId(4, 5, 6)),
        ),
        request_sha256="c" * 64,
    )


def test_sona_runtime_pads_history_once_for_generation_and_ranking(tmp_path: Path) -> None:
    session = _Session()
    selected: list[int] = []

    artifact = b"verified-onnx-graph"

    def session_factory(payload: bytes, device_index: int) -> _Session:
        assert payload == artifact
        selected.append(device_index)
        return session

    runtime = SonaOnnxCudaRuntime(
        artifact,
        device_index=3,
        session_factory=session_factory,
    )
    result = runtime.infer(_request())

    assert selected == [3]
    assert session.feed is not None
    assert session.feed["history_mask"].shape == (1, SONA_MAX_HISTORY_EVENTS)
    assert session.feed["history_mask"][0, -1] == 1
    assert session.feed["history_sids"][0, -1].tolist() == [1, 2, 3]
    assert session.feed["candidate_mask"][0, :3].tolist() == [1, 1, 0]
    assert result.request_sha256 == "c" * 64
    assert result.generated[0].semantic_id.values == (7, 8, 9)
    assert tuple(value.recording_id for value in result.ranked) == (TRACK_B, TRACK_A)


def test_sona_runtime_rejects_zero_generated_semantic_id(tmp_path: Path) -> None:
    class ZeroSession(_Session):
        def run(
            self,
            output_names: Sequence[str],
            input_feed: dict[str, npt.NDArray[np.generic]],
        ) -> Sequence[object]:
            values = list(super().run(output_names, input_feed))
            values[0] = np.asarray([[[0, 0, 0]]], dtype=np.int64)
            return values

    runtime = SonaOnnxCudaRuntime(
        b"verified-onnx-graph",
        device_index=0,
        session_factory=lambda path, device_index: ZeroSession(),
    )

    with pytest.raises(ModelArtifactError, match="unbounded or non-finite"):
        runtime.infer(_request())


def test_default_session_loads_verified_bytes_with_deterministic_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Options:
        enable_mem_pattern = True
        intra_op_num_threads = 0
        inter_op_num_threads = 0
        use_deterministic_compute = False

    class Session:
        pass

    monkeypatch.setattr(
        "autplay_gpu.sona_runtime.ort.preload_dlls", lambda directory: None
    )
    monkeypatch.setattr("autplay_gpu.sona_runtime.ort.SessionOptions", Options)

    def create_session(
        artifact: bytes, *, sess_options: Options, providers: object
    ) -> Session:
        captured.update(artifact=artifact, options=sess_options, providers=providers)
        return Session()

    monkeypatch.setattr("autplay_gpu.sona_runtime.ort.InferenceSession", create_session)

    sona_runtime._create_session(b"verified-graph-bytes", 3)

    options = cast(Options, captured["options"])
    assert captured["artifact"] == b"verified-graph-bytes"
    assert options.use_deterministic_compute is True
    assert options.enable_mem_pattern is False
