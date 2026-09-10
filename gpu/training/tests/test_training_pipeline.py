"""End-to-end immutable dataset, checkpoint, export, and smoke evidence."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
import pytest
import rfc8785
from autplay.domain.recommendations import JsonValue
from autplay_sona_training.benchmark import benchmark_sona_checkpoint
from autplay_sona_training.dataset import (
    SONA_MAX_DATASET_MANIFEST_BYTES,
    load_sona_dataset,
    materialize_sona_dataset,
)
from autplay_sona_training.export import SonaOnnxProvenance, export_sona_onnx
from autplay_sona_training.fixture import materialize_synthetic_fixture_bundle
from autplay_sona_training.model import SonaLiteConfig
from autplay_sona_training.tokenizer import load_sona_tokenizer
from autplay_sona_training.trainer import (
    SonaTrainingConfig,
    load_sona_checkpoint,
    train_sona_checkpoint,
)

LEGACY_V2_DATASET = (
    Path(__file__).resolve().parents[1]
    / "evidence"
    / "r1b-checkpoint4-20260904-02"
    / "fixture"
    / "dataset"
)


def test_synthetic_bundle_is_deterministic_owner_safe_and_not_quality_eligible(
    tmp_path: Path,
) -> None:
    first = materialize_synthetic_fixture_bundle(tmp_path / "first")
    second = materialize_synthetic_fixture_bundle(tmp_path / "second")
    first_dataset = load_sona_dataset(tmp_path / "first" / "dataset")
    second_dataset = load_sona_dataset(tmp_path / "second" / "dataset")
    tokenizer = load_sona_tokenizer(tmp_path / "first" / "tokenizer")

    assert first == second
    assert first_dataset.manifest_sha256 == second_dataset.manifest_sha256
    assert first_dataset.example_count == 8
    assert first_dataset.data_classification == "SYNTHETIC_FIXTURE"
    assert first_dataset.quality_eligible is False
    assert len(first_dataset.owner_lineage_tokens) == 1
    assert first_dataset.tokenizer_sha256 == tokenizer.manifest_sha256
    assert first_dataset.tokenizer_active_codes_per_level == tokenizer.centroids.shape[1]
    assert np.all(first_dataset.target_sids > 0)
    manifest_bytes = (tmp_path / "first" / "dataset" / "manifest.json").read_bytes()
    assert b"owner_user_id" not in manifest_bytes
    assert b'"recording_id":' not in manifest_bytes
    with pytest.raises(ValueError, match="approval is not implemented"):
        materialize_sona_dataset(
            (),
            tmp_path / "forbidden-quality-dataset",
            split="train",
            data_classification="APPROVED_OWNER_SAFE",
            quality_eligible=True,
            owner_lineage_hmac_key=b"x" * 32,
            tokenizer_active_codes_per_level=1,
        )


def test_saved_v2_synthetic_evidence_remains_loadable_but_never_quality_eligible() -> None:
    dataset = load_sona_dataset(LEGACY_V2_DATASET)

    assert dataset.source_kind == "SYNTHETIC_FIXTURE_V1"
    assert dataset.quality_eligible is False
    assert dataset.quality_approval_sha256 is None
    assert np.array_equal(dataset.request_sha256, dataset.example_sha256)


def test_cpu_training_checkpoint_is_reproducible_pickle_free_and_provenance_bound(
    tmp_path: Path,
) -> None:
    fixture = materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    configuration = SonaTrainingConfig(
        epochs=2,
        batch_size=4,
        learning_rate=1e-3,
        seed=23,
        device="cpu",
    )
    model_configuration = SonaLiteConfig(
        codebook_size=17,
        model_dimensions=16,
        encoder_layers=1,
    )
    first = train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        tmp_path / "checkpoint-first",
        model_config=model_configuration,
        training_config=configuration,
    )
    second = train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        tmp_path / "checkpoint-second",
        model_config=model_configuration,
        training_config=configuration,
    )

    assert first == second
    assert first.dataset_manifest_sha256 == fixture.dataset_manifest_sha256
    assert first.optimizer_steps == 4
    assert first.quality_eligible is False
    assert not tuple((tmp_path / "checkpoint-first").rglob("*.pt"))
    model, loaded = load_sona_checkpoint(tmp_path / "checkpoint-first")
    assert loaded == first
    exported = export_sona_onnx(
        model,
        tmp_path / "sona-lite.onnx",
        provenance=SonaOnnxProvenance(
            checkpoint_manifest_sha256=first.checkpoint_manifest_sha256,
            checkpoint_weights_sha256=first.weights_sha256,
            dataset_manifest_sha256=first.dataset_manifest_sha256,
            tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            dataset_approval_sha256=first.dataset_approval_sha256,
            dataset_bundle_sha256=first.dataset_bundle_sha256,
            quality_provenance_eligible=first.quality_provenance_eligible,
            quality_eligible=first.quality_eligible,
        ),
    )
    assert exported.checkpoint_manifest_sha256 == first.checkpoint_manifest_sha256
    assert exported.quality_eligible is False
    assert exported.commit_sha256 is not None
    assert (tmp_path / "sona-lite.onnx.manifest.json").is_file()
    assert (tmp_path / "sona-lite.onnx.commit.json").is_file()
    with pytest.raises(ValueError, match="requires evaluation approval"):
        SonaOnnxProvenance(
            checkpoint_manifest_sha256=first.checkpoint_manifest_sha256,
            checkpoint_weights_sha256=first.weights_sha256,
            dataset_manifest_sha256=first.dataset_manifest_sha256,
            tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            dataset_approval_sha256=None,
            dataset_bundle_sha256=None,
            quality_provenance_eligible=False,
            quality_eligible=True,
        )


def test_checkpoint_hash_verification_and_cpu_benchmark_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = materialize_synthetic_fixture_bundle(tmp_path / "fixture", recording_count=1)
    trained = train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        tmp_path / "checkpoint",
        model_config=SonaLiteConfig(
            codebook_size=2,
            model_dimensions=16,
            encoder_layers=1,
        ),
        training_config=SonaTrainingConfig(batch_size=8, seed=29, device="cpu"),
    )
    model, _ = load_sona_checkpoint(tmp_path / "checkpoint")
    artifact = tmp_path / "sona-lite.onnx"
    export_sona_onnx(
        model,
        artifact,
        provenance=SonaOnnxProvenance(
            checkpoint_manifest_sha256=trained.checkpoint_manifest_sha256,
            checkpoint_weights_sha256=trained.weights_sha256,
            dataset_manifest_sha256=trained.dataset_manifest_sha256,
            tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            dataset_approval_sha256=None,
            dataset_bundle_sha256=None,
            quality_provenance_eligible=False,
            quality_eligible=False,
        ),
    )
    real_session_factory = ort.InferenceSession

    def create_session(artifact_snapshot: object, *, providers: list[str]) -> ort.InferenceSession:
        assert isinstance(artifact_snapshot, bytes)
        artifact.write_bytes(b"replaced-after-verification")
        return real_session_factory(artifact_snapshot, providers=providers)

    monkeypatch.setattr("autplay_sona_training.benchmark.ort.InferenceSession", create_session)
    result = benchmark_sona_checkpoint(
        tmp_path / "checkpoint",
        artifact,
        tmp_path / "fixture" / "dataset",
        tmp_path / "fixture" / "tokenizer",
        tmp_path / "benchmark.json",
        device_preference="cpu",
        warmup_iterations=1,
        measured_iterations=2,
    )

    assert result.device_type == "cpu"
    assert result.generated_nonzero is True
    assert result.generated_within_tokenizer is True
    assert result.generated_expandable is True
    assert result.quality_provenance_eligible is False
    assert result.quality_eligible is False
    assert result.p95_latency_ms > 0.0
    benchmark_envelope = cast(
        dict[str, JsonValue], json.loads((tmp_path / "benchmark.json").read_bytes())
    )
    benchmark_evidence = cast(dict[str, JsonValue], benchmark_envelope["evidence"])
    raw_samples = cast(list[dict[str, JsonValue]], benchmark_evidence["raw_samples"])
    benchmark_dataset = load_sona_dataset(tmp_path / "fixture" / "dataset")
    expected_requests = {bytes(value).decode("ascii") for value in benchmark_dataset.request_sha256}
    assert benchmark_envelope["kind"] == "SONA_ORT_BENCHMARK_EVIDENCE_ENVELOPE_V1"
    assert benchmark_evidence["request_count"] == benchmark_dataset.example_count
    assert len(raw_samples) == benchmark_dataset.example_count * 2
    assert {cast(str, value["request_sha256"]) for value in raw_samples} == expected_requests
    assert {
        (cast(str, value["request_sha256"]), cast(int, value["iteration"])) for value in raw_samples
    } == {(request, iteration) for request in expected_requests for iteration in range(2)}

    weight_path = next((tmp_path / "checkpoint" / "weights").glob("*.npy"))
    payload = bytearray(weight_path.read_bytes())
    payload[-1] ^= 1
    weight_path.write_bytes(bytes(payload))
    with pytest.raises(ValueError, match="weight hash mismatch"):
        load_sona_checkpoint(tmp_path / "checkpoint")


def test_training_rejects_model_vocabulary_that_escapes_tokenizer_coverage(
    tmp_path: Path,
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    with pytest.raises(ValueError, match="bound tokenizer coverage"):
        train_sona_checkpoint(
            tmp_path / "fixture" / "dataset",
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(
                codebook_size=32,
                model_dimensions=16,
                encoder_layers=1,
            ),
            training_config=SonaTrainingConfig(device="cpu"),
        )


def test_model_rejects_noncanonical_or_unbounded_categorical_vocabularies() -> None:
    with pytest.raises(ValueError, match="categorical vocabulary"):
        SonaLiteConfig(action_vocabulary_size=2)
    with pytest.raises(ValueError, match="categorical vocabulary"):
        SonaLiteConfig(seed_vocabulary_size=4_097)


def test_benchmark_rejects_in_range_sid_absent_from_tokenizer_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    trained = train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        tmp_path / "checkpoint",
        model_config=SonaLiteConfig(
            codebook_size=17,
            model_dimensions=16,
            encoder_layers=1,
        ),
        training_config=SonaTrainingConfig(device="cpu"),
    )
    model, _ = load_sona_checkpoint(tmp_path / "checkpoint")
    artifact = tmp_path / "sona-lite.onnx"
    export_sona_onnx(
        model,
        artifact,
        provenance=SonaOnnxProvenance(
            checkpoint_manifest_sha256=trained.checkpoint_manifest_sha256,
            checkpoint_weights_sha256=trained.weights_sha256,
            dataset_manifest_sha256=trained.dataset_manifest_sha256,
            tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            dataset_approval_sha256=None,
            dataset_bundle_sha256=None,
            quality_provenance_eligible=False,
            quality_eligible=False,
        ),
    )
    tokenizer = load_sona_tokenizer(tmp_path / "fixture" / "tokenizer")
    assert (7, 7, 7) not in {semantic_id.values for semantic_id in tokenizer.semantic_ids}

    class InRangeUnexpandableSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self, output_names: object, inputs: object) -> list[np.ndarray]:
            return [np.asarray([[[7, 7, 7]]], dtype=np.int64)]

        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(
        "autplay_sona_training.benchmark.ort.InferenceSession",
        InRangeUnexpandableSession,
    )
    with pytest.raises(RuntimeError, match="outside tokenizer coverage"):
        benchmark_sona_checkpoint(
            tmp_path / "checkpoint",
            artifact,
            tmp_path / "fixture" / "dataset",
            tmp_path / "fixture" / "tokenizer",
            tmp_path / "benchmark.json",
            device_preference="cpu",
            warmup_iterations=0,
            measured_iterations=1,
        )


def test_checkpoint_loader_rejects_provenance_mismatch_and_oversized_weight(
    tmp_path: Path,
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    checkpoint_directory = tmp_path / "checkpoint"
    train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        checkpoint_directory,
        model_config=SonaLiteConfig(
            codebook_size=17,
            model_dimensions=16,
            encoder_layers=1,
        ),
        training_config=SonaTrainingConfig(device="cpu"),
    )
    envelope = _load_manifest(checkpoint_directory)
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["format"] = "UNSUPPORTED"
    _write_manifest(checkpoint_directory, envelope, manifest)
    with pytest.raises(ValueError, match="format is unsupported"):
        load_sona_checkpoint(checkpoint_directory)

    manifest["format"] = "PICKLE_FREE_NUMPY_STATE_V1"
    manifest["training_device_type"] = "cuda"
    _write_manifest(checkpoint_directory, envelope, manifest)
    with pytest.raises(ValueError, match="training devices disagree"):
        load_sona_checkpoint(checkpoint_directory)

    manifest["training_device_type"] = "cpu"
    entries = cast(list[JsonValue], manifest["weights"])
    first_entry = cast(dict[str, JsonValue], entries[0])
    weight_path = checkpoint_directory / "weights" / cast(str, first_entry["file"])
    weight_path.write_bytes(weight_path.read_bytes() + b"x" * 4_097)
    first_entry["sha256"] = sha256(weight_path.read_bytes()).hexdigest()
    _write_manifest(checkpoint_directory, envelope, manifest)
    with pytest.raises(ValueError, match="payload size"):
        load_sona_checkpoint(checkpoint_directory)


def test_dataset_loader_rejects_self_consistent_invalid_semantics_and_quality_flag(
    tmp_path: Path,
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    dataset_directory = tmp_path / "fixture" / "dataset"
    history_actions_path = dataset_directory / "history_actions.npy"
    history_actions = np.load(history_actions_path, allow_pickle=False)
    history_actions[0, -1] = 99
    with history_actions_path.open("wb") as stream:
        np.save(stream, history_actions, allow_pickle=False)
    _rehash_dataset_manifest(dataset_directory, "history_actions")
    with pytest.raises(ValueError, match="categorical input"):
        load_sona_dataset(dataset_directory)

    materialize_synthetic_fixture_bundle(tmp_path / "quality-fixture")
    quality_directory = tmp_path / "quality-fixture" / "dataset"
    envelope = _load_manifest(quality_directory)
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["quality_eligible"] = True
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    (quality_directory / "manifest.json").write_bytes(rfc8785.dumps(envelope))
    with pytest.raises(ValueError, match="approval is not implemented"):
        load_sona_dataset(quality_directory)

    materialize_synthetic_fixture_bundle(tmp_path / "oversized-manifest-fixture")
    oversized_directory = tmp_path / "oversized-manifest-fixture" / "dataset"
    (oversized_directory / "manifest.json").write_bytes(
        b"x" * (SONA_MAX_DATASET_MANIFEST_BYTES + 1)
    )
    with pytest.raises(ValueError, match="manifest exceeds"):
        load_sona_dataset(oversized_directory)


def _rehash_dataset_manifest(directory: Path, tensor_name: str) -> None:
    envelope = _load_manifest(directory)
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    entries = cast(list[JsonValue], manifest["tensors"])
    for raw_entry in entries:
        entry = cast(dict[str, JsonValue], raw_entry)
        if entry["name"] == tensor_name:
            entry["sha256"] = sha256((directory / f"{tensor_name}.npy").read_bytes()).hexdigest()
            break
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    (directory / "manifest.json").write_bytes(rfc8785.dumps(envelope))


def _load_manifest(directory: Path) -> dict[str, JsonValue]:
    parsed = cast(JsonValue, json.loads((directory / "manifest.json").read_bytes()))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("manifest"), dict):
        raise AssertionError("test fixture manifest is invalid")
    return parsed


def _write_manifest(
    directory: Path,
    envelope: dict[str, JsonValue],
    manifest: dict[str, JsonValue],
) -> None:
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    (directory / "manifest.json").write_bytes(rfc8785.dumps(envelope))
