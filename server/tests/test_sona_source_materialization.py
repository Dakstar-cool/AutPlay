"""Pure 0026 historical-case reconstruction and P11-to-Sona re-key tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
import rfc8785
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    request_document,
)
from autplay.application.sona_source_materialization import (
    SONA_P11_TEACHER_CALIBRATION_INPUT_KIND,
    SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1,
    SonaSourceHistoricalCase,
    SonaSourceHistoricalInputs,
    build_sona_p11_teacher_calibration_input,
    build_sona_source_historical_inputs,
    finalize_sona_prepared_source_case,
    materialize_sona_source_historical_case,
    prepare_sona_source_historical_inputs,
    reconstruct_sona_temporal_input,
    verify_sona_p11_teacher_calibration_input,
    verify_sona_reconstructed_temporal_input,
)
from autplay.application.sona_source_planning import (
    SonaSourcePlanningRecord,
    SonaSourceRequestObservation,
)
from autplay.application.sona_source_reconstruction import (
    SONA_0026_EVIDENCE_ID_SCHEME,
    SONA_0026_NORMALIZATION_POLICY,
)
from autplay.application.sona_training import build_sona_teacher_snapshot
from autplay.domain.adaptive_recommendations import (
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
)
from autplay.domain.recommendations import (
    PipelineDefinition,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    ScoredCandidate,
    SnapshotTrack,
)
from autplay.domain.sona import SonaSemanticId
from autplay.domain.sona_training import SonaObservedOutcome, SonaTeacherTarget

OWNER = UUID("00000000-0000-7000-8000-000000000001")
OTHER = UUID("00000000-0000-7000-8000-000000000002")
BASELINE_ID = UUID("00000000-0000-7000-8000-000000000101")
PROFILE = UUID("00000000-0000-7000-8000-000000000201")
DEVICE = UUID("00000000-0000-7000-8000-000000000202")
TRACK_A = UUID("00000000-0000-7000-8000-000000000301")
TRACK_B = UUID("00000000-0000-7000-8000-000000000302")
TRACK_C = UUID("00000000-0000-7000-8000-000000000303")
CUTOFF_MS = 1_788_375_600_000
TOKENIZER_SHA256 = "b" * 64
MODEL_MANIFEST_SHA256 = "c" * 64
OWNER_LINEAGE_KEY = b"owner-lineage-test-key-material-32"


def _track(recording_id: UUID) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=recording_id,
        user_track_ref_id=None,
        artist_key=f"artist-{recording_id.int}",
        release_key=None,
        metadata_tokens=(),
        availability="VAULT",
        authorized=True,
        identity_status="ACTIVE",
        preference="NONE",
        excluded=False,
        play_count=1,
        organic_play_count=1,
        recommended_play_count=0,
        last_played_at_ms=CUTOFF_MS - 10_000,
        added_at_ms=CUTOFF_MS - 20_000,
        release_date_ordinal=None,
    )


def _event(index: int, recording_id: UUID) -> TemporalEvidence:
    return TemporalEvidence(
        evidence_id=UUID(f"00000000-0000-7000-9000-{index:012x}"),
        source_event_id=UUID(f"00000000-0000-7000-8000-{index:012x}"),
        owner_user_id=OWNER,
        server_profile_id=PROFILE,
        device_id=DEVICE,
        device_sequence=index,
        server_sequence=index,
        source_event_type="LISTENING_EVENT_RECORDED",
        signal=TemporalSignal.FINALIZED_ORGANIC_LISTEN,
        derivation_key="BASE_LISTEN_V1",
        recording_id=recording_id,
        dimensions=(
            TemporalDimension(
                DimensionKind.CANONICAL_ARTIST_ID,
                f"artist-{recording_id.int}",
            ),
        ),
        occurred_at_ms=CUTOFF_MS - index * 1_000,
        received_at_ms=CUTOFF_MS - 500,
        effective_at_ms=CUTOFF_MS - index * 1_000,
        time_classification=TimeClassification.TRUSTED_EVENT_TIME,
        origin_lane=OriginLane.ORGANIC,
        signed_strength=0.5,
        quality_weight=1.0,
        excluded_from_taste=False,
        source_request_sha256=f"{index + 100:064x}",
    )


def _case(*, evidence: tuple[TemporalEvidence, ...] | None = None) -> SonaSourceHistoricalCase:
    tracks = (_track(TRACK_B), _track(TRACK_A))
    snapshot_document = recommendation_input_snapshot_document(
        OWNER,
        interaction_watermark=10,
        tracks=tracks,
    )
    availability_document = recommendation_availability_snapshot_document(tracks)
    policy_document = recommendation_policy_snapshot_document(tracks)
    reference = RecommendationSnapshotRef(
        snapshot_id=BASELINE_ID,
        input_snapshot_sha256=sha256(rfc8785.dumps(snapshot_document)).hexdigest(),
        interaction_watermark=10,
        catalog_snapshot=CUTOFF_MS - 10_000,
        availability_snapshot=sha256(rfc8785.dumps(availability_document)).hexdigest(),
        policy_snapshot_sha256=sha256(rfc8785.dumps(policy_document)).hexdigest(),
    )
    baseline = RecommendationInputSnapshot(
        reference=reference,
        tracks=tracks,
        retained_until=datetime.now(UTC) + timedelta(days=1),
    )
    query = RecommendationQuery(
        OWNER,
        RecommendationSurface.RECOMMENDATIONS,
        seed=17,
    )
    pipeline = baseline_pipeline_definition()
    p11_request_sha256 = sha256(
        rfc8785.dumps(request_document(query, pipeline, reference))
    ).hexdigest()
    observed_at_ms = CUTOFF_MS + 1_000
    teacher = build_sona_teacher_snapshot(
        source_request_sha256=p11_request_sha256,
        teacher_key="p11-calibrated",
        teacher_version="1",
        teacher_manifest_sha256="d" * 64,
        targets=(
            SonaTeacherTarget(TRACK_B, (0.9, 0.8, 0.1, 0.9)),
            SonaTeacherTarget(TRACK_A, (0.4, 0.3, 0.2, 0.4)),
        ),
    )
    return SonaSourceHistoricalCase(
        observation=SonaSourceRequestObservation(
            request_sha256=p11_request_sha256,
            owner_user_id=OWNER,
            cutoff_at_ms=CUTOFF_MS,
            observed_at_ms=observed_at_ms,
        ),
        query=query,
        p11_pipeline=pipeline,
        baseline=baseline,
        evidence=evidence or (_event(2, TRACK_B), _event(1, TRACK_A)),
        outcome=SonaObservedOutcome(
            owner_user_id=OWNER,
            source_request_sha256=p11_request_sha256,
            recording_id=TRACK_B,
            observed_at_ms=observed_at_ms,
            completion=1.0,
            like=1.0,
            skip=0.0,
        ),
        teacher=teacher,
    )


def _semantic_ids() -> dict[UUID, SonaSemanticId]:
    return {
        TRACK_A: SonaSemanticId(1, 2, 3),
        TRACK_B: SonaSemanticId(4, 5, 6),
    }


def _planning_record(
    case: SonaSourceHistoricalCase,
    *,
    played_ms: int = 180_000,
    completion_ratio: float | None = 0.9,
) -> SonaSourcePlanningRecord:
    return SonaSourcePlanningRecord(
        observation=case.observation,
        request_document=request_document(case.query, case.p11_pipeline, case.baseline.reference),
        snapshot_document=recommendation_input_snapshot_document(
            OWNER,
            interaction_watermark=case.baseline.reference.interaction_watermark,
            tracks=case.baseline.tracks,
        ),
        baseline_snapshot_id=case.baseline.reference.snapshot_id,
        input_snapshot_sha256=case.baseline.reference.input_snapshot_sha256,
        interaction_watermark=case.baseline.reference.interaction_watermark,
        catalog_snapshot=case.baseline.reference.catalog_snapshot,
        availability_snapshot=case.baseline.reference.availability_snapshot,
        policy_snapshot_sha256=case.baseline.reference.policy_snapshot_sha256,
        retained_until=case.baseline.retained_until,
        outcome_source_event_id=UUID("00000000-0000-7000-8000-000000000901"),
        outcome_source_request_sha256="9" * 64,
        outcome_recording_id=TRACK_B,
        outcome_played_ms=played_ms,
        outcome_completion_ratio=completion_ratio,
        outcome_excluded_from_taste=False,
    )


def test_planning_record_builds_exact_teacher_independent_inputs() -> None:
    case = _case()
    inputs = build_sona_source_historical_inputs(
        _planning_record(case),
        evidence=case.evidence,
    )

    assert inputs.query == case.query
    assert inputs.p11_pipeline == case.p11_pipeline
    assert inputs.baseline == case.baseline
    assert inputs.evidence == case.evidence
    assert inputs.outcome.completion == 1.0
    assert inputs.outcome.skip == 0.0
    assert inputs.outcome.like is None
    assert inputs.outcome.outcome_source_event_id == UUID("00000000-0000-7000-8000-000000000901")
    assert inputs.outcome.outcome_source_request_sha256 == "9" * 64


def test_planning_record_uses_frozen_skip_boundaries_and_rejects_snapshot_tampering() -> None:
    case = _case()
    skipped = build_sona_source_historical_inputs(
        _planning_record(case, played_ms=29_999, completion_ratio=0.9),
        evidence=case.evidence,
    )
    assert skipped.outcome.completion == 0.0
    assert skipped.outcome.skip == 1.0

    record = _planning_record(case)
    with pytest.raises(ValueError, match="snapshot hash"):
        build_sona_source_historical_inputs(
            replace(
                record,
                snapshot_document={**record.snapshot_document, "selection_policy": "tampered"},
            ),
            evidence=case.evidence,
        )


def test_materialization_is_order_independent_and_preserves_p11_lineage() -> None:
    first_case = _case()
    second_case = replace(first_case, evidence=tuple(reversed(first_case.evidence)))

    first = materialize_sona_source_historical_case(
        first_case,
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )
    second = materialize_sona_source_historical_case(
        second_case,
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )

    assert first == second
    assert first.temporal_input.source_kind == (
        SONA_TEMPORAL_SOURCE_KIND_RECONSTRUCTED_0026_SYNC_V1
    )
    assert first.temporal_input.document["persisted_original_temporal_snapshot"] is False
    assert first.temporal_input.document["normalization_policy"] == (SONA_0026_NORMALIZATION_POLICY)
    assert first.temporal_input.document["normalized_evidence_id_scheme"] == (
        SONA_0026_EVIDENCE_ID_SCHEME
    )
    assert first.request.request_sha256 != first_case.observation.request_sha256
    assert first.rekey.p11_request_sha256 == first_case.observation.request_sha256
    assert first.rekey.sona_request_sha256 == first.request.request_sha256
    assert first.outcome.source_request_sha256 == first.request.request_sha256
    assert first.teacher.source_request_sha256 == first.request.request_sha256
    assert first.teacher_calibration_input.document["artifact_kind"] == (
        SONA_P11_TEACHER_CALIBRATION_INPUT_KIND
    )
    assert first.teacher_calibration_input.document["calibration_fit_bound"] is False
    assert first.teacher_calibration_input.document["quality_eligible"] is False
    assert first.teacher_calibration_input.document["blockers"] == [
        "TEACHER_CALIBRATION_PARAMETERS_NOT_BOUND"
    ]
    assert first.example.request == first.request
    verify_sona_p11_teacher_calibration_input(first.teacher_calibration_input)
    verify_sona_reconstructed_temporal_input(first.temporal_input)


def test_prepare_then_finalize_breaks_the_validation_calibration_cycle() -> None:
    case = _case()
    prepared = prepare_sona_source_historical_inputs(
        SonaSourceHistoricalInputs(
            observation=case.observation,
            query=case.query,
            p11_pipeline=case.p11_pipeline,
            baseline=case.baseline,
            evidence=case.evidence,
            outcome=case.outcome,
        ),
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )

    assert prepared.teacher_calibration_input.sona_request_sha256 == (
        prepared.request.request_sha256
    )
    assert prepared.outcome.source_request_sha256 == prepared.request.request_sha256
    with pytest.raises(ValueError, match="prepared source case integrity"):
        finalize_sona_prepared_source_case(prepared, teacher=case.teacher)

    calibrated_teacher = build_sona_teacher_snapshot(
        source_request_sha256=prepared.request.request_sha256,
        teacher_key="p11-calibrated-v2",
        teacher_version="2",
        teacher_manifest_sha256="e" * 64,
        targets=case.teacher.targets,
    )
    finalized = finalize_sona_prepared_source_case(
        prepared,
        teacher=calibrated_teacher,
    )

    assert finalized.teacher == calibrated_teacher
    assert finalized.example.teacher_snapshot_sha256 == calibrated_teacher.snapshot_sha256


def test_finalize_rejects_tampered_prepared_case() -> None:
    case = _case()
    prepared = prepare_sona_source_historical_inputs(
        SonaSourceHistoricalInputs(
            observation=case.observation,
            query=case.query,
            p11_pipeline=case.p11_pipeline,
            baseline=case.baseline,
            evidence=case.evidence,
            outcome=case.outcome,
        ),
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )
    teacher = build_sona_teacher_snapshot(
        source_request_sha256=prepared.request.request_sha256,
        teacher_key="p11-calibrated-v2",
        teacher_version="2",
        teacher_manifest_sha256="e" * 64,
        targets=case.teacher.targets,
    )

    with pytest.raises(ValueError, match="prepared source case integrity"):
        finalize_sona_prepared_source_case(
            replace(prepared, rekey=replace(prepared.rekey, observed_at_ms=CUTOFF_MS + 2_000)),
            teacher=teacher,
        )


class _IncompleteP11Authority(RecommendationPipelineRunner):
    def candidate_pool(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        pipeline: PipelineDefinition,
    ) -> tuple[ScoredCandidate, ...]:
        return super().candidate_pool(query, snapshot, pipeline)[:-1]


def test_teacher_calibration_input_requires_exact_p11_candidate_coverage() -> None:
    case = _case()
    materialized = materialize_sona_source_historical_case(
        case,
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )

    with pytest.raises(ValueError, match="exact Sona candidate set"):
        build_sona_p11_teacher_calibration_input(
            observation=case.observation,
            query=case.query,
            p11_pipeline=case.p11_pipeline,
            baseline=case.baseline,
            request=materialized.request,
            candidate_authority=_IncompleteP11Authority(),
        )


def test_teacher_calibration_input_integrity_rejects_fitted_status_tampering() -> None:
    materialized = materialize_sona_source_historical_case(
        _case(),
        semantic_ids=_semantic_ids(),
        tokenizer_sha256=TOKENIZER_SHA256,
        model_manifest_sha256=MODEL_MANIFEST_SHA256,
        owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
    )
    calibration_input = materialized.teacher_calibration_input
    tampered = replace(
        calibration_input,
        document={**calibration_input.document, "calibration_fit_bound": True},
    )

    with pytest.raises(ValueError, match="integrity verification"):
        verify_sona_p11_teacher_calibration_input(tampered)


@pytest.mark.parametrize(
    ("case", "semantic_ids"),
    (
        (_case(), {TRACK_A: SonaSemanticId(1, 2, 3)}),
        (
            _case(evidence=(_event(1, TRACK_A), _event(2, TRACK_C))),
            _semantic_ids(),
        ),
    ),
)
def test_materialization_rejects_incomplete_candidate_or_history_tokenizer_coverage(
    case: SonaSourceHistoricalCase,
    semantic_ids: dict[UUID, SonaSemanticId],
) -> None:
    with pytest.raises(ValueError, match="complete tokenizer coverage"):
        materialize_sona_source_historical_case(
            case,
            semantic_ids=semantic_ids,
            tokenizer_sha256=TOKENIZER_SHA256,
            model_manifest_sha256=MODEL_MANIFEST_SHA256,
            owner_lineage_hmac_key=OWNER_LINEAGE_KEY,
        )


def test_reconstruction_rejects_cross_owner_and_tampered_source_ancestry() -> None:
    case = _case()
    with pytest.raises(ValueError, match="cross-owner or request lineage"):
        reconstruct_sona_temporal_input(
            replace(case, outcome=replace(case.outcome, owner_user_id=OTHER))
        )
    with pytest.raises(ValueError, match="baseline snapshot hash"):
        reconstruct_sona_temporal_input(
            replace(
                case,
                baseline=replace(
                    case.baseline,
                    tracks=(*case.baseline.tracks, _track(TRACK_C)),
                ),
            )
        )
    with pytest.raises(ValueError, match="baseline authority hashes"):
        reconstruct_sona_temporal_input(
            replace(
                case,
                baseline=replace(
                    case.baseline,
                    reference=replace(
                        case.baseline.reference,
                        availability_snapshot="e" * 64,
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="teacher snapshot hash"):
        reconstruct_sona_temporal_input(
            replace(
                case,
                teacher=replace(case.teacher, targets=tuple(reversed(case.teacher.targets))),
            )
        )


def test_reconstruction_rejects_duplicate_or_conflicting_normalized_evidence() -> None:
    case = _case()
    with pytest.raises(ValueError, match="duplicate identities"):
        reconstruct_sona_temporal_input(
            replace(case, evidence=(case.evidence[0], case.evidence[0]))
        )
    with pytest.raises(ValueError, match="source hash conflicts"):
        reconstruct_sona_temporal_input(
            replace(
                case,
                evidence=(
                    case.evidence[0],
                    replace(
                        case.evidence[1],
                        source_event_id=case.evidence[0].source_event_id,
                        source_request_sha256="f" * 64,
                    ),
                ),
            )
        )


def test_reconstruction_integrity_rejects_a_persisted_snapshot_claim() -> None:
    reconstructed = reconstruct_sona_temporal_input(_case())
    tampered = replace(
        reconstructed,
        document={
            **reconstructed.document,
            "persisted_original_temporal_snapshot": True,
        },
    )

    with pytest.raises(ValueError, match="integrity verification"):
        verify_sona_reconstructed_temporal_input(tampered)
