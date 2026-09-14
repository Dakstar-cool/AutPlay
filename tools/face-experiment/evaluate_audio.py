"""Offline real-playlist inference diagnostics; no calibrated Face or quality labels."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from audio_inputs import load_manifest
from reference import infer, load_baseline, verify_artifacts


def read_signal(root: Path, clip: dict) -> np.ndarray:
    """Read exactly one bounded PCM artifact and verify its complete digest."""
    path = root / clip["file"]
    if path.name != clip["file"] or path.is_symlink():
        raise ValueError("clip_path_invalid")
    if not 640000 <= path.stat().st_size <= 768000:
        raise ValueError("clip_size_invalid")
    raw = path.read_bytes()
    if len(raw) != clip["samples"] * 4 or hashlib.sha256(raw).hexdigest() != clip["pcm_sha256"]:
        raise ValueError("clip_digest_mismatch")
    signal = np.frombuffer(raw, dtype="<f4").copy()
    if not np.isfinite(signal).all():
        raise ValueError("nonfinite_pcm")
    return signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", choices=["musicnn-deam", "effnet-jamendo"], required=True)
    parser.add_argument("--models", type=Path, default=Path("/models"))
    parser.add_argument("--clips", type=Path, default=Path("/clips"))
    parser.add_argument("--output", type=Path, default=Path("/output"))
    args = parser.parse_args()
    manifest_bytes, manifest = load_manifest(args.clips)
    clips = manifest["clips"]
    verify_artifacts(args.models)
    started = time.perf_counter()
    extractor, head, dimension, card = load_baseline(args.baseline, args.models)
    load_seconds = time.perf_counter() - started
    # Separate an actual-audio warmup from the measured sample pass.
    warm_started = time.perf_counter()
    warmup_signal = read_signal(args.clips, clips[0])
    infer(warmup_signal, extractor, head, dimension, len(card["classes"]))
    warmup_seconds = time.perf_counter() - warm_started
    rows, repeats, predictions_all = [], [], []
    track_embeddings = defaultdict(list)
    repeat_indices = {0, len(clips) // 2, len(clips) - 1}
    for index, clip in enumerate(clips):
        signal = read_signal(args.clips, clip)
        started = time.perf_counter()
        embeddings, predictions = infer(signal, extractor, head, dimension, len(card["classes"]))
        elapsed = time.perf_counter() - started
        track_embeddings[clip["track_id"]].append(embeddings.astype(np.float64))
        predictions_all.append(predictions)
        rows.append(
            {
                "track_id": clip["track_id"],
                "clip_index": clip["clip_index"],
                "clip_start_ms": clip["clip_start_ms"],
                "pcm_sha256": clip["pcm_sha256"],
                "duration_seconds": len(signal) / 16000,
                "elapsed_seconds": elapsed,
                "embedding_shape": list(embeddings.shape),
                "prediction_shape": list(predictions.shape),
                "rms": float(np.sqrt(np.mean(signal.astype(np.float64) ** 2))),
                "near_silence_fraction": float(np.mean(np.abs(signal) < 0.0001)),
                "pcm_above_unit_fraction": float(np.mean(np.abs(signal) > 1)),
                # Row indices are retained; no unverified window-to-millisecond mapping is invented.
                "raw_predictions": predictions.tolist(),
            }
        )
        if index in repeat_indices:
            again_emb, again_pred = infer(signal, extractor, head, dimension, len(card["classes"]))
            emb_delta = float(np.max(np.abs(again_emb - embeddings)))
            pred_delta = float(np.max(np.abs(again_pred - predictions)))
            if max(emb_delta, pred_delta) > 1e-6:
                raise ValueError("repeat_inference_mismatch")
            repeats.append(
                {
                    "clip_index_in_manifest": index,
                    "embedding_max_abs_delta": emb_delta,
                    "prediction_max_abs_delta": pred_delta,
                }
            )
    track_ids = sorted(track_embeddings)
    pooled = np.stack([np.concatenate(track_embeddings[key]).mean(axis=0) for key in track_ids])
    norms = np.linalg.norm(pooled, axis=1)
    if np.any(norms <= 1e-12):
        raise ValueError("pooled_embedding_zero_norm")
    pooled /= norms[:, None]
    cosine = pooled @ pooled.T
    np.testing.assert_allclose(np.diag(cosine), 1, atol=1e-6)
    np.testing.assert_allclose(cosine, cosine.T, atol=1e-7)
    off_diagonal = cosine[np.triu_indices(len(track_ids), k=1)]
    nearest = []
    for index, key in enumerate(track_ids):
        order = sorted(
            (other for other in range(len(track_ids)) if other != index),
            key=lambda other: (-float(cosine[index, other]), track_ids[other]),
        )[:3]
        nearest.append(
            {
                "track_id": key,
                "neighbors": [
                    {"track_id": track_ids[other], "cosine": float(cosine[index, other])}
                    for other in order
                ],
            }
        )
    prediction_matrix = np.concatenate(predictions_all).astype(np.float64)
    lower, upper = (1.0, 9.0) if args.baseline == "musicnn-deam" else (0.0, 1.0)
    elapsed = sum(row["elapsed_seconds"] for row in rows)
    duration = sum(row["duration_seconds"] for row in rows)
    report = {
        "status": "REAL_AUDIO_TECHNICAL_PASS",
        "baseline": args.baseline,
        "source_kind": "USER_PLAYLIST_UNLABELED_MUSIC",
        "musical_quality_evaluated": False,
        "interpreter_approved": False,
        "calibrated_confidence_available": False,
        "target_gpu_measured": False,
        "production_onnx_cuda_qualified": False,
        "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "model_manifest_sha256": hashlib.sha256(
            (args.models / "artifacts.json").read_bytes()
        ).hexdigest(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "essentia_tensorflow": importlib.metadata.version("essentia-tensorflow"),
            "numpy": np.__version__,
            "cpu_limit": 2,
        },
        "tracks": len(track_ids),
        "clips_count": len(rows),
        "audio_seconds": duration,
        "model_load_seconds": load_seconds,
        "warmup_seconds": warmup_seconds,
        "inference_seconds": elapsed,
        "real_time_factor": elapsed / duration,
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "measurement_scope": (
            "CPU container; acquisition concurrent; technical timing, not capacity qualification"
        ),
        "class_order": card["classes"],
        "class_min": prediction_matrix.min(axis=0).tolist(),
        "class_max": prediction_matrix.max(axis=0).tolist(),
        "class_std": prediction_matrix.std(axis=0).tolist(),
        "values_outside_nominal_range": int(
            np.sum((prediction_matrix < lower) | (prediction_matrix > upper))
        ),
        "repeat_checks": repeats,
        "clips": rows,
        "retrieval_diagnostics": {
            "quality_measured": False,
            "tracks": len(track_ids),
            "off_diagonal_cosine_min": float(off_diagonal.min()) if off_diagonal.size else None,
            "off_diagonal_cosine_max": float(off_diagonal.max()) if off_diagonal.size else None,
            "distinct_pooled_vectors": int(np.unique(pooled, axis=0).shape[0]),
            "nearest": nearest,
        },
    }
    args.output.mkdir(exist_ok=True)
    (args.output / f"{args.baseline}-real.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "baseline",
                    "tracks",
                    "clips_count",
                    "inference_seconds",
                    "process_peak_rss_kib",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
