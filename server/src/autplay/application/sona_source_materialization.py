"""Pure P11-to-Sona materialization with explicit reconstructed temporal lineage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from math import isfinite
from typing import Final, cast
from uuid import UUID, uuid5

import rfc8785

from autplay.application.adaptive_recommendations import (
    canonical_sha256,
    temporal_evidence_document,
)
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    request_document,
)
from autplay.application.sona import build_sona_inference_request, sona_inference_request_document
from autplay.application.sona_source_planning import (
    SonaSourcePlanningRecord,
    SonaSourceRequestObservation,
    SonaSourceRequestRekey,
    derive_sona_source_request_rekey,
)
from autplay.application.sona_source_reconstruction import (
    SONA_0026_EVIDENCE_ID_SCHEME,
    SONA_0026_NORMALIZATION_POLICY,
    classify_sona_0026_listening_values,
)
from autplay.application.sona_training import (
    build_sona_teacher_snapshot,
    build_sona_training_example,
    verify_sona_teacher_snapshot,
)
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    MAX_SOURCE_EVENTS,
    TemporalEvidence,
    TemporalSignal,
)
from autplay.domain.recommendations import (
    JsonValue,
    PipelineDefinition,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import (
    UNKNOWN_SEMANTIC_ID,
    SonaInferenceRequest,
    SonaSemanticId,
)
from autplay.domain.sona_training import (
    SonaObservedOutcome,
    SonaTeacherSnapshot,
    SonaTrainingExample,
)

SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1: Final = (
    "RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1"
)
SONA_TEMPORAL_RECONSTRUCTION_KIND: Final = "SONA_TEMPORAL_RECONSTRUCTION_V1"
SONA_TEMPORAL_RECONSTRUCTION_NAMESPACE: Final = UUID("470a8daf-1b4e-5d2a-aebd-666547648682")
SONA_P11_TEACHER_CALIBRATION_INPUT_KIND: Final = "SONA_P11_TEACHER_CALIBRATION_INPUT_V1"
SONA_P11_TEACHER_CALIBRATION_POLICY: Final = "P11_LOGIT_TEMPERATURE_CALIBRATION_V1"
SONA_P11_TEACHER_INPUT_SEMANTICS: Final = "P11_RAW_HEURISTIC_SCORE_AS_LOGIT_V1"


@dataclass(frozen=True, slots=True)
class SonaSourceHistoricalCase:
    """Transient owner-bearing facts for one exact historical P11 request."""

    observation: SonaSourceRequestObservation
    query: RecommendationQuery
    p11_pipeline: PipelineDefinition
    baseline: RecommendationInputSnapshot
    evidence: tuple[TemporalEvidence, ...]
    outcome: SonaObservedOutcome
    teacher: SonaTeacherSnapshot


@dataclass(frozen=True, slots=True)
class SonaSourceHistoricalInputs:
    """Teacher-independent facts needed before validation-only calibration can run."""

    observation: SonaSourceRequestObservation
    query: RecommendationQuery
    p11_pipeline: PipelineDefinition
    baseline: RecommendationInputSnapshot
    evidence: tuple[TemporalEvidence, ...]
    outcome: SonaObservedOutcome


@dataclass(frozen=True, slots=True)
class SonaReconstructedTemporalInput:
    """Content identity for 0026-derived evidence; never a persisted R1A snapshot claim."""

    source_kind: str
    temporal_snapshot_id: UUID
    temporal_snapshot_sha256: str
    source_evidence_sha256: str
    source_evidence_count: int
    document: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SonaP11TeacherCalibrationInput:
    """Candidate-complete P11 scores; no fitted calibration is implied."""

    p11_request_sha256: str
    sona_request_sha256: str
    p11_pipeline_manifest_sha256: str
    scores: tuple[tuple[UUID, float], ...]
    document: dict[str, JsonValue]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class SonaMaterializedSourceCase:
    """Transient materialization result ready for split reconciliation and tensorization."""

    temporal_input: SonaReconstructedTemporalInput
    request: SonaInferenceRequest
    outcome: SonaObservedOutcome
    teacher: SonaTeacherSnapshot
    teacher_calibration_input: SonaP11TeacherCalibrationInput
    rekey: SonaSourceRequestRekey
    example: SonaTrainingExample


@dataclass(frozen=True, slots=True)
class SonaPreparedSourceCase:
    """Teacher-independent case ready for split planning and V2 calibration."""

    temporal_input: SonaReconstructedTemporalInput
    request: SonaInferenceRequest
    outcome: SonaObservedOutcome
    teacher_calibration_input: SonaP11TeacherCalibrationInput
    rekey: SonaSourceRequestRekey


def reconstruct_sona_temporal_input(
    case: SonaSourceHistoricalCase,
) -> SonaReconstructedTemporalInput:
    """Bind normalized 0026 evidence without representing it as an original R1A row."""

    _verify_historical_case_ancestry(case)
    return _reconstruct_sona_temporal_input(
        case.observation,
        case.baseline,
        case.evidence,
    )


def _reconstruct_sona_temporal_input(
    observation: SonaSourceRequestObservation,
    baseline: RecommendationInputSnapshot,
    evidence: tuple[TemporalEvidence, ...],
) -> SonaReconstructedTemporalInput:
    if not 1 <= len(evidence) <= MAX_SOURCE_EVENTS:
        raise ValueError("Sona reconstructed temporal evidence count is outside the accepted bound")
    evidence_ids = tuple(value.evidence_id for value in evidence)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("Sona reconstructed temporal evidence contains duplicate identities")
    source_hash_by_event: dict[UUID, str] = {}
    for value in evidence:
        prior = source_hash_by_event.setdefault(
            value.source_event_id,
            value.source_request_sha256,
        )
        if prior != value.source_request_sha256:
            raise ValueError("Sona reconstructed temporal evidence source hash conflicts")
    if any(
        value.owner_user_id != observation.owner_user_id
        or value.server_sequence is None
        or value.server_sequence > baseline.reference.interaction_watermark
        or value.received_at_ms > observation.cutoff_at_ms
        or value.effective_at_ms > observation.cutoff_at_ms
        for value in evidence
    ):
        raise ValueError("Sona reconstructed temporal evidence escapes the historical boundary")
    ordered = tuple(
        sorted(
            evidence,
            key=lambda value: (
                value.effective_at_ms,
                value.received_at_ms,
                cast(int, value.server_sequence),
                value.evidence_id.hex,
            ),
        )
    )
    evidence_documents = cast(
        list[JsonValue], [temporal_evidence_document(value) for value in ordered]
    )
    source_evidence_sha256 = canonical_sha256(evidence_documents)
    identity_document: dict[str, JsonValue] = {
        "schema_version": 1,
        "reconstruction_kind": SONA_TEMPORAL_RECONSTRUCTION_KIND,
        "source_kind": SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1,
        "source_schema_head": "0026",
        "persisted_original_temporal_snapshot": False,
        "normalization_policy": SONA_0026_NORMALIZATION_POLICY,
        "normalized_evidence_id_scheme": SONA_0026_EVIDENCE_ID_SCHEME,
        "p11_request_sha256": observation.request_sha256,
        "baseline_snapshot_id": str(baseline.reference.snapshot_id),
        "baseline_input_snapshot_sha256": baseline.reference.input_snapshot_sha256,
        "cutoff_at_ms": observation.cutoff_at_ms,
        "interaction_watermark": baseline.reference.interaction_watermark,
        "feature_policy_sha256": DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256,
        "source_evidence_count": len(ordered),
        "source_evidence_sha256": source_evidence_sha256,
        "stable_order": [
            "effective_at_ms",
            "received_at_ms",
            "server_sequence",
            "evidence_id",
        ],
    }
    identity_sha256 = canonical_sha256(identity_document)
    temporal_snapshot_id = uuid5(SONA_TEMPORAL_RECONSTRUCTION_NAMESPACE, identity_sha256)
    unhashed_document = {
        **identity_document,
        "temporal_snapshot_id": str(temporal_snapshot_id),
        "temporal_snapshot_id_scheme": "UUID5_RECONSTRUCTION_IDENTITY_SHA256_HEX_V1",
    }
    temporal_snapshot_sha256 = canonical_sha256(unhashed_document)
    document = {
        **unhashed_document,
        "temporal_snapshot_sha256": temporal_snapshot_sha256,
    }
    return SonaReconstructedTemporalInput(
        source_kind=SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1,
        temporal_snapshot_id=temporal_snapshot_id,
        temporal_snapshot_sha256=temporal_snapshot_sha256,
        source_evidence_sha256=source_evidence_sha256,
        source_evidence_count=len(ordered),
        document=document,
    )


def materialize_sona_source_historical_case(
    case: SonaSourceHistoricalCase,
    *,
    semantic_ids: Mapping[UUID, SonaSemanticId],
    tokenizer_sha256: str,
    model_manifest_sha256: str,
    owner_lineage_hmac_key: bytes,
    candidate_authority: RecommendationPipelineRunner | None = None,
) -> SonaMaterializedSourceCase:
    """Build one canonical Sona example and its exact P11-to-Sona re-key."""

    _verify_historical_case_ancestry(case)
    prepared = prepare_sona_source_historical_inputs(
        SonaSourceHistoricalInputs(
            observation=case.observation,
            query=case.query,
            p11_pipeline=case.p11_pipeline,
            baseline=case.baseline,
            evidence=case.evidence,
            outcome=case.outcome,
        ),
        semantic_ids=semantic_ids,
        tokenizer_sha256=tokenizer_sha256,
        model_manifest_sha256=model_manifest_sha256,
        owner_lineage_hmac_key=owner_lineage_hmac_key,
        candidate_authority=candidate_authority,
    )
    teacher = build_sona_teacher_snapshot(
        source_request_sha256=prepared.request.request_sha256,
        teacher_key=case.teacher.teacher_key,
        teacher_version=case.teacher.teacher_version,
        teacher_manifest_sha256=case.teacher.teacher_manifest_sha256,
        targets=case.teacher.targets,
    )
    return finalize_sona_prepared_source_case(prepared, teacher=teacher)


def prepare_sona_source_historical_inputs(
    inputs: SonaSourceHistoricalInputs,
    *,
    semantic_ids: Mapping[UUID, SonaSemanticId],
    tokenizer_sha256: str,
    model_manifest_sha256: str,
    owner_lineage_hmac_key: bytes,
    candidate_authority: RecommendationPipelineRunner | None = None,
) -> SonaPreparedSourceCase:
    """Build teacher-independent identities and raw P11 calibration inputs."""

    _verify_historical_inputs_ancestry(inputs)
    temporal_input = _reconstruct_sona_temporal_input(
        inputs.observation,
        inputs.baseline,
        inputs.evidence,
    )
    authority = candidate_authority or RecommendationPipelineRunner()
    request = build_sona_inference_request(
        inputs.query,
        inputs.baseline,
        temporal_snapshot_id=temporal_input.temporal_snapshot_id,
        cutoff_at_ms=inputs.observation.cutoff_at_ms,
        evidence=inputs.evidence,
        semantic_ids=semantic_ids,
        tokenizer_sha256=tokenizer_sha256,
        model_manifest_sha256=model_manifest_sha256,
        candidate_authority=authority,
    )
    mandatory_recording_ids = {
        value.recording_id
        for value in authority.filter_snapshot_tracks(inputs.query, inputs.baseline)
    }
    request_recording_ids = {value.recording_id for value in request.candidates}
    if request_recording_ids != mandatory_recording_ids or any(
        value.semantic_id == UNKNOWN_SEMANTIC_ID for value in request.history
    ):
        raise ValueError("Sona quality materialization lacks complete tokenizer coverage")
    teacher_calibration_input = build_sona_p11_teacher_calibration_input(
        observation=inputs.observation,
        query=inputs.query,
        p11_pipeline=inputs.p11_pipeline,
        baseline=inputs.baseline,
        request=request,
        candidate_authority=authority,
    )

    rekey = derive_sona_source_request_rekey(
        inputs.observation,
        request,
        owner_lineage_hmac_key=owner_lineage_hmac_key,
    )
    return SonaPreparedSourceCase(
        temporal_input=temporal_input,
        request=request,
        outcome=replace(inputs.outcome, source_request_sha256=request.request_sha256),
        teacher_calibration_input=teacher_calibration_input,
        rekey=rekey,
    )


def build_sona_source_historical_inputs(
    record: SonaSourcePlanningRecord,
    *,
    evidence: tuple[TemporalEvidence, ...],
) -> SonaSourceHistoricalInputs:
    """Parse one exact planning row into teacher-independent historical inputs."""

    if canonical_sha256(record.request_document) != record.observation.request_sha256:
        raise ValueError("Sona historical request hash is not canonical")
    if canonical_sha256(record.snapshot_document) != record.input_snapshot_sha256:
        raise ValueError("Sona historical snapshot hash is not canonical")
    query_document = record.request_document
    query = RecommendationQuery(
        user_id=UUID(_source_string(query_document, "user_id")),
        surface=RecommendationSurface(_source_string(query_document, "surface")),
        context=_source_string(query_document, "context"),
        limit=_source_integer(query_document, "limit"),
        exploration=_source_number(query_document, "exploration"),
        seed=_source_integer(query_document, "seed"),
        schema_version=_source_integer(query_document, "schema_version"),
        canonicalization_version=_source_integer(
            query_document,
            "canonicalization_version",
        ),
        shadow=_source_boolean(query_document, "shadow"),
    )
    raw_tracks = record.snapshot_document.get("tracks")
    if not isinstance(raw_tracks, list):
        raise ValueError("Sona historical snapshot tracks are invalid")
    tracks = tuple(_source_snapshot_track(value) for value in raw_tracks)
    baseline = RecommendationInputSnapshot(
        reference=RecommendationSnapshotRef(
            snapshot_id=record.baseline_snapshot_id,
            input_snapshot_sha256=record.input_snapshot_sha256,
            interaction_watermark=record.interaction_watermark,
            catalog_snapshot=record.catalog_snapshot,
            availability_snapshot=record.availability_snapshot,
            policy_snapshot_sha256=record.policy_snapshot_sha256,
        ),
        tracks=tracks,
        retained_until=record.retained_until,
    )
    signal = classify_sona_0026_listening_values(
        played_ms=record.outcome_played_ms,
        completion_ratio=record.outcome_completion_ratio,
        excluded_from_taste=record.outcome_excluded_from_taste,
    )
    if signal is TemporalSignal.EXCLUDE_FROM_TASTE:
        raise ValueError("Sona historical outcome is excluded from taste")
    outcome = SonaObservedOutcome(
        owner_user_id=record.observation.owner_user_id,
        source_request_sha256=record.observation.request_sha256,
        recording_id=record.outcome_recording_id,
        observed_at_ms=record.observation.observed_at_ms,
        completion=float(signal is TemporalSignal.FINALIZED_COMPLETION),
        like=None,
        skip=float(signal is TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP),
        outcome_source_event_id=record.outcome_source_event_id,
        outcome_source_request_sha256=record.outcome_source_request_sha256,
    )
    inputs = SonaSourceHistoricalInputs(
        observation=record.observation,
        query=query,
        p11_pipeline=baseline_pipeline_definition(),
        baseline=baseline,
        evidence=evidence,
        outcome=outcome,
    )
    _verify_historical_inputs_ancestry(inputs)
    return inputs


def finalize_sona_prepared_source_case(
    prepared: SonaPreparedSourceCase,
    *,
    teacher: SonaTeacherSnapshot,
) -> SonaMaterializedSourceCase:
    """Bind a calibrated V2 teacher only after validation ancestry is frozen."""

    verify_sona_reconstructed_temporal_input(prepared.temporal_input)
    verify_sona_p11_teacher_calibration_input(prepared.teacher_calibration_input)
    verify_sona_teacher_snapshot(teacher)
    request_sha256 = canonical_sha256(sona_inference_request_document(prepared.request))
    candidate_ids = {value.recording_id for value in prepared.request.candidates}
    score_ids = {recording_id for recording_id, _ in prepared.teacher_calibration_input.scores}
    if (
        request_sha256 != prepared.request.request_sha256
        or prepared.request.temporal_snapshot_id != prepared.temporal_input.temporal_snapshot_id
        or prepared.teacher_calibration_input.p11_request_sha256
        != prepared.rekey.p11_request_sha256
        or prepared.teacher_calibration_input.sona_request_sha256 != prepared.request.request_sha256
        or prepared.rekey.sona_request_sha256 != prepared.request.request_sha256
        or prepared.rekey.cutoff_at_ms != prepared.request.cutoff_at_ms
        or prepared.rekey.observed_at_ms != prepared.outcome.observed_at_ms
        or prepared.outcome.owner_user_id != prepared.request.owner_user_id
        or prepared.outcome.source_request_sha256 != prepared.request.request_sha256
        or teacher.source_request_sha256 != prepared.request.request_sha256
        or candidate_ids != score_ids
    ):
        raise ValueError("Sona prepared source case integrity verification failed")
    example = build_sona_training_example(prepared.request, prepared.outcome, teacher)
    return SonaMaterializedSourceCase(
        temporal_input=prepared.temporal_input,
        request=prepared.request,
        outcome=prepared.outcome,
        teacher=teacher,
        teacher_calibration_input=prepared.teacher_calibration_input,
        rekey=prepared.rekey,
        example=example,
    )


def build_sona_p11_teacher_calibration_input(
    *,
    observation: SonaSourceRequestObservation,
    query: RecommendationQuery,
    p11_pipeline: PipelineDefinition,
    baseline: RecommendationInputSnapshot,
    request: SonaInferenceRequest,
    candidate_authority: RecommendationPipelineRunner | None = None,
) -> SonaP11TeacherCalibrationInput:
    """Capture exact P11 score inputs while keeping calibration explicitly unfit."""

    _verify_historical_request_ancestry(observation, query, p11_pipeline, baseline)
    frozen_p11 = baseline_pipeline_definition()
    if p11_pipeline != frozen_p11:
        raise ValueError("Sona teacher calibration requires the exact frozen P11 pipeline")
    request_document_sha256 = canonical_sha256(sona_inference_request_document(request))
    if (
        request_document_sha256 != request.request_sha256
        or request.owner_user_id != observation.owner_user_id
        or request.baseline_snapshot_id != baseline.reference.snapshot_id
        or request.cutoff_at_ms != observation.cutoff_at_ms
        or request.interaction_watermark != baseline.reference.interaction_watermark
        or request.seed != query.seed
    ):
        raise ValueError("Sona teacher calibration request ancestry is not canonical")

    authority = candidate_authority or RecommendationPipelineRunner()
    mandatory_ids = {
        value.recording_id for value in authority.filter_snapshot_tracks(query, baseline)
    }
    request_ids = {value.recording_id for value in request.candidates}
    scored = authority.candidate_pool(query, baseline, p11_pipeline)
    score_pairs = tuple(
        sorted(
            ((value.candidate.recording_id, value.score) for value in scored),
            key=lambda value: value[0].hex,
        )
    )
    scored_ids = tuple(recording_id for recording_id, _ in score_pairs)
    if (
        not score_pairs
        or len(scored_ids) != len(set(scored_ids))
        or set(scored_ids) != mandatory_ids
        or request_ids != mandatory_ids
    ):
        raise ValueError("P11 teacher scores do not cover the exact Sona candidate set")
    if any(not isfinite(score) for _, score in score_pairs):
        raise ValueError("P11 teacher scores contain a non-finite value")

    candidate_ids: list[JsonValue] = [str(recording_id) for recording_id in scored_ids]
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "artifact_kind": SONA_P11_TEACHER_CALIBRATION_INPUT_KIND,
        "p11_request_sha256": observation.request_sha256,
        "sona_request_sha256": request.request_sha256,
        "p11_pipeline_key": p11_pipeline.pipeline_key,
        "p11_pipeline_version": p11_pipeline.version,
        "p11_pipeline_manifest_sha256": p11_pipeline.manifest_sha256,
        "calibration_policy": SONA_P11_TEACHER_CALIBRATION_POLICY,
        "input_value_semantics": SONA_P11_TEACHER_INPUT_SEMANTICS,
        "candidate_count": len(score_pairs),
        "candidate_set_sha256": canonical_sha256(candidate_ids),
        "scores": cast(
            list[JsonValue],
            [
                {"recording_id": str(recording_id), "raw_p11_score": score}
                for recording_id, score in score_pairs
            ],
        ),
        "calibration_fit_bound": False,
        "quality_eligible": False,
        "blockers": ["TEACHER_CALIBRATION_PARAMETERS_NOT_BOUND"],
    }
    input_sha256 = canonical_sha256(document)
    return SonaP11TeacherCalibrationInput(
        p11_request_sha256=observation.request_sha256,
        sona_request_sha256=request.request_sha256,
        p11_pipeline_manifest_sha256=p11_pipeline.manifest_sha256,
        scores=score_pairs,
        document=document,
        input_sha256=input_sha256,
    )


def verify_sona_p11_teacher_calibration_input(
    value: SonaP11TeacherCalibrationInput,
) -> None:
    """Verify a transported unfit P11 calibration input without recomputing P11."""

    document = value.document
    documented_scores = document.get("scores")
    expected_scores: list[JsonValue] = [
        {"recording_id": str(recording_id), "raw_p11_score": score}
        for recording_id, score in value.scores
    ]
    candidate_ids: list[JsonValue] = [str(recording_id) for recording_id, _ in value.scores]
    if (
        document.get("artifact_kind") != SONA_P11_TEACHER_CALIBRATION_INPUT_KIND
        or document.get("p11_request_sha256") != value.p11_request_sha256
        or document.get("sona_request_sha256") != value.sona_request_sha256
        or document.get("p11_pipeline_manifest_sha256") != value.p11_pipeline_manifest_sha256
        or document.get("calibration_policy") != SONA_P11_TEACHER_CALIBRATION_POLICY
        or document.get("input_value_semantics") != SONA_P11_TEACHER_INPUT_SEMANTICS
        or document.get("candidate_count") != len(value.scores)
        or document.get("candidate_set_sha256") != canonical_sha256(candidate_ids)
        or documented_scores != expected_scores
        or document.get("calibration_fit_bound") is not False
        or document.get("quality_eligible") is not False
        or document.get("blockers") != ["TEACHER_CALIBRATION_PARAMETERS_NOT_BOUND"]
        or canonical_sha256(document) != value.input_sha256
    ):
        raise ValueError("Sona P11 teacher calibration input integrity verification failed")


def verify_sona_reconstructed_temporal_input(value: SonaReconstructedTemporalInput) -> None:
    """Verify a transported reconstruction identity without accessing owner-bearing evidence."""

    document = value.document
    declared_sha256 = document.get("temporal_snapshot_sha256")
    unhashed = {key: item for key, item in document.items() if key != "temporal_snapshot_sha256"}
    if (
        value.source_kind != SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1
        or document.get("source_kind") != value.source_kind
        or document.get("persisted_original_temporal_snapshot") is not False
        or document.get("normalization_policy") != SONA_0026_NORMALIZATION_POLICY
        or document.get("normalized_evidence_id_scheme") != SONA_0026_EVIDENCE_ID_SCHEME
        or document.get("temporal_snapshot_id") != str(value.temporal_snapshot_id)
        or document.get("source_evidence_sha256") != value.source_evidence_sha256
        or document.get("source_evidence_count") != value.source_evidence_count
        or declared_sha256 != value.temporal_snapshot_sha256
        or canonical_sha256(unhashed) != value.temporal_snapshot_sha256
    ):
        raise ValueError("Sona reconstructed temporal input integrity verification failed")


def _verify_historical_case_ancestry(case: SonaSourceHistoricalCase) -> None:
    observation = case.observation
    if (
        case.query.user_id != observation.owner_user_id
        or case.outcome.owner_user_id != observation.owner_user_id
        or case.outcome.source_request_sha256 != observation.request_sha256
        or case.outcome.observed_at_ms != observation.observed_at_ms
        or case.teacher.source_request_sha256 != observation.request_sha256
    ):
        raise ValueError("Sona historical case contains cross-owner or request lineage")
    _verify_historical_request_ancestry(
        observation,
        case.query,
        case.p11_pipeline,
        case.baseline,
    )
    verify_sona_teacher_snapshot(case.teacher)


def _verify_historical_inputs_ancestry(inputs: SonaSourceHistoricalInputs) -> None:
    observation = inputs.observation
    if (
        inputs.query.user_id != observation.owner_user_id
        or inputs.outcome.owner_user_id != observation.owner_user_id
        or inputs.outcome.source_request_sha256 != observation.request_sha256
        or inputs.outcome.observed_at_ms != observation.observed_at_ms
    ):
        raise ValueError("Sona historical inputs contain cross-owner or request lineage")
    _verify_historical_request_ancestry(
        observation,
        inputs.query,
        inputs.p11_pipeline,
        inputs.baseline,
    )


def _verify_historical_request_ancestry(
    observation: SonaSourceRequestObservation,
    query: RecommendationQuery,
    p11_pipeline: PipelineDefinition,
    baseline: RecommendationInputSnapshot,
) -> None:
    if query.user_id != observation.owner_user_id:
        raise ValueError("Sona historical request contains a cross-owner lineage")
    reference = baseline.reference
    snapshot_document = recommendation_input_snapshot_document(
        observation.owner_user_id,
        interaction_watermark=reference.interaction_watermark,
        tracks=baseline.tracks,
    )
    if sha256(rfc8785.dumps(snapshot_document)).hexdigest() != reference.input_snapshot_sha256:
        raise ValueError("Sona historical baseline snapshot hash is not canonical")
    availability_document = recommendation_availability_snapshot_document(baseline.tracks)
    policy_document = recommendation_policy_snapshot_document(baseline.tracks)
    catalog_snapshot = max(
        (max(value.added_at_ms, value.last_played_at_ms or 0) for value in baseline.tracks),
        default=0,
    )
    if (
        sha256(rfc8785.dumps(availability_document)).hexdigest() != reference.availability_snapshot
        or sha256(rfc8785.dumps(policy_document)).hexdigest() != reference.policy_snapshot_sha256
        or catalog_snapshot != reference.catalog_snapshot
    ):
        raise ValueError("Sona historical baseline authority hashes are not canonical")
    p11_document = request_document(query, p11_pipeline, reference)
    if canonical_sha256(p11_document) != observation.request_sha256:
        raise ValueError("Sona historical P11 request hash is not canonical")


def _source_snapshot_track(value: object) -> SnapshotTrack:
    document = _source_object(value, "track")
    raw_ref = document.get("user_track_ref_id")
    raw_release = document.get("release_key")
    raw_tokens = document.get("metadata_tokens")
    if raw_ref is not None and not isinstance(raw_ref, str):
        raise ValueError("Sona historical track reference is invalid")
    if raw_release is not None and not isinstance(raw_release, str):
        raise ValueError("Sona historical release key is invalid")
    if not isinstance(raw_tokens, list) or not all(isinstance(token, str) for token in raw_tokens):
        raise ValueError("Sona historical metadata tokens are invalid")
    return SnapshotTrack(
        recording_id=UUID(_source_string(document, "recording_id")),
        user_track_ref_id=None if raw_ref is None else UUID(raw_ref),
        artist_key=_source_string(document, "artist_key"),
        release_key=raw_release,
        metadata_tokens=tuple(cast(list[str], raw_tokens)),
        availability=_source_string(document, "availability"),
        authorized=_source_boolean(document, "authorized"),
        identity_status=_source_string(document, "identity_status"),
        preference=_source_string(document, "preference"),
        excluded=_source_boolean(document, "excluded"),
        play_count=_source_integer(document, "play_count"),
        organic_play_count=_source_integer(document, "organic_play_count"),
        recommended_play_count=_source_integer(document, "recommended_play_count"),
        last_played_at_ms=_source_optional_integer(document, "last_played_at_ms"),
        added_at_ms=_source_integer(document, "added_at_ms"),
        release_date_ordinal=_source_optional_integer(document, "release_date_ordinal"),
    )


def _source_object(value: object, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"Sona historical {field} is invalid")
    return cast(dict[str, JsonValue], value)


def _source_string(value: Mapping[str, JsonValue], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ValueError(f"Sona historical {field} is invalid")
    return candidate


def _source_integer(value: Mapping[str, JsonValue], field: str) -> int:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(f"Sona historical {field} is invalid")
    return candidate


def _source_optional_integer(value: Mapping[str, JsonValue], field: str) -> int | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    return _source_integer(value, field)


def _source_number(value: Mapping[str, JsonValue], field: str) -> float:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int | float):
        raise ValueError(f"Sona historical {field} is invalid")
    return float(candidate)


def _source_boolean(value: Mapping[str, JsonValue], field: str) -> bool:
    candidate = value.get(field)
    if not isinstance(candidate, bool):
        raise ValueError(f"Sona historical {field} is invalid")
    return candidate


__all__ = (
    "SONA_P11_TEACHER_CALIBRATION_INPUT_KIND",
    "SONA_P11_TEACHER_CALIBRATION_POLICY",
    "SONA_P11_TEACHER_INPUT_SEMANTICS",
    "SONA_TEMPORAL_RECONSTRUCTION_KIND",
    "SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1",
    "SonaMaterializedSourceCase",
    "SonaP11TeacherCalibrationInput",
    "SonaPreparedSourceCase",
    "SonaReconstructedTemporalInput",
    "SonaSourceHistoricalCase",
    "SonaSourceHistoricalInputs",
    "build_sona_p11_teacher_calibration_input",
    "build_sona_source_historical_inputs",
    "finalize_sona_prepared_source_case",
    "materialize_sona_source_historical_case",
    "prepare_sona_source_historical_inputs",
    "reconstruct_sona_temporal_input",
    "verify_sona_p11_teacher_calibration_input",
    "verify_sona_reconstructed_temporal_input",
)
