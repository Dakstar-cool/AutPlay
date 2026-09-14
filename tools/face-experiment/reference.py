"""Offline frozen-model technical preflight; never emits an approved Face timeline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import time
from pathlib import Path

import numpy as np
from essentia.standard import (
    TensorflowPredict2D,
    TensorflowPredictEffnetDiscogs,
    TensorflowPredictMusiCNN,
)


def verify_artifacts(models: Path) -> None:
    """Check the image-owned allowlist before loading any graph."""
    trusted_manifest = Path(__file__).with_name("artifacts.lock.json").read_bytes()
    if (models / "artifacts.json").read_bytes() != trusted_manifest:
        raise ValueError("artifact_manifest_mismatch")
    manifest = json.loads(trusted_manifest)
    for artifact in manifest["files"]:
        path = models / artifact["name"]
        if (
            path.parent != models
            or path.is_symlink()
            or path.stat().st_size != artifact["size_bytes"]
        ):
            raise ValueError("artifact_size_mismatch")
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != artifact["sha256"]:
                raise ValueError("artifact_digest_mismatch")


def load_baseline(baseline: str, models: Path) -> tuple:
    """Load compatible frozen extractor/head pairs without retrieval pooling."""
    if baseline == "musicnn-deam":
        extractor = TensorflowPredictMusiCNN(
            graphFilename=str(models / "musicnn.pb"),
            output="model/dense/BiasAdd",
            patchSize=187,
            patchHopSize=93,
            batchSize=64,
            lastPatchMode="discard",
        )
        head_name, head_output, embedding_size = "deam", "model/Identity", 200
    elif baseline == "effnet-jamendo":
        extractor = TensorflowPredictEffnetDiscogs(
            graphFilename=str(models / "effnet.pb"),
            output="PartitionedCall:1",
            patchSize=128,
            patchHopSize=62,
            batchSize=64,
            lastPatchMode="discard",
            lastBatchMode="same",
        )
        head_name, head_output, embedding_size = "jamendo", "model/Sigmoid", 1280
    else:
        raise ValueError("baseline_invalid")
    head = TensorflowPredict2D(
        graphFilename=str(models / f"{head_name}.pb"),
        input="model/Placeholder",
        output=head_output,
    )
    card = json.loads((models / f"{head_name}.json").read_text())
    return extractor, head, embedding_size, card


def infer(signal: np.ndarray, extractor, head, embedding_size: int, class_count: int) -> tuple:
    """Reject empty, nonfinite or incompatible model tensors."""
    embeddings = extractor(signal)
    if embeddings.ndim != 2 or embeddings.shape[0] == 0 or embeddings.shape[1] != embedding_size:
        raise ValueError("embedding_shape_mismatch")
    predictions = head(embeddings)
    if predictions.shape != (embeddings.shape[0], class_count):
        raise ValueError("prediction_shape_mismatch")
    if not np.isfinite(embeddings).all() or not np.isfinite(predictions).all():
        raise ValueError("nonfinite_model_result")
    return embeddings, predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", choices=["musicnn-deam", "effnet-jamendo"], required=True)
    parser.add_argument("--models", type=Path, default=Path("/models"))
    parser.add_argument("--output", type=Path, default=Path("/output"))
    args = parser.parse_args()
    verify_artifacts(args.models)
    started = time.perf_counter()
    extractor, head, embedding_size, card = load_baseline(args.baseline, args.models)
    load_seconds = time.perf_counter() - started
    sample_rate, duration_seconds = 16_000, 12
    source_time = np.arange(sample_rate * duration_seconds, dtype=np.float64) / sample_rate
    fixtures = {
        "silence": np.zeros(source_time.size, dtype=np.float32),
        "steady_tone": (0.2 * np.sin(2 * np.pi * 440 * source_time)).astype(np.float32),
        "pulsed_chord": (
            0.05
            * (
                np.sin(2 * np.pi * 220 * source_time)
                + np.sin(2 * np.pi * 277.18 * source_time)
                + np.sin(2 * np.pi * 329.63 * source_time)
            )
            * (0.2 + 0.8 * (np.mod(source_time, 0.5) < 0.12))
        ).astype(np.float32),
    }
    rows = []
    for fixture_name, signal in fixtures.items():
        started = time.perf_counter()
        embeddings, predictions = infer(
            signal, extractor, head, embedding_size, len(card["classes"])
        )
        elapsed = time.perf_counter() - started
        # Preserve segment outputs; mean/L2-pooled retrieval embeddings cannot feed these heads.
        rows.append(
            {
                "fixture": fixture_name,
                "pcm_f32le_sha256": hashlib.sha256(signal.astype("<f4").tobytes()).hexdigest(),
                "sample_rate": sample_rate,
                "duration_seconds": duration_seconds,
                "embedding_shape": list(embeddings.shape),
                "prediction_shape": list(predictions.shape),
                "elapsed_seconds": elapsed,
                "real_time_factor": elapsed / duration_seconds,
                "raw_predictions": predictions.tolist(),
            }
        )
    report = {
        "status": "TECHNICAL_PREFLIGHT_PASS",
        "baseline": args.baseline,
        "source_kind": "GENERATED_SYNTHETIC_PCM_NOT_MUSIC_EVALUATION",
        "musical_quality_evaluated": False,
        "interpreter_approved": False,
        "calibrated_confidence_available": False,
        "target_gpu_measured": False,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "essentia_tensorflow": importlib.metadata.version("essentia-tensorflow"),
            "numpy": np.__version__,
            "cpu_limit": 2,
        },
        "model_manifest_sha256": hashlib.sha256(
            (args.models / "artifacts.json").read_bytes()
        ).hexdigest(),
        "model_load_seconds": load_seconds,
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "measurement_scope": (
            "One isolated CPU process, three fixtures in order; "
            "first inference cold, no performance qualification"
        ),
        "class_order": card["classes"],
        "fixtures": rows,
    }
    args.output.mkdir(exist_ok=True)
    (args.output / f"{args.baseline}.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ["status", "baseline", "model_load_seconds", "process_peak_rss_kib"]
            }
        )
    )


if __name__ == "__main__":
    main()
