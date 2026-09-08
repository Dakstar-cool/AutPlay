"""Sona shadow evidence remains separate from P11 truth and replays exact hashes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest
import rfc8785
from autplay.application.recommendations import baseline_pipeline_definition, request_document
from autplay.application.sona import SonaShadowService, build_sona_inference_request
from autplay.application.sona_evaluation import (
    AttributedOutcome,
    build_paired_ranking_case,
    compute_recommendation_ranking_sha256,
)
from autplay.application.sona_shadow import (
    SonaShadowCoordinator,
    sona_shadow_evidence_document,
    sona_shadow_evidence_from_document,
)
from autplay.domain.adaptive_recommendations import (
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
)
from autplay.domain.recommendations import (
    CandidateContribution,
    JsonValue,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    ReplayInputUnavailable,
    SnapshotTrack,
)
from autplay.domain.sona import (
    SonaGeneratedCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
    SonaShadowEvidence,
    SonaShadowStatus,
    SonaTemporalSnapshot,
)

OWNER = UUID("00000000-0000-7000-8000-000000000001")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000000002")
BASELINE_ID = UUID("00000000-0000-7000-8000-000000000003")
TEMPORAL_ID = UUID("00000000-0000-7000-8000-000000000004")
PROFILE = UUID("00000000-0000-7000-8000-000000000005")
DEVICE = UUID("00000000-0000-7000-8000-000000000006")
TRACK = UUID("00000000-0000-7000-8000-000000000007")
EVENT = UUID("00000000-0000-7000-8000-000000000008")
SOURCE = UUID("00000000-0000-7000-8000-000000000009")
HASH = "a" * 64
TOKENIZER = "b" * 64
TOKENIZER_MANIFEST = "d" * 64
MODEL = "c" * 64
CUTOFF_MS = 1_788_375_600_000
NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def _baseline() -> RecommendationInputSnapshot:
    return RecommendationInputSnapshot(
        RecommendationSnapshotRef(BASELINE_ID, HASH, 1, 1, HASH, HASH),
        (
            SnapshotTrack(
                TRACK,
                None,
                "artist",
                None,
                (),
                "VAULT",
                True,
                "ACTIVE",
                "NONE",
                False,
                0,
                0,
                0,
                None,
                CUTOFF_MS,
                None,
            ),
        ),
        NOW + timedelta(days=30),
    )


def _event() -> TemporalEvidence:
    return TemporalEvidence(
        evidence_id=EVENT,
        source_event_id=SOURCE,
        owner_user_id=OWNER,
        server_profile_id=PROFILE,
        device_id=DEVICE,
        device_sequence=1,
        server_sequence=1,
        source_event_type="LISTENING_EVENT_RECORDED",
        signal=TemporalSignal.FINALIZED_ORGANIC_LISTEN,
        derivation_key="BASE_LISTEN_V1",
        recording_id=TRACK,
        dimensions=(TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, "artist"),),
        occurred_at_ms=CUTOFF_MS - 1_000,
        received_at_ms=CUTOFF_MS - 500,
        effective_at_ms=CUTOFF_MS - 1_000,
        time_classification=TimeClassification.TRUSTED_EVENT_TIME,
        origin_lane=OriginLane.ORGANIC,
        signed_strength=0.5,
        quality_weight=1.0,
        excluded_from_taste=False,
        source_request_sha256=HASH,
    )


def _temporal() -> SonaTemporalSnapshot:
    return SonaTemporalSnapshot(
        OWNER,
        TEMPORAL_ID,
        "d" * 64,
        "e" * 64,
        BASELINE_ID,
        HASH,
        CUTOFF_MS,
        1,
        1,
        HASH,
        (_event(),),
        NOW + timedelta(days=30),
    )


def _response() -> RecommendationResponse:
    query = RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, seed=17)
    pipeline = baseline_pipeline_definition()
    reference = _baseline().reference
    document = request_document(query, pipeline, reference)
    trace = RecommendationRequestTrace(
        REQUEST_ID,
        query,
        pipeline,
        reference,
        sha256(rfc8785.dumps(document)).hexdigest(),
        document,
        NOW,
    )
    return RecommendationResponse(trace, ())


class _SemanticIds:
    tokenizer_manifest_sha256 = TOKENIZER_MANIFEST

    def load(
        self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
    ) -> Mapping[UUID, SonaSemanticId]:
        assert tokenizer_sha256 == TOKENIZER
        return {recording_id: SonaSemanticId(1, 1, 1) for recording_id in recording_ids}

    def expand(
        self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
    ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
        assert tokenizer_sha256 == TOKENIZER
        semantic_id = SonaSemanticId(1, 1, 1)
        return {semantic_id: (TRACK,)} if semantic_id in semantic_ids else {}


class _Inference:
    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        return SonaInferenceOutput(
            request.request_sha256,
            (SonaGeneratedCandidate(SonaSemanticId(1, 1, 1), -0.1),),
            (SonaRankedCandidate(TRACK, (0.1, 0.2, 0.3, 0.4), 0.5),),
        )


class _FailingInference:
    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        del request
        raise RuntimeError("fixture unavailable")


class _Snapshots:
    snapshot: RecommendationInputSnapshot | None = _baseline()

    def capture(self, user_id: UUID, *, retained_until: datetime) -> RecommendationInputSnapshot:
        del user_id, retained_until
        raise AssertionError("shadow replay must not capture current state")

    def load(self, user_id: UUID, snapshot_id: UUID) -> RecommendationInputSnapshot | None:
        if user_id == OWNER and snapshot_id == BASELINE_ID:
            return self.snapshot
        return None


class _Traces:
    def __init__(self) -> None:
        self.trace = _response().request
        self.ensured: list[str] = []

    def request(self, user_id: UUID, request_id: UUID) -> RecommendationRequestTrace | None:
        return self.trace if (user_id, request_id) == (OWNER, REQUEST_ID) else None

    def exact(self, user_id: UUID, request_id: UUID) -> RecommendationResponse | None:
        return _response() if (user_id, request_id) == (OWNER, REQUEST_ID) else None

    def save(self, response: RecommendationResponse) -> None:
        del response
        raise AssertionError("shadow coordinator must not save P11 responses")

    def ensure_pipeline(self, pipeline: PipelineDefinition) -> None:
        self.ensured.append(pipeline.manifest_sha256)


class _TemporalSnapshots:
    snapshot: SonaTemporalSnapshot | None = _temporal()

    def load_sona_snapshot(
        self, user_id: UUID, temporal_snapshot_id: UUID
    ) -> SonaTemporalSnapshot | None:
        if (user_id, temporal_snapshot_id) == (OWNER, TEMPORAL_ID):
            return self.snapshot
        return None


class _Evidence:
    value: SonaShadowEvidence | None = None

    def save(self, evidence: SonaShadowEvidence) -> None:
        if self.value is not None and self.value != evidence:
            raise ValueError("identity conflict")
        self.value = evidence

    def load(self, user_id: UUID, recommendation_request_id: UUID) -> SonaShadowEvidence | None:
        if (user_id, recommendation_request_id) == (OWNER, REQUEST_ID):
            return self.value
        return None


def _coordinator(
    inference: _Inference | _FailingInference | None = None,
) -> tuple[SonaShadowCoordinator, _Snapshots, _TemporalSnapshots, _Evidence]:
    snapshots = _Snapshots()
    temporal = _TemporalSnapshots()
    evidence = _Evidence()
    service = SonaShadowService(
        _SemanticIds(),
        inference or _Inference(),
        tokenizer_sha256=TOKENIZER,
        model_manifest_sha256=MODEL,
    )
    return (
        SonaShadowCoordinator(
            service=service,
            snapshots=snapshots,
            traces=_Traces(),
            temporal_snapshots=temporal,
            evidence=evidence,
        ),
        snapshots,
        temporal,
        evidence,
    )


def test_shadow_run_persists_hashes_and_algorithmic_replay_is_exact() -> None:
    coordinator, _, _, evidence = _coordinator()
    p11 = _response()

    stored = coordinator.run(p11, _temporal())
    replayed = coordinator.algorithmic_replay(OWNER, REQUEST_ID)

    assert stored == replayed == evidence.value
    assert stored.status is SonaShadowStatus.SUCCEEDED
    assert stored.output is not None
    assert stored.sona_request_sha256 == stored.output.request_sha256
    assert stored.sona_output_sha256 is not None
    assert stored.postprocessed_ranking_sha256 is not None
    assert stored.generated_recording_ids == (TRACK,)
    assert stored.generated_expansion == ((SonaSemanticId(1, 1, 1), (TRACK,)),)
    assert tuple(item.recording_id for item in stored.postprocessed_ranking) == (TRACK,)
    assert p11 == _response()
    assert p11.items == ()


def test_evaluation_case_is_derived_from_bound_p11_and_persisted_shadow_evidence() -> None:
    coordinator, _, _, _ = _coordinator()
    p11 = _response()
    served = replace(
        p11,
        items=(
            RankedRecommendation(
                TRACK,
                1,
                0.25,
                "P11_TEST",
                ("P11_TEST",),
                (CandidateContribution("p11-test", "1", 1, 0.25),),
                "artist",
                None,
            ),
        ),
    )
    stored = coordinator.run(served, _temporal())
    sona_request = build_sona_inference_request(
        replace(p11.request.query, shadow=True),
        _baseline(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(),),
        semantic_ids={TRACK: SonaSemanticId(1, 1, 1)},
        tokenizer_sha256=TOKENIZER,
        model_manifest_sha256=MODEL,
    )
    outcome = AttributedOutcome(
        event_sha256="1" * 64,
        recording_id=TRACK,
        signal="selection",
        source_request_sha256=sona_request.request_sha256,
        source_rank=1,
        observed_at_ms=CUTOFF_MS + 1,
    )

    case = build_paired_ranking_case(
        case_id="owner-time-split-1",
        owner_lineage_token="2" * 64,
        baseline_response=served,
        baseline_snapshot=_baseline(),
        sona_request=sona_request,
        shadow_evidence=stored,
        outcome_documents=(
            {
                "schema_version": 1,
                "kind": "SONA_ATTRIBUTED_OUTCOME_V1",
                "recording_id": str(outcome.recording_id),
                "signal": outcome.signal,
                "source_request_sha256": outcome.source_request_sha256,
                "source_rank": outcome.source_rank,
                "observed_at_ms": outcome.observed_at_ms,
            },
        ),
    )

    assert case.p11_ranking_sha256 == compute_recommendation_ranking_sha256(served.items)
    assert case.sona_postprocessed_ranking_sha256 == stored.postprocessed_ranking_sha256
    assert case.sona_generated_ids == stored.generated_recording_ids


def test_replay_never_substitutes_current_or_missing_snapshots() -> None:
    coordinator, snapshots, temporal, _ = _coordinator()
    coordinator.run(_response(), _temporal())

    snapshots.snapshot = None
    with pytest.raises(ReplayInputUnavailable):
        coordinator.algorithmic_replay(OWNER, REQUEST_ID)

    snapshots.snapshot = _baseline()
    temporal.snapshot = None
    with pytest.raises(ReplayInputUnavailable):
        coordinator.algorithmic_replay(OWNER, REQUEST_ID)


def test_gpu_failure_persists_degraded_evidence_without_changing_p11() -> None:
    coordinator, _, _, _ = _coordinator(_FailingInference())
    p11 = _response()

    stored = coordinator.run(p11, _temporal())

    assert stored.status is SonaShadowStatus.DEGRADED
    assert stored.reason == "SONA_UNAVAILABLE"
    assert stored.output is None and stored.sona_output_sha256 is None
    assert stored.postprocessed_ranking == () and stored.postprocessed_ranking_sha256 is None
    assert stored.sona_request_sha256 is not None
    assert p11 == _response()


def test_replay_rejects_changed_model_identity_before_inference() -> None:
    coordinator, snapshots, temporal, evidence = _coordinator()
    coordinator.run(_response(), _temporal())

    class ForbiddenInference:
        def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
            del request
            raise AssertionError("identity mismatch must fail before inference")

    changed_service = SonaShadowService(
        _SemanticIds(),
        ForbiddenInference(),
        tokenizer_sha256=TOKENIZER,
        model_manifest_sha256="e" * 64,
    )
    changed = SonaShadowCoordinator(
        service=changed_service,
        snapshots=snapshots,
        traces=_Traces(),
        temporal_snapshots=temporal,
        evidence=evidence,
    )

    with pytest.raises(ReplayInputUnavailable):
        changed.algorithmic_replay(OWNER, REQUEST_ID)


def test_evidence_parser_rejects_top_level_output_hash_mismatch() -> None:
    coordinator, _, _, _ = _coordinator()
    stored = coordinator.run(_response(), _temporal())
    document = sona_shadow_evidence_document(stored)
    document["sona_output_sha256"] = "e" * 64
    unhashed = cast(
        dict[str, JsonValue],
        {key: value for key, value in document.items() if key != "evidence_sha256"},
    )
    document["evidence_sha256"] = sha256(rfc8785.dumps(unhashed)).hexdigest()

    with pytest.raises(ValueError, match="output hash binding mismatch"):
        sona_shadow_evidence_from_document(cast(dict[str, object], document))

    malformed = replace(stored, sona_output_sha256="e" * 64)
    with pytest.raises(ValueError, match="output hash binding mismatch"):
        sona_shadow_evidence_document(malformed)

    malformed_ranking = replace(stored, postprocessed_ranking_sha256="e" * 64)
    with pytest.raises(ValueError, match="postprocessed ranking hash binding mismatch"):
        sona_shadow_evidence_document(malformed_ranking)
