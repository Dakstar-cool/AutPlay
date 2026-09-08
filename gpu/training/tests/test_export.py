"""Sona-Lite ONNX export and runtime-shape compatibility evidence."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
import torch
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS
from autplay_sona_training.export import export_sona_onnx
from autplay_sona_training.model import SonaLiteConfig, SonaLiteModel


def test_exported_graph_has_exact_runtime_contract_and_executes_on_cpu(tmp_path: Path) -> None:
    torch.manual_seed(11)
    model = SonaLiteModel(SonaLiteConfig(codebook_size=16, model_dimensions=16, encoder_layers=1))
    artifact_path = tmp_path / "sona-lite.onnx"

    exported = export_sona_onnx(model, artifact_path)
    manifest_path = tmp_path / "sona-lite.onnx.manifest.json"
    session = ort.InferenceSession(artifact_path, providers=["CPUExecutionProvider"])
    feed = {
        "history_sids": np.zeros((1, SONA_MAX_HISTORY_EVENTS, 3), dtype=np.int64),
        "history_actions": np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64),
        "history_origins": np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64),
        "history_age_buckets": np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64),
        "history_mask": np.zeros((1, SONA_MAX_HISTORY_EVENTS), dtype=np.int64),
        "candidate_sids": np.zeros((1, SONA_MAX_CANDIDATES, 3), dtype=np.int64),
        "candidate_mask": np.zeros((1, SONA_MAX_CANDIDATES), dtype=np.int64),
        "seed": np.asarray((19,), dtype=np.int64),
    }
    feed["history_sids"][0, -1] = (1, 2, 3)
    feed["history_mask"][0, -1] = 1
    feed["candidate_sids"][0, :2] = ((1, 2, 3), (4, 5, 6))
    feed["candidate_mask"][0, :2] = 1
    outputs = session.run(None, feed)
    with torch.inference_mode():
        eager = model(*(torch.from_numpy(value) for value in feed.values()))

    assert len(exported.model_manifest_sha256) == 64
    assert len(exported.artifact_sha256) == 64
    assert exported.commit_sha256 is not None and len(exported.commit_sha256) == 64
    assert exported.quality_eligible is False
    assert b"UNBOUND_TEST_ONLY" in manifest_path.read_bytes()
    assert (tmp_path / "sona-lite.onnx.commit.json").is_file()
    assert outputs[0].shape == (1, 1, 3)
    assert np.all(outputs[0] > 0)
    assert outputs[1].shape == (1, 1)
    assert outputs[2].shape == (1, SONA_MAX_CANDIDATES, 4)
    assert outputs[3].shape == (1, SONA_MAX_CANDIDATES)
    np.testing.assert_array_equal(outputs[0], eager[0].numpy())
    for actual, expected in zip(outputs[1:], eager[1:], strict=True):
        np.testing.assert_allclose(actual, expected.numpy(), rtol=1e-4, atol=1e-5)
