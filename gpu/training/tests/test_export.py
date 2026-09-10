"""Sona-Lite ONNX export and runtime-shape compatibility evidence."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
import torch
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS
from autplay_sona_training.export import export_sona_onnx
from autplay_sona_training.model import SonaLiteConfig, SonaLiteModel


def test_exported_graph_has_exact_runtime_contract_and_matches_eager_matrix(
    tmp_path: Path,
) -> None:
    torch.manual_seed(11)
    model = SonaLiteModel(SonaLiteConfig(codebook_size=16, model_dimensions=16, encoder_layers=1))
    artifact_path = tmp_path / "sona-lite.onnx"

    exported = export_sona_onnx(model, artifact_path)
    manifest_path = tmp_path / "sona-lite.onnx.manifest.json"
    session = ort.InferenceSession(artifact_path, providers=["CPUExecutionProvider"])
    assert len(exported.model_manifest_sha256) == 64
    assert len(exported.artifact_sha256) == 64
    assert exported.commit_sha256 is not None and len(exported.commit_sha256) == 64
    assert exported.quality_eligible is False
    assert b"UNBOUND_TEST_ONLY" in manifest_path.read_bytes()
    assert (tmp_path / "sona-lite.onnx.commit.json").is_file()
    for case_name, history_count, candidate_count in (
        ("empty", 0, 0),
        ("single", 1, 1),
        ("maximum", SONA_MAX_HISTORY_EVENTS, SONA_MAX_CANDIDATES),
    ):
        feed = _matrix_feed(history_count=history_count, candidate_count=candidate_count)
        outputs = session.run(None, feed)
        with torch.inference_mode():
            eager = model(*(torch.from_numpy(value) for value in feed.values()))

        assert outputs[0].shape == (1, 1, 3), case_name
        assert np.all(outputs[0] > 0), case_name
        assert outputs[1].shape == (1, 1), case_name
        assert outputs[2].shape == (1, SONA_MAX_CANDIDATES, 4), case_name
        assert outputs[3].shape == (1, SONA_MAX_CANDIDATES), case_name
        np.testing.assert_array_equal(outputs[0], eager[0].numpy(), err_msg=case_name)
        for actual, expected in zip(outputs[1:], eager[1:], strict=True):
            np.testing.assert_allclose(
                actual,
                expected.numpy(),
                rtol=1e-4,
                atol=1e-5,
                err_msg=case_name,
            )


def _matrix_feed(
    *,
    history_count: int,
    candidate_count: int,
) -> dict[str, np.ndarray]:
    history_sids = np.zeros((1, SONA_MAX_HISTORY_EVENTS, 3), dtype=np.int64)
    history_actions = np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_origins = np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_age_buckets = np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_mask = np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    candidate_sids = np.zeros((1, SONA_MAX_CANDIDATES, 3), dtype=np.int64)
    candidate_mask = np.zeros((1, SONA_MAX_CANDIDATES), dtype=np.int64)
    if history_count:
        history_start = SONA_MAX_HISTORY_EVENTS - history_count
        values = np.arange(history_count * 3, dtype=np.int64).reshape(history_count, 3)
        history_sids[0, history_start:] = (values % 15) + 1
        history_actions[0, history_start:] = (np.arange(history_count) % 9) + 1
        history_origins[0, history_start:] = (np.arange(history_count) % 5) + 1
        history_age_buckets[0, history_start:] = np.arange(history_count) % 64
        history_mask[0, history_start:] = 1
    if candidate_count:
        values = np.arange(candidate_count * 3, dtype=np.int64).reshape(candidate_count, 3)
        candidate_sids[0, :candidate_count] = (values % 15) + 1
        candidate_mask[0, :candidate_count] = 1
    return {
        "history_sids": history_sids,
        "history_actions": history_actions,
        "history_origins": history_origins,
        "history_age_buckets": history_age_buckets,
        "history_mask": history_mask,
        "candidate_sids": candidate_sids,
        "candidate_mask": candidate_mask,
        "seed": np.asarray((19 + history_count + candidate_count,), dtype=np.int64),
    }
