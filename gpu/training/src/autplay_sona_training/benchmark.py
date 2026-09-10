"""Bounded ONNX Runtime smoke and latency evidence for committed Sona-Lite artifacts."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter, time_ns
from typing import cast

import numpy as np
import numpy.typing as npt
import onnxruntime as ort  # type: ignore[import-untyped]
import rfc8785
from autplay.domain.recommendations import JsonValue

from .dataset import SonaTensorDataset, load_sona_dataset
from .quality_bundle import (
    SonaQualityDatasetBundle,
    reverify_quality_approved_sona_dataset_bundle,
)
from .tokenizer import load_sona_tokenizer
from .trainer import DevicePreference, load_quality_sona_checkpoint, load_sona_checkpoint

SONA_MAX_BENCHMARK_ARTIFACT_BYTES = 536_870_912
SONA_MAX_BENCHMARK_SIDECAR_BYTES = 1_048_576
SONA_MAX_ORT_PROFILE_BYTES = 67_108_864


@dataclass(frozen=True, slots=True)
class SonaBenchmarkResult:
    evidence_sha256: str
    artifact_sha256: str
    model_manifest_sha256: str
    commit_sha256: str
    checkpoint_manifest_sha256: str
    checkpoint_weights_sha256: str
    dataset_manifest_sha256: str
    tokenizer_manifest_sha256: str
    device_type: str
    device_name: str
    cuda_only_execution: bool
    iterations: int
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    generated_nonzero: bool
    generated_within_tokenizer: bool
    generated_expandable: bool
    quality_provenance_eligible: bool
    quality_eligible: bool


def benchmark_sona_checkpoint(
    checkpoint_directory: Path,
    artifact_path: Path,
    dataset_directory: Path,
    tokenizer_directory: Path,
    evidence_path: Path,
    *,
    device_preference: DevicePreference,
    warmup_iterations: int = 5,
    measured_iterations: int = 20,
    quality_bundle: SonaQualityDatasetBundle | None = None,
    before_publish: Callable[[], object] | None = None,
) -> SonaBenchmarkResult:
    """Execute the exact committed ONNX artifact and persist content-addressed evidence."""

    if not 0 <= warmup_iterations <= 1_000 or not 1 <= measured_iterations <= 10_000:
        raise ValueError("Sona benchmark iteration count is outside the accepted bound")
    if quality_bundle is not None and (device_preference != "cuda" or warmup_iterations < 1):
        raise ValueError("Sona quality benchmark requires CUDA and at least one warmup iteration")
    if evidence_path.exists():
        raise FileExistsError(f"Sona benchmark evidence already exists: {evidence_path}")
    if quality_bundle is None:
        _, checkpoint = load_sona_checkpoint(checkpoint_directory)
    else:
        _, checkpoint = load_quality_sona_checkpoint(checkpoint_directory, quality_bundle)
    dataset = load_sona_dataset(dataset_directory)
    tokenizer = load_sona_tokenizer(tokenizer_directory)
    expected_dataset_manifest = (
        checkpoint.dataset_manifest_sha256
        if quality_bundle is None
        else quality_bundle.test.manifest_sha256
    )
    if dataset.manifest_sha256 != expected_dataset_manifest:
        raise ValueError("Sona benchmark dataset does not match its verified evaluation split")
    if (
        checkpoint.tokenizer_sha256 != tokenizer.manifest_sha256
        or dataset.tokenizer_sha256 != tokenizer.manifest_sha256
        or checkpoint.tokenizer_active_codes_per_level != tokenizer.centroids.shape[1]
        or dataset.tokenizer_active_codes_per_level != tokenizer.centroids.shape[1]
    ):
        raise ValueError("Sona benchmark tokenizer does not match dataset and checkpoint")

    artifact_bytes, manifest, artifact_sha256, model_manifest_sha256, commit_sha256 = (
        _load_committed_artifact(artifact_path)
    )
    provenance = _expect_object(manifest.get("training_provenance"), "training_provenance")
    expected_provenance: dict[str, JsonValue] = {
        "checkpoint_manifest_sha256": checkpoint.checkpoint_manifest_sha256,
        "checkpoint_weights_sha256": checkpoint.weights_sha256,
        "dataset_manifest_sha256": checkpoint.dataset_manifest_sha256,
        "tokenizer_sha256": checkpoint.tokenizer_sha256,
        "dataset_approval_sha256": checkpoint.dataset_approval_sha256,
        "dataset_bundle_sha256": checkpoint.dataset_bundle_sha256,
        "quality_provenance_eligible": checkpoint.quality_provenance_eligible,
        "quality_eligible": checkpoint.quality_eligible,
    }
    if (
        provenance != expected_provenance
        or manifest.get("weights_sha256") != checkpoint.weights_sha256
    ):
        raise ValueError("Sona benchmark artifact does not match the verified checkpoint")

    provider, device_type = _select_provider(device_preference)
    cuda_only_execution = False
    if quality_bundle is not None:
        session_options = ort.SessionOptions()
        session_options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        session = ort.InferenceSession(
            artifact_bytes,
            sess_options=session_options,
            providers=[provider],
        )
        session.disable_fallback()
    else:
        session = ort.InferenceSession(artifact_bytes, providers=[provider])
    if quality_bundle is not None and session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("Sona quality benchmark did not execute with CUDAExecutionProvider")
    if quality_bundle is not None:
        _verify_cuda_execution_profile(
            artifact_bytes,
            provider=provider,
            inputs=_example_inputs(dataset, 0),
        )
        cuda_only_execution = True
    expandable_semantic_ids = {semantic_id.values for semantic_id in tokenizer.semantic_ids}
    latencies: list[float] = []
    raw_samples: list[JsonValue] = []
    generated_nonzero = True
    generated_within_tokenizer = True
    generated_expandable = True
    for example_index, raw_request_sha256 in enumerate(dataset.request_sha256.tolist()):
        inputs = _example_inputs(dataset, example_index)
        request_sha256 = raw_request_sha256.decode("ascii")
        for iteration in range(warmup_iterations + measured_iterations):
            started = perf_counter()
            outputs = session.run(None, inputs)
            elapsed_ms = (perf_counter() - started) * 1_000.0
            if iteration >= warmup_iterations:
                latencies.append(elapsed_ms)
                raw_samples.append(
                    {
                        "request_sha256": request_sha256,
                        "iteration": iteration - warmup_iterations,
                        "latency_ms": elapsed_ms,
                    }
                )
            generated = cast(npt.NDArray[np.int64], outputs[0])
            generated_nonzero &= bool(np.all(generated > 0))
            generated_within_tokenizer &= bool(
                np.all(generated <= dataset.tokenizer_active_codes_per_level)
            )
            generated_sid = tuple(int(value) for value in generated[0, 0].tolist())
            generated_expandable &= generated_sid in expandable_semantic_ids
    if not generated_nonzero or not generated_within_tokenizer or not generated_expandable:
        raise RuntimeError("Sona benchmark generated a Semantic ID outside tokenizer coverage")
    latency_values = np.asarray(latencies, dtype=np.float64)
    mean_latency_ms = float(latency_values.mean())
    p50_latency_ms = float(np.percentile(latency_values, 50, method="higher"))
    p95_latency_ms = float(np.percentile(latency_values, 95, method="higher"))
    device_name = session.get_providers()[0]
    environment_sha256 = sha256(
        rfc8785.dumps(
            {
                "device_type": device_type,
                "device_name": device_name,
                "onnxruntime_version": ort.__version__,
            }
        )
    ).hexdigest()
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "benchmark": "SONA_LITE_COMMITTED_ONNX_RUNTIME_V1",
        "artifact_sha256": artifact_sha256,
        "model_manifest_sha256": model_manifest_sha256,
        "commit_sha256": commit_sha256,
        "checkpoint_manifest_sha256": checkpoint.checkpoint_manifest_sha256,
        "checkpoint_weights_sha256": checkpoint.weights_sha256,
        "dataset_manifest_sha256": dataset.manifest_sha256,
        "data_classification": dataset.data_classification,
        "dataset_approval_sha256": checkpoint.dataset_approval_sha256,
        "dataset_bundle_sha256": checkpoint.dataset_bundle_sha256,
        "quality_provenance_eligible": checkpoint.quality_provenance_eligible,
        "quality_eligible": checkpoint.quality_eligible and dataset.quality_eligible,
        "device_type": device_type,
        "device_name": device_name,
        "onnxruntime_version": ort.__version__,
        "environment_sha256": environment_sha256,
        "cuda_only_execution": cuda_only_execution,
        "warmup_iterations": warmup_iterations,
        "measured_iterations": measured_iterations,
        "request_count": dataset.example_count,
        "raw_samples": raw_samples,
        "mean_latency_ms": mean_latency_ms,
        "p50_latency_ms": p50_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "generated_nonzero": generated_nonzero,
        "generated_within_tokenizer": generated_within_tokenizer,
        "generated_expandable": generated_expandable,
        "tokenizer_fit_manifest_sha256": tokenizer.manifest_sha256,
        "tokenizer_active_codes_per_level": dataset.tokenizer_active_codes_per_level,
    }
    evidence_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    envelope: dict[str, JsonValue] = {
        "schema_version": 1,
        "kind": "SONA_ORT_BENCHMARK_EVIDENCE_ENVELOPE_V1",
        "evidence": document,
        "evidence_sha256": evidence_sha256,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{evidence_path.name}.", dir=evidence_path.parent))
    try:
        temporary_evidence = temporary / evidence_path.name
        temporary_evidence.write_bytes(rfc8785.dumps(envelope))
        if quality_bundle is not None:
            reverify_quality_approved_sona_dataset_bundle(
                quality_bundle,
                at_ms=time_ns() // 1_000_000,
            )
        if before_publish is not None:
            before_publish()
        os.link(temporary_evidence, evidence_path)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return SonaBenchmarkResult(
        evidence_sha256=evidence_sha256,
        artifact_sha256=artifact_sha256,
        model_manifest_sha256=model_manifest_sha256,
        commit_sha256=commit_sha256,
        checkpoint_manifest_sha256=checkpoint.checkpoint_manifest_sha256,
        checkpoint_weights_sha256=checkpoint.weights_sha256,
        dataset_manifest_sha256=dataset.manifest_sha256,
        tokenizer_manifest_sha256=tokenizer.manifest_sha256,
        device_type=device_type,
        device_name=device_name,
        cuda_only_execution=cuda_only_execution,
        iterations=measured_iterations,
        mean_latency_ms=mean_latency_ms,
        p50_latency_ms=p50_latency_ms,
        p95_latency_ms=p95_latency_ms,
        generated_nonzero=generated_nonzero,
        generated_within_tokenizer=generated_within_tokenizer,
        generated_expandable=generated_expandable,
        quality_provenance_eligible=checkpoint.quality_provenance_eligible,
        quality_eligible=checkpoint.quality_eligible and dataset.quality_eligible,
    )


def _verify_cuda_execution_profile(
    artifact: bytes,
    *,
    provider: str,
    inputs: dict[str, npt.NDArray[np.generic]],
) -> None:
    """Prove node placement from one ORT trace without contaminating latency samples."""

    profile_directory = Path(tempfile.mkdtemp(prefix="autplay-sona-ort-profile."))
    profile_path: Path | None = None
    session: ort.InferenceSession | None = None
    try:
        options = ort.SessionOptions()
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        options.enable_profiling = True
        options.profile_file_prefix = str(profile_directory / "trace")
        session = ort.InferenceSession(
            artifact,
            sess_options=options,
            providers=[provider],
        )
        session.disable_fallback()
        active_providers = session.get_providers()
        if not active_providers or active_providers[0] != "CUDAExecutionProvider":
            raise RuntimeError("Sona quality profile did not select CUDAExecutionProvider")
        session.run(None, inputs)
        profile_path = Path(session.end_profiling())
        raw_events = cast(
            object,
            json.loads(_bounded_read(profile_path, SONA_MAX_ORT_PROFILE_BYTES)),
        )
        if not isinstance(raw_events, list):
            raise RuntimeError("Sona quality execution profile is invalid")
        node_providers: list[str] = []
        for raw_event in raw_events:
            if not isinstance(raw_event, dict) or raw_event.get("cat") != "Node":
                continue
            arguments = raw_event.get("args")
            if not isinstance(arguments, dict):
                raise RuntimeError("Sona quality execution profile omits node placement")
            node_provider = arguments.get("provider")
            if not isinstance(node_provider, str):
                raise RuntimeError("Sona quality execution profile omits node placement")
            node_providers.append(node_provider)
        if not node_providers or set(node_providers) != {"CUDAExecutionProvider"}:
            raise RuntimeError("Sona quality execution profile contains non-CUDA nodes")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Sona quality execution profile is unavailable") from error
    finally:
        if profile_path is None and session is not None:
            with suppress(Exception):
                session.end_profiling()
        shutil.rmtree(profile_directory, ignore_errors=True)


def _load_committed_artifact(
    artifact_path: Path,
) -> tuple[bytes, dict[str, JsonValue], str, str, str]:
    artifact = _bounded_read(artifact_path, SONA_MAX_BENCHMARK_ARTIFACT_BYTES)
    artifact_sha256 = sha256(artifact).hexdigest()
    manifest_envelope = _read_object(
        artifact_path.with_suffix(f"{artifact_path.suffix}.manifest.json")
    )
    if set(manifest_envelope) != {"manifest", "manifest_sha256"}:
        raise ValueError("Sona benchmark artifact manifest envelope is invalid")
    manifest = _expect_object(manifest_envelope.get("manifest"), "manifest")
    model_manifest_sha256 = _expect_string(
        manifest_envelope.get("manifest_sha256"), "manifest_sha256"
    )
    if (
        sha256(rfc8785.dumps(manifest)).hexdigest() != model_manifest_sha256
        or manifest.get("artifact_sha256") != artifact_sha256
    ):
        raise ValueError("Sona benchmark artifact manifest identity mismatch")
    commit_envelope = _read_object(artifact_path.with_suffix(f"{artifact_path.suffix}.commit.json"))
    if set(commit_envelope) != {"commit", "commit_sha256"}:
        raise ValueError("Sona benchmark artifact commit envelope is invalid")
    commit = _expect_object(commit_envelope.get("commit"), "commit")
    commit_sha256 = _expect_string(commit_envelope.get("commit_sha256"), "commit_sha256")
    expected_commit: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": artifact_sha256,
        "model_manifest_sha256": model_manifest_sha256,
    }
    if sha256(rfc8785.dumps(commit)).hexdigest() != commit_sha256 or commit != expected_commit:
        raise ValueError("Sona benchmark artifact commit identity mismatch")
    return artifact, manifest, artifact_sha256, model_manifest_sha256, commit_sha256


def _example_inputs(
    dataset: SonaTensorDataset, example_index: int
) -> dict[str, npt.NDArray[np.generic]]:
    names = (
        "history_sids",
        "history_actions",
        "history_origins",
        "history_age_buckets",
        "history_mask",
        "candidate_sids",
        "candidate_mask",
        "seed",
    )
    return {
        name: np.ascontiguousarray(
            cast(npt.NDArray[np.generic], getattr(dataset, name))[example_index : example_index + 1]
        )
        for name in names
    }


def _select_provider(preference: DevicePreference) -> tuple[str, str]:
    available = ort.get_available_providers()
    if preference == "cuda":
        if "CUDAExecutionProvider" not in available:
            raise RuntimeError("Sona CUDA benchmark was requested but CUDA ORT is unavailable")
        return "CUDAExecutionProvider", "cuda"
    if preference == "auto" and "CUDAExecutionProvider" in available:
        return "CUDAExecutionProvider", "cuda"
    if "CPUExecutionProvider" not in available:
        raise RuntimeError("Sona CPU ONNX Runtime provider is unavailable")
    return "CPUExecutionProvider", "cpu"


def _bounded_read(path: Path, maximum: int) -> bytes:
    size = path.stat().st_size
    if not 1 <= size <= maximum:
        raise ValueError("Sona benchmark artifact file size is invalid")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError("Sona benchmark artifact file changed while reading")
    return payload


def _read_object(path: Path) -> dict[str, object]:
    payload = _bounded_read(path, SONA_MAX_BENCHMARK_SIDECAR_BYTES)
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Sona benchmark artifact sidecar is invalid") from error
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise ValueError("Sona benchmark artifact sidecar must be an object")
    return cast(dict[str, object], parsed)


def _expect_object(value: object, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Sona benchmark {field} must be an object")
    return cast(dict[str, JsonValue], value)


def _expect_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Sona benchmark {field} must be a string")
    return value


__all__ = ("SonaBenchmarkResult", "benchmark_sona_checkpoint")
