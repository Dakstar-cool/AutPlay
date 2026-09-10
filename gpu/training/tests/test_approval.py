"""Atomic signed quality-bundle ancestry and anti-leakage tests."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
import pytest
import rfc8785
from autplay.application.recommendations import baseline_pipeline_definition
from autplay.application.sona import sona_inference_request_document
from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    build_sona_source_provenance_acceptance,
    materialize_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_planning import SONA_SOURCE_REKEY_PLAN_KIND
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki, sign_p1363
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona_approval import (
    SONA_DATASET_APPROVAL_DOMAIN,
    SONA_LABEL_DELAY_EMBARGO_MS,
    SONA_SOURCE_APPROVAL_DOMAIN,
    compute_sona_dataset_bundle_sha256,
    verify_sona_source_approval,
)
from autplay.domain.sona_training import SonaTrainingExample
from autplay_sona_training.benchmark import benchmark_sona_checkpoint
from autplay_sona_training.dataset import (
    SonaTensorDataset,
    load_sona_dataset,
    materialize_quality_candidate_sona_dataset,
    materialize_sona_dataset,
)
from autplay_sona_training.export import SonaOnnxProvenance, export_sona_onnx
from autplay_sona_training.fixture import (
    _synthetic_training_examples,
    materialize_synthetic_fixture_bundle,
)
from autplay_sona_training.model import SonaLiteConfig
from autplay_sona_training.quality_bundle import (
    SONA_CATALOG_MANIFEST_KIND,
    SONA_SOURCE_MANIFEST_KIND,
    compute_sona_leakage_audit_sha256,
    compute_sona_owner_lineage_manifest_sha256,
    load_quality_approved_sona_dataset_bundle,
)
from autplay_sona_training.quality_trust import (
    SONA_REVIEWER_SPKI_PATH_ENV,
    SONA_REVIEWER_THUMBPRINT_ENV,
    load_deployment_sona_reviewer_trust_anchor,
)
from autplay_sona_training.teacher_calibration import (
    SonaTeacherCalibrationCandidate,
    SonaTeacherCalibrationExample,
    build_sona_teacher_calibration_set,
    build_sona_teacher_manifest,
    calibrated_sona_teacher_probabilities,
    fit_sona_teacher_temperatures,
    materialize_sona_teacher_calibration_set,
)
from autplay_sona_training.tokenizer import load_sona_tokenizer
from autplay_sona_training.trainer import (
    SonaTrainingConfig,
    load_quality_sona_checkpoint,
    load_sona_checkpoint,
    train_quality_sona_checkpoint,
)
from cryptography.hazmat.primitives.asymmetric import ec

DAY_MS = 24 * 60 * 60 * 1_000
CUTOFF_BASE_MS = 1_788_400_000_000
LINEAGE_KEY_ID = "r1b-test-lineage"
LINEAGE_HMAC_KEY = b"x" * 32
REVIEWER_KEY = ec.generate_private_key(ec.SECP256R1())
REVIEWER_SPKI = public_spki(REVIEWER_KEY)


@pytest.fixture(autouse=True)
def _deployment_reviewer_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = (tmp_path / "quality-reviewer.spki").resolve()
    path.write_bytes(REVIEWER_SPKI)
    monkeypatch.setenv(SONA_REVIEWER_SPKI_PATH_ENV, str(path))
    monkeypatch.setenv(
        SONA_REVIEWER_THUMBPRINT_ENV,
        public_key_thumbprint(REVIEWER_SPKI).hex(),
    )


class BundleArguments(TypedDict):
    train_directory: Path
    validation_directory: Path
    test_directory: Path
    tokenizer_directory: Path
    source_manifest_path: Path
    catalog_manifest_path: Path
    source_rekey_plan_path: Path
    source_provenance_acceptance_path: Path
    teacher_calibration_path: Path
    teacher_manifest_path: Path
    source_approval_path: Path
    dataset_approval_path: Path
    at_ms: int


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _synthetic_p11_raw_score(request_sha256: str, recording_hex: str) -> float:
    """Produce bounded fixture-only logits without implying real P11 execution."""

    prefix = _digest(f"synthetic-p11-score:{request_sha256}:{recording_hex}")[:8]
    return (int(prefix, 16) / 0xFFFFFFFF) * 4.0 - 2.0


def _set_digest(values: frozenset[str]) -> str:
    return hashlib.sha256(rfc8785.dumps(sorted(values))).hexdigest()


def _signed(
    payload: Mapping[str, JsonValue],
    key: ec.EllipticCurvePrivateKey,
    domain: str,
) -> dict[str, JsonValue]:
    digest = hashlib.sha256(rfc8785.dumps(dict(payload))).digest()
    return {
        "payload": dict(payload),
        "payload_sha256": digest.hex(),
        "signature_algorithm": "ES256-P1363",
        "signature_b64url": sign_p1363(key, domain, digest),
    }


def _write_document(path: Path, document: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rfc8785.dumps(dict(document)))


def _write_manifest(path: Path, manifest: dict[str, JsonValue]) -> str:
    manifest_sha256 = hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()
    _write_document(path, {"manifest": manifest, "manifest_sha256": manifest_sha256})
    return manifest_sha256


def _source_payload(
    key: ec.EllipticCurvePrivateKey,
    *,
    source_manifest_sha256: str,
    embedding_snapshot_sha256: str,
    catalog_snapshot_sha256: str,
    request_set_sha256: str,
    recording_set_sha256: str,
    recording_count: int = 16,
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "approval_kind": "SONA_SOURCE_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "source_manifest_sha256": source_manifest_sha256,
        "embedding_snapshot_sha256": embedding_snapshot_sha256,
        "catalog_snapshot_sha256": catalog_snapshot_sha256,
        "owner_lineage_key_id": LINEAGE_KEY_ID,
        "owner_count": 1,
        "recording_count": recording_count,
        "request_count": 24,
        "request_set_sha256": request_set_sha256,
        "recording_set_sha256": recording_set_sha256,
        "approved_at_ms": 100,
        "not_after_ms": 1_000,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }


def _dataset_payload(
    key: ec.EllipticCurvePrivateKey,
    *,
    source_approval_sha256: str,
    split_manifest_sha256: tuple[str, str, str],
    test_request_sha256s: frozenset[str],
    tokenizer_manifest_sha256: str,
    tokenizer_source_snapshot_sha256: str,
    teacher_manifest_sha256: str,
    source_model_manifest_sha256: str,
    leakage_audit_sha256: str,
    owner_lineage_manifest_sha256: str,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "approval_kind": "SONA_DATASET_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_bundle_sha256": "0" * 64,
        "train_manifest_sha256": split_manifest_sha256[0],
        "validation_manifest_sha256": split_manifest_sha256[1],
        "test_manifest_sha256": split_manifest_sha256[2],
        "test_request_count": len(test_request_sha256s),
        "test_request_set_sha256": _set_digest(test_request_sha256s),
        "source_approval_sha256": source_approval_sha256,
        "tokenizer_manifest_sha256": tokenizer_manifest_sha256,
        "tokenizer_source_snapshot_sha256": tokenizer_source_snapshot_sha256,
        "teacher_manifest_sha256": teacher_manifest_sha256,
        "source_model_manifest_sha256": source_model_manifest_sha256,
        "split_policy": "OWNER_TIME_ORDERED_WITH_7D_LABEL_EMBARGO_V1",
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "train_cutoff_start_ms": CUTOFF_BASE_MS - DAY_MS,
        "train_cutoff_end_ms": CUTOFF_BASE_MS + DAY_MS,
        "validation_cutoff_start_ms": CUTOFF_BASE_MS + 8 * DAY_MS,
        "validation_cutoff_end_ms": CUTOFF_BASE_MS + 10 * DAY_MS,
        "test_cutoff_start_ms": CUTOFF_BASE_MS + 17 * DAY_MS,
        "test_cutoff_end_ms": CUTOFF_BASE_MS + 19 * DAY_MS,
        "request_hash_overlap_count": 0,
        "example_hash_overlap_count": 0,
        "leakage_audit_sha256": leakage_audit_sha256,
        "owner_lineage_manifest_sha256": owner_lineage_manifest_sha256,
        "approved_at_ms": 200,
        "not_after_ms": 900,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    payload["dataset_bundle_sha256"] = compute_sona_dataset_bundle_sha256(payload)
    return payload


def _shift_examples(
    examples: tuple[SonaTrainingExample, ...], *, split: str, shift_ms: int
) -> tuple[SonaTrainingExample, ...]:
    shifted = []
    for index, example in enumerate(examples):
        request = replace(
            example.request,
            cutoff_at_ms=example.request.cutoff_at_ms + shift_ms,
            request_sha256="0" * 64,
        )
        request = replace(
            request,
            request_sha256=hashlib.sha256(
                rfc8785.dumps(sona_inference_request_document(request))
            ).hexdigest(),
        )
        shifted.append(
            replace(
                example,
                request=request,
                observed_at_ms=example.observed_at_ms + shift_ms,
                teacher_key="p11-calibrated",
                teacher_version="2",
                teacher_manifest_sha256="0" * 64,
                example_sha256=_digest(f"approved-{split}-example-{index}"),
            )
        )
    return tuple(shifted)


def _build_bundle(
    root: Path,
    *,
    synthetic_materializer: bool = False,
    recording_count_delta: int = 0,
) -> BundleArguments:
    fixture_root = root / "fixture-source"
    materialize_synthetic_fixture_bundle(fixture_root)
    tokenizer = load_sona_tokenizer(fixture_root / "tokenizer")
    examples = _synthetic_training_examples(tokenizer.mapping(), tokenizer.manifest_sha256)
    source_model_manifest_sha256 = examples[0].request.model_manifest_sha256
    raw_split_examples = tuple(
        _shift_examples(examples, split=split, shift_ms=shift_ms)
        for split, shift_ms in (
            ("train", 0),
            ("validation", 9 * DAY_MS),
            ("test", 18 * DAY_MS),
        )
    )
    calibration = build_sona_teacher_calibration_set(
        tuple(
            SonaTeacherCalibrationExample(
                p11_request_sha256=_digest(
                    f"synthetic-p11-request:{example.request.request_sha256}"
                ),
                sona_request_sha256=example.request.request_sha256,
                candidates=tuple(
                    SonaTeacherCalibrationCandidate(
                        recording_id=target.recording_id,
                        raw_p11_score=_synthetic_p11_raw_score(
                            example.request.request_sha256, target.recording_id.hex
                        ),
                        labels=target.labels,
                        label_mask=target.label_mask,
                    )
                    for target in example.ranking_targets
                ),
            )
            for example in raw_split_examples[1]
        ),
        source_model_manifest_sha256=source_model_manifest_sha256,
        p11_pipeline_manifest_sha256=baseline_pipeline_definition().manifest_sha256,
    )
    fit = fit_sona_teacher_temperatures(calibration)
    teacher_manifest = build_sona_teacher_manifest(
        calibration,
        teacher_key="p11-calibrated",
        teacher_version="2",
    )
    teacher_calibration_path = root / "teacher-calibration.json"
    materialize_sona_teacher_calibration_set(calibration, teacher_calibration_path)
    teacher_manifest_path = root / "teacher-manifest.json"
    teacher_manifest_sha256 = _write_manifest(teacher_manifest_path, teacher_manifest)
    split_examples = tuple(
        tuple(
            replace(
                example,
                teacher_manifest_sha256=teacher_manifest_sha256,
                ranking_targets=tuple(
                    replace(
                        target,
                        teacher_probabilities=calibrated_sona_teacher_probabilities(
                            _synthetic_p11_raw_score(
                                example.request.request_sha256, target.recording_id.hex
                            ),
                            fit,
                        ),
                    )
                    for target in example.ranking_targets
                ),
            )
            for example in values
        )
        for values in raw_split_examples
    )
    approved_request_sha256s = frozenset(
        example.request.request_sha256 for values in split_examples for example in values
    )
    recording_ids = frozenset(str(value) for value in tokenizer.recording_ids)
    recording_set_sha256 = _set_digest(recording_ids)
    catalog_manifest: dict[str, JsonValue] = {
        "schema_version": 1,
        "manifest_kind": SONA_CATALOG_MANIFEST_KIND,
        "recording_count": len(recording_ids) + recording_count_delta,
        "recording_set_sha256": recording_set_sha256,
        "contains_raw_owner_ids": False,
    }
    catalog_manifest_path = root / "catalog-manifest.json"
    catalog_manifest_sha256 = _write_manifest(catalog_manifest_path, catalog_manifest)
    provenance_acceptance = build_sona_source_provenance_acceptance(
        generation_id="quality-bundle-test-generation",
        encrypted_archive_sha256=_digest("quality-bundle-test-archive"),
        recorded_at_ms=50,
    )
    source_provenance_acceptance_path = root / "source-provenance-acceptance.json"
    materialize_sona_source_provenance_acceptance(
        provenance_acceptance,
        source_provenance_acceptance_path,
    )
    source_rekey_plan: dict[str, JsonValue] = {
        "schema_version": 1,
        "plan_kind": SONA_SOURCE_REKEY_PLAN_KIND,
        "quality_eligible": False,
        "selected_request_count": len(approved_request_sha256s),
        "owner_lineage_key_id": LINEAGE_KEY_ID,
        "owner_count": 1,
        "request_set_sha256": _set_digest(approved_request_sha256s),
        "request_hash_overlap_count": 0,
        "owner_time_ordering_violation_count": 0,
        "label_boundary_violation_count": 0,
    }
    source_rekey_plan_path = root / "source-rekey-plan.json"
    source_rekey_plan_sha256 = _write_manifest(
        source_rekey_plan_path,
        source_rekey_plan,
    )
    source_manifest: dict[str, JsonValue] = {
        "schema_version": 2,
        "manifest_kind": SONA_SOURCE_MANIFEST_KIND,
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "embedding_snapshot_sha256": tokenizer.source_embeddings_sha256,
        "catalog_snapshot_sha256": catalog_manifest_sha256,
        "owner_lineage_key_id": LINEAGE_KEY_ID,
        "owner_count": 1,
        "recording_count": len(recording_ids) + recording_count_delta,
        "request_count": len(approved_request_sha256s),
        "request_set_sha256": _set_digest(approved_request_sha256s),
        "recording_set_sha256": recording_set_sha256,
        "rekey_plan_sha256": source_rekey_plan_sha256,
        "temporal_provenance_kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
        "provenance_acceptance_sha256": provenance_acceptance.acceptance_sha256,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    }
    source_manifest_path = root / "source-manifest.json"
    source_manifest_sha256 = _write_manifest(source_manifest_path, source_manifest)
    key = REVIEWER_KEY
    spki = public_spki(key)
    source_document = _signed(
        _source_payload(
            key,
            source_manifest_sha256=source_manifest_sha256,
            embedding_snapshot_sha256=tokenizer.source_embeddings_sha256,
            catalog_snapshot_sha256=catalog_manifest_sha256,
            request_set_sha256=_set_digest(approved_request_sha256s),
            recording_set_sha256=recording_set_sha256,
            recording_count=len(recording_ids) + recording_count_delta,
        ),
        key,
        SONA_SOURCE_APPROVAL_DOMAIN,
    )
    source = verify_sona_source_approval(
        source_document,
        trusted_reviewer_spki=spki,
        at_ms=250,
    )
    split_directories = tuple(root / split for split in ("train", "validation", "test"))
    split_hashes = []
    for split, directory, approved_examples in zip(
        ("train", "validation", "test"), split_directories, split_examples, strict=True
    ):
        if synthetic_materializer:
            manifest_sha256 = materialize_sona_dataset(
                approved_examples,
                directory,
                split=split,
                data_classification="APPROVED_OWNER_SAFE",
                quality_eligible=False,
                owner_lineage_hmac_key=LINEAGE_HMAC_KEY,
                tokenizer_active_codes_per_level=tokenizer.centroids.shape[1],
            )
        else:
            manifest_sha256 = materialize_quality_candidate_sona_dataset(
                approved_examples,
                directory,
                split=split,
                source_approval=source,
                approved_request_sha256s=approved_request_sha256s,
                owner_lineage_hmac_key=LINEAGE_HMAC_KEY,
                tokenizer_active_codes_per_level=tokenizer.centroids.shape[1],
            )
        split_hashes.append(manifest_sha256)
    datasets = cast(
        tuple[SonaTensorDataset, SonaTensorDataset, SonaTensorDataset],
        tuple(load_sona_dataset(directory) for directory in split_directories),
    )
    dataset_document = _signed(
        _dataset_payload(
            key,
            source_approval_sha256=source.approval_sha256,
            split_manifest_sha256=cast(tuple[str, str, str], tuple(split_hashes)),
            test_request_sha256s=frozenset(
                example.request.request_sha256 for example in split_examples[2]
            ),
            tokenizer_manifest_sha256=tokenizer.manifest_sha256,
            tokenizer_source_snapshot_sha256=tokenizer.source_embeddings_sha256,
            teacher_manifest_sha256=teacher_manifest_sha256,
            source_model_manifest_sha256=source_model_manifest_sha256,
            leakage_audit_sha256=compute_sona_leakage_audit_sha256(datasets),
            owner_lineage_manifest_sha256=compute_sona_owner_lineage_manifest_sha256(datasets),
        ),
        key,
        SONA_DATASET_APPROVAL_DOMAIN,
    )
    source_approval_path = root / "source-approval.json"
    dataset_approval_path = root / "dataset-approval.json"
    _write_document(source_approval_path, source_document)
    _write_document(dataset_approval_path, dataset_document)
    return {
        "train_directory": split_directories[0],
        "validation_directory": split_directories[1],
        "test_directory": split_directories[2],
        "tokenizer_directory": fixture_root / "tokenizer",
        "source_manifest_path": source_manifest_path,
        "catalog_manifest_path": catalog_manifest_path,
        "source_rekey_plan_path": source_rekey_plan_path,
        "source_provenance_acceptance_path": source_provenance_acceptance_path,
        "teacher_calibration_path": teacher_calibration_path,
        "teacher_manifest_path": teacher_manifest_path,
        "source_approval_path": source_approval_path,
        "dataset_approval_path": dataset_approval_path,
        "at_ms": 250,
    }


def test_atomic_bundle_derives_quality_only_after_all_artifacts_verify(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "approved")

    assert (
        "reviewer_trust"
        not in inspect.signature(load_quality_approved_sona_dataset_bundle).parameters
    )
    bundle = load_quality_approved_sona_dataset_bundle(**arguments)

    assert bundle.train.quality_eligible is True
    assert bundle.validation.quality_eligible is True
    assert bundle.test.quality_eligible is True
    assert bundle.train.quality_approval_sha256 == bundle.dataset_approval.approval_sha256


def test_verified_bundle_provenance_flows_through_candidate_checkpoint_and_onnx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _build_bundle(tmp_path / "approved")
    bundle = load_quality_approved_sona_dataset_bundle(**arguments)
    monkeypatch.setattr("autplay_sona_training.trainer.time_ns", lambda: 250_000_000)

    result = train_quality_sona_checkpoint(
        bundle,
        tmp_path / "checkpoint",
        model_config=SonaLiteConfig(
            codebook_size=17,
            model_dimensions=16,
            encoder_layers=1,
        ),
        training_config=SonaTrainingConfig(batch_size=8, seed=31, device="cpu"),
    )

    assert result.dataset_approval_sha256 == bundle.dataset_approval.approval_sha256
    assert result.dataset_bundle_sha256 == bundle.dataset_approval.dataset_bundle_sha256
    assert result.source_approval_sha256 == bundle.source_approval.approval_sha256
    assert result.quality_provenance_eligible is True
    assert result.quality_eligible is False
    with pytest.raises(ValueError, match="requires signed bundle re-verification"):
        load_sona_checkpoint(tmp_path / "checkpoint")
    model, loaded = load_quality_sona_checkpoint(tmp_path / "checkpoint", bundle)
    assert loaded == result

    next(model.parameters()).data.add_(1.0)
    with pytest.raises(ValueError, match="weights do not match"):
        export_sona_onnx(
            model,
            tmp_path / "mutated.onnx",
            provenance=SonaOnnxProvenance(
                checkpoint_manifest_sha256=result.checkpoint_manifest_sha256,
                checkpoint_weights_sha256=result.weights_sha256,
                dataset_manifest_sha256=result.dataset_manifest_sha256,
                tokenizer_sha256=result.tokenizer_sha256,
                dataset_approval_sha256=result.dataset_approval_sha256,
                dataset_bundle_sha256=result.dataset_bundle_sha256,
                quality_provenance_eligible=result.quality_provenance_eligible,
                quality_eligible=result.quality_eligible,
            ),
            before_publish=lambda: load_quality_approved_sona_dataset_bundle(**arguments),
        )
    model, loaded = load_quality_sona_checkpoint(tmp_path / "checkpoint", bundle)
    assert loaded == result

    exported = export_sona_onnx(
        model,
        tmp_path / "sona-quality-candidate.onnx",
        provenance=SonaOnnxProvenance(
            checkpoint_manifest_sha256=result.checkpoint_manifest_sha256,
            checkpoint_weights_sha256=result.weights_sha256,
            dataset_manifest_sha256=result.dataset_manifest_sha256,
            tokenizer_sha256=result.tokenizer_sha256,
            dataset_approval_sha256=result.dataset_approval_sha256,
            dataset_bundle_sha256=result.dataset_bundle_sha256,
            quality_provenance_eligible=result.quality_provenance_eligible,
            quality_eligible=result.quality_eligible,
        ),
        before_publish=lambda: load_quality_approved_sona_dataset_bundle(**arguments),
    )

    assert exported.dataset_approval_sha256 == bundle.dataset_approval.approval_sha256
    assert exported.dataset_bundle_sha256 == bundle.dataset_approval.dataset_bundle_sha256
    assert exported.quality_provenance_eligible is True
    assert exported.quality_eligible is False
    with pytest.raises(ValueError, match="quality benchmark requires CUDA"):
        benchmark_sona_checkpoint(
            tmp_path / "checkpoint",
            tmp_path / "sona-quality-candidate.onnx",
            arguments["test_directory"],
            arguments["tokenizer_directory"],
            tmp_path / "quality-benchmark.json",
            device_preference="cpu",
            warmup_iterations=1,
            measured_iterations=1,
            quality_bundle=bundle,
        )

    selected_sid = load_sona_tokenizer(arguments["tokenizer_directory"]).semantic_ids[0].values
    session_state: dict[str, object] = {}

    class CudaOnlyOptions:
        enable_profiling: bool = False
        profile_file_prefix: str = ""

        def add_session_config_entry(self, key: str, value: str) -> None:
            session_state["config"] = (key, value)

    class CudaOnlySession:
        def __init__(
            self,
            artifact: bytes,
            *,
            sess_options: CudaOnlyOptions,
            providers: list[str],
        ) -> None:
            assert artifact
            assert isinstance(sess_options, CudaOnlyOptions)
            assert providers == ["CUDAExecutionProvider"]
            self.options = sess_options

        def disable_fallback(self) -> None:
            session_state["run_fallback_disabled"] = True

        def get_providers(self) -> list[str]:
            return ["CUDAExecutionProvider"]

        def run(self, output_names: object, inputs: object) -> list[np.ndarray]:
            return [np.asarray([[selected_sid]], dtype=np.int64)]

        def end_profiling(self) -> str:
            profile_path = Path(f"{self.options.profile_file_prefix}.json")
            profile_path.write_text(
                json.dumps(
                    [
                        {
                            "cat": "Node",
                            "name": "fixture_kernel_time",
                            "args": {"provider": "CUDAExecutionProvider"},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            session_state["profile_verified"] = True
            return str(profile_path)

    monkeypatch.setattr("autplay_sona_training.benchmark.ort.SessionOptions", CudaOnlyOptions)
    monkeypatch.setattr("autplay_sona_training.benchmark.ort.InferenceSession", CudaOnlySession)
    monkeypatch.setattr(
        "autplay_sona_training.benchmark.ort.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr("autplay_sona_training.benchmark.time_ns", lambda: 250_000_000)
    quality_benchmark = benchmark_sona_checkpoint(
        tmp_path / "checkpoint",
        tmp_path / "sona-quality-candidate.onnx",
        arguments["test_directory"],
        arguments["tokenizer_directory"],
        tmp_path / "quality-benchmark.json",
        device_preference="cuda",
        warmup_iterations=1,
        measured_iterations=1,
        quality_bundle=bundle,
    )
    quality_evidence = cast(
        dict[str, JsonValue],
        json.loads((tmp_path / "quality-benchmark.json").read_bytes()),
    )
    assert session_state == {
        "config": ("session.disable_cpu_ep_fallback", "1"),
        "profile_verified": True,
        "run_fallback_disabled": True,
    }
    assert quality_benchmark.cuda_only_execution is True
    assert cast(dict[str, JsonValue], quality_evidence["evidence"])["cuda_only_execution"] is True


def test_synthetic_source_kind_is_permanently_disqualifying(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "synthetic", synthetic_materializer=True)

    with pytest.raises(ValueError, match=r"source ancestry|Synthetic Sona dataset"):
        load_quality_approved_sona_dataset_bundle(**arguments)


def test_quality_checkpoint_reverifies_expiry_immediately_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _build_bundle(tmp_path / "expired-before-publish")
    bundle = load_quality_approved_sona_dataset_bundle(**arguments)
    monkeypatch.setattr("autplay_sona_training.trainer.time_ns", lambda: 901_000_000)
    checkpoint = tmp_path / "checkpoint"

    with pytest.raises(ValueError, match="not valid"):
        train_quality_sona_checkpoint(
            bundle,
            checkpoint,
            model_config=SonaLiteConfig(
                codebook_size=17,
                model_dimensions=16,
                encoder_layers=1,
            ),
            training_config=SonaTrainingConfig(batch_size=8, seed=37, device="cpu"),
        )

    assert not checkpoint.exists()


def test_tampered_validation_manifest_fails_atomic_bundle(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "tampered")
    manifest_path = arguments["validation_directory"] / "manifest.json"
    envelope = cast(dict[str, JsonValue], json.loads(manifest_path.read_bytes()))
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["example_count"] = 999
    manifest_path.write_bytes(rfc8785.dumps(envelope))

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        load_quality_approved_sona_dataset_bundle(**arguments)


def test_tampered_teacher_calibration_fails_atomic_bundle(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "tampered-teacher-calibration")
    calibration_path = arguments["teacher_calibration_path"]
    envelope = cast(dict[str, JsonValue], json.loads(calibration_path.read_bytes()))
    calibration = cast(dict[str, JsonValue], envelope["calibration"])
    examples = cast(list[JsonValue], calibration["examples"])
    first_example = cast(dict[str, JsonValue], examples[0])
    candidates = cast(list[JsonValue], first_example["candidates"])
    first_candidate = cast(dict[str, JsonValue], candidates[0])
    first_candidate["raw_p11_score"] = 99.0
    calibration_path.write_bytes(rfc8785.dumps(envelope))

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        load_quality_approved_sona_dataset_bundle(**arguments)


def test_cross_split_request_reuse_is_rejected(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "leakage")
    train = load_sona_dataset(arguments["train_directory"])
    validation = load_sona_dataset(arguments["validation_directory"])
    test = load_sona_dataset(arguments["test_directory"])
    leaked_validation = replace(validation, request_sha256=train.request_sha256.copy())

    with pytest.raises(ValueError, match="cross-split request or example leakage"):
        compute_sona_leakage_audit_sha256((train, leaked_validation, test))


def test_undeclared_dataset_sidecar_fails_atomic_bundle(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "sidecar")
    (arguments["train_directory"] / "raw-owner-debug.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="undeclared or unsafe files"):
        load_quality_approved_sona_dataset_bundle(**arguments)


def test_approved_recording_count_must_match_tokenizer_membership(tmp_path: Path) -> None:
    arguments = _build_bundle(tmp_path / "recording-count", recording_count_delta=1)

    with pytest.raises(ValueError, match="recording count does not match"):
        load_quality_approved_sona_dataset_bundle(**arguments)


def test_deployment_reviewer_trust_requires_separate_path_and_thumbprint_pin(
    tmp_path: Path,
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    path = (tmp_path / "reviewer.spki").resolve()
    path.write_bytes(spki)
    environment = {
        SONA_REVIEWER_SPKI_PATH_ENV: str(path),
        SONA_REVIEWER_THUMBPRINT_ENV: public_key_thumbprint(spki).hex(),
    }

    anchor = load_deployment_sona_reviewer_trust_anchor(environment)

    assert anchor.reviewer_spki == spki
    with pytest.raises(RuntimeError, match="trust is invalid"):
        load_deployment_sona_reviewer_trust_anchor(
            {**environment, SONA_REVIEWER_THUMBPRINT_ENV: "0" * 64}
        )
    with pytest.raises(RuntimeError, match="not configured"):
        load_deployment_sona_reviewer_trust_anchor({})
