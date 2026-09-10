"""Paired P11/Sona metric, hash-separation and exact gate tests."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
import rfc8785
from autplay.application.recommendations import (
    baseline_pipeline_definition,
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    request_document,
)
from autplay.application.sona import (
    SonaShadowResult,
    SonaShadowService,
    sona_inference_request_document,
)
from autplay.application.sona_evaluation import (
    REQUIRED_DIRECTIONAL_SCENARIOS,
    SONA_DIRECTIONAL_EXPECTED_SHA256,
    SONA_SAFETY_GATE_IDS,
    DirectionalScenarioEvidence,
    PairedEvaluationReport,
    PairedLatencySample,
    PairedRankingCase,
    PairedRankingCaseEvidence,
    SonaPerformanceEvidence,
    SonaSafetyEvidence,
    SonaSafetyGateEvidence,
    _verify_onnx_model_structure,
    build_paired_ranking_case,
    compute_sona_contract_policy_sha256,
    compute_sona_directional_scenario_bundle_sha256,
    compute_sona_evaluation_case_bundle_sha256,
    compute_sona_performance_evidence_sha256,
    compute_sona_safety_evidence_bundle_sha256,
    evaluate_paired_rankings,
    paired_evaluation_report_document,
    paired_evaluation_report_from_document,
    sona_outcome_evidence_document,
    sona_paired_execution_evidence_document,
    sona_performance_evidence_document,
    sona_safety_evidence_document,
    verify_quality_eligible_sona_artifact,
)
from autplay.application.sona_shadow import build_sona_shadow_evidence
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki, sign_p1363
from autplay.domain.recommendations import (
    CandidateContribution,
    JsonValue,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    SONA_MAX_HISTORY_EVENTS,
    SONA_RANKING_HEADS,
    SONA_SID_DEPTH,
    SonaCandidate,
    SonaGeneratedCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
    SonaTemporalSnapshot,
)
from autplay.domain.sona_approval import (
    SONA_ARTIFACT_APPROVAL_DOMAIN,
    SONA_DATASET_APPROVAL_DOMAIN,
    SONA_EVALUATION_APPROVAL_DOMAIN,
    SONA_LABEL_DELAY_EMBARGO_MS,
    SONA_SOURCE_APPROVAL_DOMAIN,
    compute_sona_dataset_bundle_sha256,
)
from cryptography.hazmat.primitives.asymmetric import ec
from onnx import TensorProto, helper

A = UUID("00000000-0000-7000-8000-000000000001")
B = UUID("00000000-0000-7000-8000-000000000002")
C = UUID("00000000-0000-7000-8000-000000000003")
D = UUID("00000000-0000-7000-8000-000000000004")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
OWNER = UUID("00000000-0000-7000-8000-000000000010")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000000011")
BASELINE_ID = UUID("00000000-0000-7000-8000-000000000012")
TEMPORAL_ID = UUID("00000000-0000-7000-8000-000000000013")
TOKENIZER = "7" * 64
TOKENIZER_MANIFEST = "8" * 64
MODEL = "9" * 64
P11_PIPELINE = baseline_pipeline_definition().manifest_sha256
CUTOFF_MS = 1_000
CREATED_AT = datetime(2026, 9, 4, tzinfo=UTC)
REVIEWER_KEY = ec.generate_private_key(ec.SECP256R1())
REVIEWER_SPKI = public_spki(REVIEWER_KEY)


@pytest.fixture(autouse=True)
def _deployment_reviewer_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "reviewer.spki"
    path.write_bytes(REVIEWER_SPKI)
    monkeypatch.setenv("AUTPLAY_SONA_QUALITY_REVIEWER_SPKI_PATH", str(path))
    monkeypatch.setenv(
        "AUTPLAY_SONA_QUALITY_REVIEWER_THUMBPRINT_SHA256",
        public_key_thumbprint(REVIEWER_SPKI).hex(),
    )


class _SemanticIds:
    tokenizer_manifest_sha256 = TOKENIZER_MANIFEST

    def load(
        self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
    ) -> Mapping[UUID, SonaSemanticId]:
        raise AssertionError("test evidence builder does not run inference")

    def expand(
        self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
    ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
        raise AssertionError("test evidence builder does not run inference")


class _Inference:
    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        raise AssertionError("test evidence builder does not run inference")


_SHADOW_SERVICE = SonaShadowService(
    _SemanticIds(),
    _Inference(),
    tokenizer_sha256=TOKENIZER,
    model_manifest_sha256=MODEL,
)
SONA_PIPELINE = _SHADOW_SERVICE.pipeline.manifest_sha256


def _snapshot() -> RecommendationInputSnapshot:
    tracks = tuple(
        SnapshotTrack(
            recording_id,
            None,
            f"artist-{index}",
            None,
            (f"genre-{index}",),
            "VAULT",
            True,
            "ACTIVE",
            "NONE",
            False,
            index - 1,
            index - 1,
            0 if index < 3 else 1,
            None,
            0,
            None,
        )
        for index, recording_id in enumerate((A, B, C, D), start=1)
    )
    input_sha256 = sha256(
        rfc8785.dumps(
            recommendation_input_snapshot_document(OWNER, interaction_watermark=0, tracks=tracks)
        )
    ).hexdigest()
    availability_sha256 = sha256(
        rfc8785.dumps(recommendation_availability_snapshot_document(tracks))
    ).hexdigest()
    policy_sha256 = sha256(
        rfc8785.dumps(recommendation_policy_snapshot_document(tracks))
    ).hexdigest()
    reference = RecommendationSnapshotRef(
        BASELINE_ID,
        input_sha256,
        0,
        0,
        availability_sha256,
        policy_sha256,
    )
    return RecommendationInputSnapshot(
        reference,
        tracks,
        CREATED_AT + timedelta(days=30),
    )


def _outcomes(
    *, signals: tuple[str, ...] = ("selection", "completion", "skip", "like", "dislike")
) -> tuple[dict[str, JsonValue], ...]:
    recordings = (A, A, C, A, D)
    source_ranks = (1, 1, 3, 1, 4)
    return tuple(
        {
            "schema_version": 1,
            "kind": "SONA_ATTRIBUTED_OUTCOME_V1",
            "recording_id": str(recording_id),
            "signal": signal,
            "source_request_sha256": _sona_request().request_sha256,
            "source_rank": source_ranks[index],
            "observed_at_ms": CUTOFF_MS + index + 1,
        }
        for index, (recording_id, signal) in enumerate(
            zip(recordings[: len(signals)], signals, strict=True)
        )
    )


def _sona_request() -> SonaInferenceRequest:
    request = SonaInferenceRequest(
        OWNER,
        TEMPORAL_ID,
        BASELINE_ID,
        CUTOFF_MS,
        0,
        TOKENIZER,
        MODEL,
        17,
        (),
        tuple(
            SonaCandidate(recording_id, SonaSemanticId(1, 1, 1)) for recording_id in (A, B, C, D)
        ),
        "0" * 64,
    )
    return replace(
        request,
        request_sha256=sha256(rfc8785.dumps(sona_inference_request_document(request))).hexdigest(),
    )


def _case(
    *,
    sona: tuple[UUID, ...] = (A, B, C, D),
    outcome_documents: tuple[Mapping[str, JsonValue], ...] | None = None,
) -> PairedRankingCaseEvidence:
    snapshot = _snapshot()
    query = RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, seed=17)
    pipeline = baseline_pipeline_definition()
    canonical_request = request_document(query, pipeline, snapshot.reference)
    trace = RecommendationRequestTrace(
        REQUEST_ID,
        query,
        pipeline,
        snapshot.reference,
        sha256(rfc8785.dumps(canonical_request)).hexdigest(),
        canonical_request,
        CREATED_AT,
    )
    baseline_items = tuple(
        RankedRecommendation(
            recording_id,
            rank,
            float(5 - rank),
            "P11_TEST",
            ("P11_TEST",),
            (CandidateContribution("p11-test", "1", rank, float(5 - rank)),),
            f"artist-{rank}",
            None,
        )
        for rank, recording_id in enumerate((A, B, C, D), start=1)
    )
    baseline = RecommendationResponse(trace, baseline_items)
    sona_request = _sona_request()
    output = SonaInferenceOutput(
        sona_request.request_sha256,
        (SonaGeneratedCandidate(SonaSemanticId(1, 1, 1), -0.1),),
        tuple(
            SonaRankedCandidate(recording_id, (score, score, score, score), score)
            for score, recording_id in zip((4.0, 3.0, 2.0, 1.0), (A, B, C, D), strict=True)
        ),
    )
    sona_items = tuple(
        RankedRecommendation(
            recording_id,
            rank,
            float(len(sona) - rank + 1),
            "SONA_TEST",
            ("SONA_TEST",),
            (CandidateContribution("sona-test", "1", rank, 1.0),),
            f"artist-{(A, B, C, D).index(recording_id) + 1}",
            None,
        )
        for rank, recording_id in enumerate(sona, start=1)
    )
    temporal = SonaTemporalSnapshot(
        OWNER,
        TEMPORAL_ID,
        "3" * 64,
        HASH_B,
        BASELINE_ID,
        snapshot.reference.input_snapshot_sha256,
        CUTOFF_MS,
        0,
        4,
        HASH_C,
        (),
        CREATED_AT + timedelta(days=30),
    )
    shadow = build_sona_shadow_evidence(
        baseline,
        temporal,
        _SHADOW_SERVICE,
        SonaShadowResult(
            sona_request,
            output,
            False,
            None,
            sona_items,
            sona,
            ((SonaSemanticId(1, 1, 1), sona),),
        ),
    )
    return PairedRankingCaseEvidence(
        case_id="owner-time-split-1",
        owner_lineage_token="f" * 64,
        baseline_response=baseline,
        baseline_snapshot=snapshot,
        sona_request=sona_request,
        shadow_evidence=shadow,
        outcome_documents=outcome_documents or _outcomes(),
    )


def _verified_cases(
    evidence: tuple[PairedRankingCaseEvidence, ...],
) -> tuple[PairedRankingCase, ...]:
    return tuple(
        build_paired_ranking_case(
            case_id=value.case_id,
            owner_lineage_token=value.owner_lineage_token,
            baseline_response=value.baseline_response,
            baseline_snapshot=value.baseline_snapshot,
            sona_request=value.sona_request,
            shadow_evidence=value.shadow_evidence,
            outcome_documents=value.outcome_documents,
        )
        for value in evidence
    )


def _performance(
    *,
    sona_ms: tuple[float, ...] = (110.0, 120.0),
    checkpoint_manifest_sha256: str = "e" * 64,
    artifact_sha256: str = "f" * 64,
    artifact_manifest_sha256: str = HASH_B,
) -> SonaPerformanceEvidence:
    samples = tuple(
        PairedLatencySample(
            "owner-time-split-1",
            _sona_request().request_sha256,
            baseline_ms,
            sona_value,
        )
        for baseline_ms, sona_value in zip((90.0, 100.0), sona_ms, strict=True)
    )
    environment_sha256 = sha256(
        rfc8785.dumps(
            {
                "device_type": "cuda",
                "device_name": "CUDAExecutionProvider",
                "onnxruntime_version": "1.26.0",
            }
        )
    ).hexdigest()
    return SonaPerformanceEvidence.from_benchmark(
        samples=samples,
        ort_benchmark_evidence_sha256="0" * 64,
        environment_sha256=environment_sha256,
        checkpoint_manifest_sha256=checkpoint_manifest_sha256,
        artifact_sha256=artifact_sha256,
        tokenizer_manifest_sha256="9" * 64,
        warmup_iterations=1,
        measured_iterations_per_case=2,
        dataset_storage_bytes=1_000,
        artifact_storage_bytes=2_000,
        dataset_storage_manifest_sha256="8" * 64,
        artifact_storage_manifest_sha256=artifact_manifest_sha256,
    )


def _ort_benchmark_evidence(
    performance: SonaPerformanceEvidence,
    *,
    dataset_approval_sha256: str,
    dataset_bundle_sha256: str,
) -> bytes:
    iterations: dict[str, int] = {}
    raw_samples: list[JsonValue] = []
    for sample in performance.samples:
        iteration = iterations.get(sample.request_sha256, 0)
        iterations[sample.request_sha256] = iteration + 1
        raw_samples.append(
            {
                "request_sha256": sample.request_sha256,
                "iteration": iteration,
                "latency_ms": sample.sona_latency_ms,
            }
        )
    latencies = tuple(sample.sona_latency_ms for sample in performance.samples)
    ordered = sorted(latencies)
    evidence: dict[str, JsonValue] = {
        "schema_version": 1,
        "benchmark": "SONA_LITE_COMMITTED_ONNX_RUNTIME_V1",
        "artifact_sha256": performance.artifact_sha256,
        "model_manifest_sha256": performance.artifact_storage_manifest_sha256,
        "commit_sha256": "c" * 64,
        "checkpoint_manifest_sha256": performance.checkpoint_manifest_sha256,
        "checkpoint_weights_sha256": "d" * 64,
        "dataset_manifest_sha256": performance.dataset_storage_manifest_sha256,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_approval_sha256": dataset_approval_sha256,
        "dataset_bundle_sha256": dataset_bundle_sha256,
        "quality_provenance_eligible": True,
        "quality_eligible": False,
        "device_type": "cuda",
        "device_name": "CUDAExecutionProvider",
        "onnxruntime_version": "1.26.0",
        "environment_sha256": performance.environment_sha256,
        "cuda_only_execution": True,
        "warmup_iterations": performance.warmup_iterations,
        "measured_iterations": performance.measured_iterations_per_case,
        "request_count": len(iterations),
        "raw_samples": raw_samples,
        "mean_latency_ms": sum(latencies) / len(latencies),
        "p50_latency_ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95_latency_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "generated_nonzero": True,
        "generated_within_tokenizer": True,
        "generated_expandable": True,
        "tokenizer_fit_manifest_sha256": performance.tokenizer_manifest_sha256,
        "tokenizer_active_codes_per_level": 1,
    }
    return rfc8785.dumps(
        {
            "schema_version": 1,
            "kind": "SONA_ORT_BENCHMARK_EVIDENCE_ENVELOPE_V1",
            "evidence": evidence,
            "evidence_sha256": sha256(rfc8785.dumps(evidence)).hexdigest(),
        }
    )


def _safety() -> SonaSafetyEvidence:
    return SonaSafetyEvidence(
        tuple(
            SonaSafetyGateEvidence.from_audit(
                gate_id,
                covered_request_sha256s=(_sona_request().request_sha256,),
            )
            for gate_id in SONA_SAFETY_GATE_IDS
        )
    )


def _outcome_evidence(evidence: tuple[PairedRankingCaseEvidence, ...]) -> bytes:
    return sona_outcome_evidence_document(
        _verified_cases(evidence),
        test_manifest_sha256="8" * 64,
        owner_lineage_manifest_sha256="d" * 64,
    )


def _scenarios(*, failed: str | None = None) -> tuple[DirectionalScenarioEvidence, ...]:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "recommendations"
        / "v1"
        / "scenario-vectors.json"
    )
    fixture = json.loads(fixture_path.read_bytes())
    documents = {item["case_id"]: item["expected"] for item in fixture["cases"]}
    return tuple(
        DirectionalScenarioEvidence.from_actual_document(
            case_id,
            (
                {**documents[case_id], "forced_failure": True}
                if case_id == failed
                else documents[case_id]
            ),
        )
        for case_id in sorted(REQUIRED_DIRECTIONAL_SCENARIOS)
    )


def _signed(
    payload: Mapping[str, object],
    key: ec.EllipticCurvePrivateKey,
    domain: str,
) -> dict[str, object]:
    canonical_payload = cast(dict[str, JsonValue], dict(payload))
    digest = sha256(rfc8785.dumps(canonical_payload)).digest()
    return {
        "payload": dict(payload),
        "payload_sha256": digest.hex(),
        "signature_algorithm": "ES256-P1363",
        "signature_b64url": sign_p1363(key, domain, digest),
    }


def _sona_test_onnx(
    *,
    first_input_type: int = TensorProto.INT64,
    generated_shape: tuple[int, ...] = (1, 1, SONA_SID_DEPTH),
) -> bytes:
    inputs = (
        ("history_sids", first_input_type, (1, SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH)),
        ("history_actions", TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        ("history_origins", TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        ("history_age_buckets", TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        ("history_mask", TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        ("candidate_sids", TensorProto.INT64, (1, SONA_MAX_CANDIDATES, SONA_SID_DEPTH)),
        ("candidate_mask", TensorProto.INT64, (1, SONA_MAX_CANDIDATES)),
        ("seed", TensorProto.INT64, (1,)),
    )
    outputs = (
        ("generated_sids", TensorProto.INT64, generated_shape),
        ("generated_log_probabilities", TensorProto.FLOAT, (1, 1)),
        (
            "ranking_head_scores",
            TensorProto.FLOAT,
            (1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)),
        ),
        ("ranking_scores", TensorProto.FLOAT, (1, SONA_MAX_CANDIDATES)),
    )
    nodes = tuple(
        helper.make_node(
            "Constant",
            [],
            [name],
            value=helper.make_tensor(
                f"{name}_value",
                tensor_type,
                shape,
                [0] * math.prod(shape),
            ),
        )
        for name, tensor_type, shape in outputs
    )
    graph = helper.make_graph(
        nodes,
        "sona-quality-test",
        tuple(
            helper.make_tensor_value_info(name, tensor_type, shape)
            for name, tensor_type, shape in inputs
        ),
        tuple(
            helper.make_tensor_value_info(name, tensor_type, shape)
            for name, tensor_type, shape in outputs
        ),
    )
    return cast(
        bytes,
        helper.make_model(
            graph,
            opset_imports=[helper.make_opsetid("", 20)],
        ).SerializeToString(),
    )


def _materialize_candidate_files(
    root: Path,
    *,
    source_approval_sha256: str,
    dataset_approval_sha256: str,
    dataset_bundle_sha256: str,
    train_manifest_sha256: str = "6" * 64,
) -> tuple[str, str, str]:
    checkpoint = root / "checkpoint"
    weights = checkpoint / "weights"
    weights.mkdir(parents=True)
    header = b"{'descr': '<f4', 'fortran_order': False, 'shape': (1,), }"
    header += b" " * ((64 - (10 + len(header) + 1) % 64) % 64) + b"\n"
    weight_payload = b"\x93NUMPY\x01\x00" + len(header).to_bytes(2, "little") + header
    weight_payload += b"\x00\x00\x80?"
    weight_sha256 = sha256(weight_payload).hexdigest()
    (weights / "0000.npy").write_bytes(weight_payload)
    aggregate = sha256()
    aggregate.update(b"encoder.weight")
    aggregate.update(bytes.fromhex(weight_sha256))
    weights_sha256 = aggregate.hexdigest()
    checkpoint_manifest: dict[str, JsonValue] = {
        "schema_version": 3,
        "format": "PICKLE_FREE_NUMPY_STATE_V1",
        "architecture": "SONA_LITE_SHARED_GRU_V1",
        "dataset_manifest_sha256": train_manifest_sha256,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_approval_sha256": dataset_approval_sha256,
        "dataset_bundle_sha256": dataset_bundle_sha256,
        "source_approval_sha256": source_approval_sha256,
        "tokenizer_sha256": "9" * 64,
        "quality_provenance_eligible": True,
        "quality_eligible": False,
        "weights_sha256": weights_sha256,
        "weights": [
            {
                "name": "encoder.weight",
                "file": "0000.npy",
                "sha256": weight_sha256,
                "dtype": "float32",
                "shape": [1],
            }
        ],
    }
    checkpoint_sha256 = sha256(rfc8785.dumps(checkpoint_manifest)).hexdigest()
    (checkpoint / "manifest.json").write_bytes(
        rfc8785.dumps({"manifest": checkpoint_manifest, "manifest_sha256": checkpoint_sha256})
    )
    artifact = root / "sona-quality.onnx"
    input_names = (
        "history_sids",
        "history_actions",
        "history_origins",
        "history_age_buckets",
        "history_mask",
        "candidate_sids",
        "candidate_mask",
        "seed",
    )
    output_names = (
        "generated_sids",
        "generated_log_probabilities",
        "ranking_head_scores",
        "ranking_scores",
    )
    artifact_payload = _sona_test_onnx()
    artifact.write_bytes(artifact_payload)
    artifact_sha256 = sha256(artifact_payload).hexdigest()
    artifact_manifest: dict[str, JsonValue] = {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_GRU_V1",
        "opset": 20,
        "inputs": list(input_names),
        "outputs": list(output_names),
        "artifact_sha256": artifact_sha256,
        "weights_sha256": weights_sha256,
        "config_sha256": HASH_A,
        "training_provenance": {
            "checkpoint_manifest_sha256": checkpoint_sha256,
            "checkpoint_weights_sha256": weights_sha256,
            "dataset_manifest_sha256": train_manifest_sha256,
            "tokenizer_sha256": "9" * 64,
            "dataset_approval_sha256": dataset_approval_sha256,
            "dataset_bundle_sha256": dataset_bundle_sha256,
            "quality_provenance_eligible": True,
            "quality_eligible": False,
        },
    }
    artifact_manifest_sha256 = sha256(rfc8785.dumps(artifact_manifest)).hexdigest()
    artifact.with_suffix(".onnx.manifest.json").write_bytes(
        rfc8785.dumps(
            {
                "manifest": artifact_manifest,
                "manifest_sha256": artifact_manifest_sha256,
            }
        )
    )
    commit: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": artifact_sha256,
        "model_manifest_sha256": artifact_manifest_sha256,
    }
    artifact.with_suffix(".onnx.commit.json").write_bytes(
        rfc8785.dumps(
            {"commit": commit, "commit_sha256": sha256(rfc8785.dumps(commit)).hexdigest()}
        )
    )
    return checkpoint_sha256, artifact_sha256, artifact_manifest_sha256


def _approval_documents(
    case_evidence: tuple[PairedRankingCaseEvidence, ...],
    *,
    scenarios: tuple[DirectionalScenarioEvidence, ...],
    safety: SonaSafetyEvidence,
    performance: SonaPerformanceEvidence,
    case_bundle_override: str | None = None,
    test_request_set_override: tuple[str, ...] | None = None,
    candidate_root: Path | None = None,
    candidate_train_manifest_override: str | None = None,
    candidate_source_approval_override: str | None = None,
    reviewer_key: ec.EllipticCurvePrivateKey | None = None,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    bytes,
    SonaPerformanceEvidence,
    bytes,
]:
    cases = _verified_cases(case_evidence)
    key = reviewer_key or REVIEWER_KEY
    spki = public_spki(key)
    thumbprint = public_key_thumbprint(spki).hex()
    source_payload: dict[str, object] = {
        "schema_version": 1,
        "approval_kind": "SONA_SOURCE_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "source_manifest_sha256": "1" * 64,
        "embedding_snapshot_sha256": "2" * 64,
        "catalog_snapshot_sha256": "3" * 64,
        "owner_lineage_key_id": "r1b-test",
        "owner_count": 1,
        "recording_count": 4,
        "request_count": len(cases),
        "request_set_sha256": "4" * 64,
        "recording_set_sha256": "5" * 64,
        "approved_at_ms": 100,
        "not_after_ms": 1_000,
        "reviewer_key_thumbprint_sha256": thumbprint,
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    source_document = _signed(source_payload, key, SONA_SOURCE_APPROVAL_DOMAIN)
    source_approval_sha256 = sha256(
        rfc8785.dumps(cast(dict[str, JsonValue], source_document))
    ).hexdigest()
    test_request_sha256s = test_request_set_override or tuple(
        sorted(case.request_sha256 for case in cases)
    )
    dataset_payload: dict[str, object] = {
        "schema_version": 1,
        "approval_kind": "SONA_DATASET_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_bundle_sha256": "0" * 64,
        "train_manifest_sha256": "6" * 64,
        "validation_manifest_sha256": "7" * 64,
        "test_manifest_sha256": "8" * 64,
        "test_request_count": len(test_request_sha256s),
        "test_request_set_sha256": sha256(rfc8785.dumps(list(test_request_sha256s))).hexdigest(),
        "source_approval_sha256": source_approval_sha256,
        "tokenizer_manifest_sha256": "9" * 64,
        "tokenizer_source_snapshot_sha256": "2" * 64,
        "teacher_manifest_sha256": "a" * 64,
        "source_model_manifest_sha256": "b" * 64,
        "split_policy": "OWNER_TIME_ORDERED_WITH_7D_LABEL_EMBARGO_V1",
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "train_cutoff_start_ms": 0,
        "train_cutoff_end_ms": 100,
        "validation_cutoff_start_ms": 100 + SONA_LABEL_DELAY_EMBARGO_MS,
        "validation_cutoff_end_ms": 200 + SONA_LABEL_DELAY_EMBARGO_MS,
        "test_cutoff_start_ms": 200 + 2 * SONA_LABEL_DELAY_EMBARGO_MS,
        "test_cutoff_end_ms": 300 + 2 * SONA_LABEL_DELAY_EMBARGO_MS,
        "request_hash_overlap_count": 0,
        "example_hash_overlap_count": 0,
        "leakage_audit_sha256": "c" * 64,
        "owner_lineage_manifest_sha256": "d" * 64,
        "approved_at_ms": 200,
        "not_after_ms": 900,
        "reviewer_key_thumbprint_sha256": thumbprint,
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    dataset_payload["dataset_bundle_sha256"] = compute_sona_dataset_bundle_sha256(dataset_payload)
    dataset_document = _signed(dataset_payload, key, SONA_DATASET_APPROVAL_DOMAIN)
    dataset_approval_sha256 = sha256(
        rfc8785.dumps(cast(dict[str, JsonValue], dataset_document))
    ).hexdigest()
    selected_performance = performance
    checkpoint_manifest_sha256 = performance.checkpoint_manifest_sha256
    artifact_sha256 = performance.artifact_sha256
    artifact_manifest_sha256 = performance.artifact_storage_manifest_sha256
    if candidate_root is not None:
        (
            checkpoint_manifest_sha256,
            artifact_sha256,
            artifact_manifest_sha256,
        ) = _materialize_candidate_files(
            candidate_root,
            source_approval_sha256=(candidate_source_approval_override or source_approval_sha256),
            dataset_approval_sha256=dataset_approval_sha256,
            dataset_bundle_sha256=cast(str, dataset_payload["dataset_bundle_sha256"]),
            train_manifest_sha256=candidate_train_manifest_override or "6" * 64,
        )
        selected_performance = SonaPerformanceEvidence.from_benchmark(
            samples=performance.samples,
            ort_benchmark_evidence_sha256=performance.ort_benchmark_evidence_sha256,
            environment_sha256=performance.environment_sha256,
            checkpoint_manifest_sha256=checkpoint_manifest_sha256,
            artifact_sha256=artifact_sha256,
            tokenizer_manifest_sha256=performance.tokenizer_manifest_sha256,
            warmup_iterations=performance.warmup_iterations,
            measured_iterations_per_case=performance.measured_iterations_per_case,
            dataset_storage_bytes=performance.dataset_storage_bytes,
            artifact_storage_bytes=performance.artifact_storage_bytes,
            dataset_storage_manifest_sha256=performance.dataset_storage_manifest_sha256,
            artifact_storage_manifest_sha256=artifact_manifest_sha256,
        )
    ort_evidence = _ort_benchmark_evidence(
        selected_performance,
        dataset_approval_sha256=dataset_approval_sha256,
        dataset_bundle_sha256=cast(str, dataset_payload["dataset_bundle_sha256"]),
    )
    ort_evidence_sha256 = cast(str, json.loads(ort_evidence)["evidence_sha256"])
    selected_performance = SonaPerformanceEvidence.from_benchmark(
        samples=selected_performance.samples,
        ort_benchmark_evidence_sha256=ort_evidence_sha256,
        environment_sha256=selected_performance.environment_sha256,
        checkpoint_manifest_sha256=selected_performance.checkpoint_manifest_sha256,
        artifact_sha256=selected_performance.artifact_sha256,
        tokenizer_manifest_sha256=selected_performance.tokenizer_manifest_sha256,
        warmup_iterations=selected_performance.warmup_iterations,
        measured_iterations_per_case=selected_performance.measured_iterations_per_case,
        dataset_storage_bytes=selected_performance.dataset_storage_bytes,
        artifact_storage_bytes=selected_performance.artifact_storage_bytes,
        dataset_storage_manifest_sha256=selected_performance.dataset_storage_manifest_sha256,
        artifact_storage_manifest_sha256=selected_performance.artifact_storage_manifest_sha256,
    )
    evaluation_payload: dict[str, object] = {
        "schema_version": 1,
        "approval_kind": "SONA_EVALUATION_EVIDENCE_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_approval_sha256": dataset_approval_sha256,
        "dataset_bundle_sha256": dataset_payload["dataset_bundle_sha256"],
        "tokenizer_manifest_sha256": "9" * 64,
        "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
        "artifact_sha256": artifact_sha256,
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "p11_pipeline_manifest_sha256": P11_PIPELINE,
        "sona_pipeline_manifest_sha256": SONA_PIPELINE,
        "contract_policy_sha256": compute_sona_contract_policy_sha256(),
        "evaluation_case_bundle_sha256": case_bundle_override
        or compute_sona_evaluation_case_bundle_sha256(cases),
        "execution_evidence_bundle_sha256": sha256(
            sona_paired_execution_evidence_document(case_evidence)
        ).hexdigest(),
        "outcome_evidence_bundle_sha256": sha256(_outcome_evidence(case_evidence)).hexdigest(),
        "directional_scenario_bundle_sha256": compute_sona_directional_scenario_bundle_sha256(
            scenarios
        ),
        "safety_evidence_bundle_sha256": compute_sona_safety_evidence_bundle_sha256(safety),
        "performance_evidence_sha256": compute_sona_performance_evidence_sha256(
            selected_performance
        ),
        "approved_at_ms": 300,
        "not_after_ms": 800,
        "reviewer_key_thumbprint_sha256": thumbprint,
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    evaluation_document = _signed(evaluation_payload, key, SONA_EVALUATION_APPROVAL_DOMAIN)
    return (
        source_document,
        dataset_document,
        evaluation_document,
        spki,
        selected_performance,
        ort_evidence,
    )


def _evaluate(
    cases: tuple[PairedRankingCaseEvidence, ...],
    *,
    scenarios: tuple[DirectionalScenarioEvidence, ...] | None = None,
    safety: SonaSafetyEvidence | None = None,
    performance: SonaPerformanceEvidence | None = None,
) -> PairedEvaluationReport:
    selected_scenarios = scenarios or _scenarios()
    selected_safety = safety or _safety()
    selected_performance = performance or _performance()
    (
        source_document,
        dataset_document,
        evaluation_document,
        _,
        selected_performance,
        ort_evidence,
    ) = _approval_documents(
        cases,
        scenarios=selected_scenarios,
        safety=selected_safety,
        performance=selected_performance,
    )
    return evaluate_paired_rankings(
        cases,
        catalog_recording_count=4,
        source_approval_document=source_document,
        dataset_approval_document=dataset_document,
        evaluation_approval_document=evaluation_document,
        at_ms=350,
        directional_scenarios=selected_scenarios,
        execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
        outcome_evidence_document_rfc8785=_outcome_evidence(cases),
        safety_evidence_document_rfc8785=sona_safety_evidence_document(selected_safety),
        performance_evidence_document_rfc8785=sona_performance_evidence_document(
            selected_performance
        ),
        ort_benchmark_evidence_document_rfc8785=ort_evidence,
    )


def _artifact_approval(
    report: PairedEvaluationReport,
    key: ec.EllipticCurvePrivateKey,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "approval_kind": "SONA_QUALITY_ELIGIBLE_ARTIFACT_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "evaluation_approval_sha256": report.evaluation_approval_sha256,
        "evaluation_report_sha256": report.report_sha256,
        "metrics_sha256": report.metrics_sha256,
        "performance_sha256": report.performance_sha256,
        "dataset_approval_sha256": report.dataset_approval_sha256,
        "dataset_bundle_sha256": report.dataset_bundle_sha256,
        "tokenizer_manifest_sha256": report.tokenizer_manifest_sha256,
        "checkpoint_manifest_sha256": report.checkpoint_manifest_sha256,
        "artifact_sha256": report.artifact_sha256,
        "artifact_manifest_sha256": report.artifact_manifest_sha256,
        "contract_policy_sha256": report.contract_policy_sha256,
        "quality_gate_decision": "PASS",
        "quality_eligible": True,
        "approved_at_ms": 400,
        "not_after_ms": 700,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    return _signed(payload, key, SONA_ARTIFACT_APPROVAL_DOMAIN)


def test_paired_report_passes_exact_quality_safety_and_latency_gates() -> None:
    report = _evaluate((_case(),))

    assert report.verdict.eligible is True
    assert report.verdict.failures == ()
    assert report.sona.signal_metrics["selection"].ndcg_at_10 == 1.0
    assert report.sona.signal_metrics["skip"].exposure_at_10 == 0.25
    assert report.sona_latency_p95_ms == 120.0
    assert report.latency_ratio_p95 == 1.2


def test_evaluator_parses_canonical_raw_safety_and_performance_artifacts() -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    performance = _performance()
    source, dataset, approval, _, selected_performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
    )
    altered_execution = cast(
        dict[str, JsonValue],
        json.loads(sona_paired_execution_evidence_document(cases)),
    )
    first_execution = cast(list[dict[str, JsonValue]], altered_execution["cases"])[0]
    p11_request = cast(dict[str, JsonValue], first_execution["p11_request"])
    p11_request["request_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="execution evidence ancestry"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=rfc8785.dumps(altered_execution),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )
    altered_ort = cast(dict[str, JsonValue], json.loads(ort_evidence))
    altered_ort_evidence = cast(dict[str, JsonValue], altered_ort["evidence"])
    altered_ort_samples = cast(list[dict[str, JsonValue]], altered_ort_evidence["raw_samples"])
    altered_ort_samples[0]["latency_ms"] = 999.0
    altered_ort["evidence_sha256"] = sha256(rfc8785.dumps(altered_ort_evidence)).hexdigest()
    with pytest.raises(ValueError, match="ORT benchmark evidence ancestry"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=rfc8785.dumps(altered_ort),
        )
    with pytest.raises(ValueError, match="not canonical"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=b" " + sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )

    changed_safety = SonaSafetyEvidence(
        tuple(
            SonaSafetyGateEvidence.from_audit(
                check.gate_id,
                covered_request_sha256s=check.covered_request_sha256s,
                violation_event_sha256s=("a" * 64,),
            )
            if check.gate_id == "owner_scope"
            else check
            for check in safety.checks
        )
    )
    with pytest.raises(ValueError, match="signed approval"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(changed_safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )

    altered_outcomes = cast(dict[str, JsonValue], json.loads(_outcome_evidence(cases)))
    first_outcome = cast(list[dict[str, JsonValue]], altered_outcomes["outcomes"])[0]
    first_outcome["signal"] = "completion"
    with pytest.raises(ValueError, match="outcome evidence ancestry"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=rfc8785.dumps(altered_outcomes),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_report_keeps_metrics_performance_and_envelope_hashes_separate() -> None:
    baseline = _evaluate((_case(),))
    changed = _evaluate((_case(),), performance=_performance(sona_ms=(130.0, 140.0)))

    assert baseline.metrics_sha256 != baseline.performance_sha256
    assert baseline.report_sha256 not in {baseline.metrics_sha256, baseline.performance_sha256}
    assert baseline.performance_sha256 != changed.performance_sha256
    assert baseline.report_sha256 != changed.report_sha256


def test_report_canonical_document_round_trips_and_rejects_tampering() -> None:
    report = _evaluate((_case(),))
    document = paired_evaluation_report_document(report)

    assert paired_evaluation_report_from_document(cast(dict[str, object], document)) == report
    raw_report = cast(dict[str, object], document["report"])
    raw_report["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="canonical hash mismatch"):
        paired_evaluation_report_from_document(cast(dict[str, object], document))


def test_report_recomputes_semantic_gate_after_attacker_rehashes_envelope() -> None:
    report = _evaluate((_case(),))
    document = paired_evaluation_report_document(report)
    raw_report = cast(dict[str, JsonValue], document["report"])
    raw_report["eligible"] = False
    raw_report["failures"] = ["NDCG_REGRESSION"]
    document["report_sha256"] = sha256(rfc8785.dumps(raw_report)).hexdigest()

    with pytest.raises(ValueError, match="semantic gate verdict"):
        paired_evaluation_report_from_document(cast(dict[str, object], document))


def test_passing_report_and_final_signed_approval_derive_artifact_quality_eligibility(
    tmp_path: Path,
) -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    performance = _performance()
    key = REVIEWER_KEY
    (
        source,
        dataset,
        evaluation,
        _,
        selected_performance,
        ort_evidence,
    ) = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
        reviewer_key=key,
        candidate_root=tmp_path,
    )
    report = evaluate_paired_rankings(
        cases,
        catalog_recording_count=4,
        source_approval_document=source,
        dataset_approval_document=dataset,
        evaluation_approval_document=evaluation,
        at_ms=350,
        directional_scenarios=scenarios,
        execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
        outcome_evidence_document_rfc8785=_outcome_evidence(cases),
        safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
        performance_evidence_document_rfc8785=sona_performance_evidence_document(
            selected_performance
        ),
        ort_benchmark_evidence_document_rfc8785=ort_evidence,
    )
    artifact_approval = _artifact_approval(report, key)

    verified = verify_quality_eligible_sona_artifact(
        report_document=cast(dict[str, object], paired_evaluation_report_document(report)),
        source_approval_document=source,
        dataset_approval_document=dataset,
        evaluation_approval_document=evaluation,
        artifact_approval_document=artifact_approval,
        checkpoint_directory=tmp_path / "checkpoint",
        artifact_path=tmp_path / "sona-quality.onnx",
        at_ms=450,
    )

    assert verified.quality_eligible is True
    assert verified.artifact_sha256 == report.artifact_sha256

    (tmp_path / "sona-quality.onnx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        verify_quality_eligible_sona_artifact(
            report_document=cast(dict[str, object], paired_evaluation_report_document(report)),
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=evaluation,
            artifact_approval_document=artifact_approval,
            checkpoint_directory=tmp_path / "checkpoint",
            artifact_path=tmp_path / "sona-quality.onnx",
            at_ms=450,
        )


@pytest.mark.parametrize("mismatch", ("train_manifest", "source_approval"))
def test_final_candidate_rejects_rehashed_checkpoint_outside_dataset_ancestry(
    tmp_path: Path,
    mismatch: str,
) -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    source, dataset, evaluation, _, performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=_performance(),
        reviewer_key=REVIEWER_KEY,
        candidate_root=tmp_path,
        candidate_train_manifest_override=("0" * 64 if mismatch == "train_manifest" else None),
        candidate_source_approval_override=("0" * 64 if mismatch == "source_approval" else None),
    )
    report = evaluate_paired_rankings(
        cases,
        catalog_recording_count=4,
        source_approval_document=source,
        dataset_approval_document=dataset,
        evaluation_approval_document=evaluation,
        at_ms=350,
        directional_scenarios=scenarios,
        execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
        outcome_evidence_document_rfc8785=_outcome_evidence(cases),
        safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
        performance_evidence_document_rfc8785=sona_performance_evidence_document(performance),
        ort_benchmark_evidence_document_rfc8785=ort_evidence,
    )

    with pytest.raises(ValueError, match="checkpoint does not match"):
        verify_quality_eligible_sona_artifact(
            report_document=cast(dict[str, object], paired_evaluation_report_document(report)),
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=evaluation,
            artifact_approval_document=_artifact_approval(report, REVIEWER_KEY),
            checkpoint_directory=tmp_path / "checkpoint",
            artifact_path=tmp_path / "sona-quality.onnx",
            at_ms=450,
        )


def test_gate_fails_closed_for_quality_direction_safety_quality_and_latency() -> None:
    baseline_safety = _safety()
    safety = replace(
        baseline_safety,
        checks=tuple(
            SonaSafetyGateEvidence.from_audit(
                check.gate_id,
                covered_request_sha256s=check.covered_request_sha256s,
                violation_event_sha256s=("a" * 64,),
            )
            if check.gate_id == "owner_scope"
            else check
            for check in baseline_safety.checks
        ),
    )
    report = _evaluate(
        (_case(sona=(C, D)),),
        scenarios=_scenarios(failed="rising-interest-positive-momentum"),
        safety=safety,
        performance=_performance(sona_ms=(390.0, 400.0)),
    )

    assert report.verdict.eligible is False
    assert set(report.verdict.failures) >= {
        "DIRECTIONAL_SCENARIO_FAILURE",
        "SAFETY_PRIVACY_OR_REPLAY_VIOLATION",
        "NDCG_REGRESSION",
        "LATENCY_ABSOLUTE",
        "LATENCY_RATIO",
    }


def test_gate_fails_closed_when_any_required_signal_has_no_labels() -> None:
    report = _evaluate((_case(outcome_documents=_outcomes(signals=("selection",))),))

    assert report.verdict.eligible is False
    assert "MISSING_SIGNAL_LABELS" in report.verdict.failures


def test_attributed_outcome_must_follow_cutoff_and_match_served_baseline_rank() -> None:
    at_cutoff = tuple(dict(value) for value in _outcomes())
    at_cutoff[0]["observed_at_ms"] = CUTOFF_MS
    wrong_rank = tuple(dict(value) for value in _outcomes())
    wrong_rank[0]["source_rank"] = 2

    with pytest.raises(ValueError, match="causal attribution bounds"):
        _verified_cases((_case(outcome_documents=at_cutoff),))
    with pytest.raises(ValueError, match="causal attribution bounds"):
        _verified_cases((_case(outcome_documents=wrong_rank),))


def test_case_builder_recomputes_canonical_sona_request_hash() -> None:
    evidence = _case()
    altered = replace(
        evidence,
        sona_request=replace(evidence.sona_request, seed=evidence.sona_request.seed + 1),
    )

    with pytest.raises(ValueError, match="source evidence binding"):
        _verified_cases((altered,))


def test_final_onnx_verifier_rejects_runtime_dtype_and_shape_mismatch() -> None:
    _verify_onnx_model_structure(_sona_test_onnx())

    with pytest.raises(ValueError, match="tensor contract"):
        _verify_onnx_model_structure(_sona_test_onnx(first_input_type=TensorProto.FLOAT))
    with pytest.raises(ValueError, match="tensor contract"):
        _verify_onnx_model_structure(_sona_test_onnx(generated_shape=(1, 2, SONA_SID_DEPTH)))


def test_verified_case_cannot_be_reconstructed_or_altered_outside_source_builder() -> None:
    case = _verified_cases((_case(),))[0]

    with pytest.raises(ValueError, match="verified source evidence"):
        replace(case, case_id="manually-altered")


def test_directional_expected_hashes_match_canonical_fixture_and_duplicates_fail() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "recommendations"
        / "v1"
        / "scenario-vectors.json"
    )
    fixture = json.loads(fixture_path.read_bytes())
    expected = {
        item["case_id"]: sha256(rfc8785.dumps(item["expected"])).hexdigest()
        for item in fixture["cases"]
    }
    assert expected == SONA_DIRECTIONAL_EXPECTED_SHA256

    cases = (_case(),)
    scenarios = (*_scenarios(), _scenarios()[0])
    safety = _safety()
    performance = _performance()
    source, dataset, approval, _, selected_performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
    )
    with pytest.raises(ValueError, match="scenario set is incomplete"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_safety_and_latency_evidence_must_exactly_cover_each_paired_case() -> None:
    cases = (_case(),)
    baseline_safety = _safety()
    incomplete_safety = replace(
        baseline_safety,
        checks=(
            SonaSafetyGateEvidence.from_audit(
                baseline_safety.checks[0].gate_id,
                covered_request_sha256s=("2" * 64,),
            ),
            *baseline_safety.checks[1:],
        ),
    )
    performance = _performance()
    scenarios = _scenarios()
    source, dataset, approval, _, selected_performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=incomplete_safety,
        performance=performance,
    )
    with pytest.raises(ValueError, match="request coverage is incomplete"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(incomplete_safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )

    wrong_samples = (
        replace(performance.samples[0], request_sha256="2" * 64),
        performance.samples[1],
    )
    wrong_performance = SonaPerformanceEvidence.from_benchmark(
        samples=wrong_samples,
        ort_benchmark_evidence_sha256=performance.ort_benchmark_evidence_sha256,
        environment_sha256=performance.environment_sha256,
        checkpoint_manifest_sha256=performance.checkpoint_manifest_sha256,
        artifact_sha256=performance.artifact_sha256,
        tokenizer_manifest_sha256=performance.tokenizer_manifest_sha256,
        warmup_iterations=performance.warmup_iterations,
        measured_iterations_per_case=performance.measured_iterations_per_case,
        dataset_storage_bytes=performance.dataset_storage_bytes,
        artifact_storage_bytes=performance.artifact_storage_bytes,
        dataset_storage_manifest_sha256=performance.dataset_storage_manifest_sha256,
        artifact_storage_manifest_sha256=performance.artifact_storage_manifest_sha256,
    )
    source, dataset, approval, _, selected_performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=baseline_safety,
        performance=wrong_performance,
    )
    with pytest.raises(ValueError, match="coverage"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(baseline_safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_signed_evidence_hash_mismatch_is_rejected_before_metrics() -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    performance = _performance()
    (
        source_document,
        dataset_document,
        evaluation_document,
        _,
        selected_performance,
        ort_evidence,
    ) = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
        case_bundle_override="0" * 64,
    )

    with pytest.raises(ValueError, match="signed approval"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source_document,
            dataset_approval_document=dataset_document,
            evaluation_approval_document=evaluation_document,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_evaluation_cases_must_exactly_match_approved_test_request_membership() -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    performance = _performance()
    source, dataset, approval, _, selected_performance, ort_evidence = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
        test_request_set_override=("2" * 64,),
    )

    with pytest.raises(ValueError, match="approved test request set"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source,
            dataset_approval_document=dataset,
            evaluation_approval_document=approval,
            at_ms=350,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_evaluation_reverifies_signed_approval_expiry_at_use() -> None:
    cases = (_case(),)
    scenarios = _scenarios()
    safety = _safety()
    performance = _performance()
    (
        source_document,
        dataset_document,
        evaluation_document,
        _,
        selected_performance,
        ort_evidence,
    ) = _approval_documents(
        cases,
        scenarios=scenarios,
        safety=safety,
        performance=performance,
    )

    with pytest.raises(ValueError, match="not valid"):
        evaluate_paired_rankings(
            cases,
            catalog_recording_count=4,
            source_approval_document=source_document,
            dataset_approval_document=dataset_document,
            evaluation_approval_document=evaluation_document,
            at_ms=850,
            directional_scenarios=scenarios,
            execution_evidence_document_rfc8785=sona_paired_execution_evidence_document(cases),
            outcome_evidence_document_rfc8785=_outcome_evidence(cases),
            safety_evidence_document_rfc8785=sona_safety_evidence_document(safety),
            performance_evidence_document_rfc8785=sona_performance_evidence_document(
                selected_performance
            ),
            ort_benchmark_evidence_document_rfc8785=ort_evidence,
        )


def test_contract_policy_pin_matches_full_canonical_policy() -> None:
    policy_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "recommendations"
        / "v1"
        / "contract-policy.json"
    )
    actual = sha256(rfc8785.dumps(json.loads(policy_path.read_bytes()))).hexdigest()

    assert actual == compute_sona_contract_policy_sha256()


def test_latency_ratio_uses_full_precision_at_frozen_boundary() -> None:
    exact = _evaluate(
        (_case(),),
        performance=_performance(sona_ms=(125.0, 125.0)),
    )
    above = _evaluate(
        (_case(),),
        performance=_performance(
            sona_ms=(math.nextafter(125.0, math.inf), math.nextafter(125.0, math.inf))
        ),
    )

    assert "LATENCY_RATIO" not in exact.verdict.failures
    assert "LATENCY_RATIO" in above.verdict.failures
