"""Sona-Lite request selection, hashing and shadow fail-closed evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from autplay.application.recommendations import baseline_pipeline_definition
from autplay.application.sona import (
    SonaShadowService,
    build_sona_inference_request,
    sona_shadow_pipeline_definition,
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
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import (
    SonaGeneratedCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
)

OWNER = UUID("00000000-0000-7000-8000-000000000001")
OTHER = UUID("00000000-0000-7000-8000-000000000002")
BASELINE_ID = UUID("00000000-0000-7000-8000-000000000101")
TEMPORAL_ID = UUID("00000000-0000-7000-8000-000000000102")
DEVICE = UUID("00000000-0000-7000-8000-000000000201")
PROFILE = UUID("00000000-0000-7000-8000-000000000202")
TRACK_A = UUID("00000000-0000-7000-8000-000000000301")
TRACK_B = UUID("00000000-0000-7000-8000-000000000302")
CUTOFF_MS = 1_788_375_600_000
HASH = "a" * 64


def _track(recording_id: UUID, *, preference: str = "NONE") -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=recording_id,
        user_track_ref_id=None,
        artist_key="artist",
        release_key=None,
        metadata_tokens=(),
        availability="VAULT",
        authorized=True,
        identity_status="ACTIVE",
        preference=preference,
        excluded=False,
        play_count=0,
        organic_play_count=0,
        recommended_play_count=0,
        last_played_at_ms=None,
        added_at_ms=CUTOFF_MS,
        release_date_ordinal=None,
    )


def _snapshot() -> RecommendationInputSnapshot:
    return RecommendationInputSnapshot(
        RecommendationSnapshotRef(BASELINE_ID, HASH, 10, 1, HASH, HASH),
        (_track(TRACK_B, preference="DISLIKED"), _track(TRACK_A)),
        datetime.now(UTC) + timedelta(days=1),
    )


def _event(index: int, recording_id: UUID = TRACK_A) -> TemporalEvidence:
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
        dimensions=(TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, "artist"),),
        occurred_at_ms=CUTOFF_MS - index * 1_000,
        received_at_ms=CUTOFF_MS - 500,
        effective_at_ms=CUTOFF_MS - index * 1_000,
        time_classification=TimeClassification.TRUSTED_EVENT_TIME,
        origin_lane=OriginLane.ORGANIC,
        signed_strength=0.5,
        quality_weight=1.0,
        excluded_from_taste=False,
        source_request_sha256=HASH,
    )


def test_sona_request_uses_raw_chronology_sid_vocabulary_and_mandatory_candidates() -> None:
    query = RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, seed=17, shadow=True)
    semantic_ids = {TRACK_A: SonaSemanticId(1, 2, 3)}
    first = build_sona_inference_request(
        query,
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(2, TRACK_B), _event(1)),
        semantic_ids=semantic_ids,
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    )
    second = build_sona_inference_request(
        query,
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1), _event(2, TRACK_B)),
        semantic_ids=semantic_ids,
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    )

    assert first == second
    assert tuple(value.server_sequence for value in first.history) == (2, 1)
    assert first.history[0].semantic_id.values == (0, 0, 0)
    assert tuple(value.recording_id for value in first.candidates) == (TRACK_A,)


def test_sona_request_rejects_cross_owner_and_late_evidence() -> None:
    query = RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True)
    with pytest.raises(ValueError, match="cross-owner"):
        build_sona_inference_request(
            query,
            _snapshot(),
            temporal_snapshot_id=TEMPORAL_ID,
            cutoff_at_ms=CUTOFF_MS,
            evidence=(replace(_event(1), owner_user_id=OTHER),),
            semantic_ids={TRACK_A: SonaSemanticId(1, 2, 3)},
            tokenizer_sha256="b" * 64,
            model_manifest_sha256="c" * 64,
        )

    request = build_sona_inference_request(
        query,
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(replace(_event(11), server_sequence=11),),
        semantic_ids={TRACK_A: SonaSemanticId(1, 2, 3)},
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    )
    assert request.history == ()


def test_sona_shadow_service_never_runs_for_non_shadow_and_degrades_on_runtime_failure() -> None:
    class SemanticIds:
        tokenizer_manifest_sha256 = "d" * 64

        def load(
            self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
        ) -> Mapping[UUID, SonaSemanticId]:
            del recording_ids, tokenizer_sha256
            return {TRACK_A: SonaSemanticId(1, 2, 3)}

        def expand(
            self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
        ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
            del semantic_ids, tokenizer_sha256
            return {}

    class FailingInference:
        calls = 0

        def infer(self, request: object) -> SonaInferenceOutput:
            del request
            self.calls += 1
            raise RuntimeError("GPU unavailable")

    inference = FailingInference()
    service = SonaShadowService(
        SemanticIds(),
        inference,
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    )
    baseline = service.run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )
    shadow = service.run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )

    assert baseline.degraded_to_p11 and baseline.reason == "SHADOW_NOT_REQUESTED"
    assert shadow.degraded_to_p11 and shadow.reason == "SONA_UNAVAILABLE"
    assert inference.calls == 1


def test_sona_shadow_service_rejects_ranked_recording_outside_bound_candidates() -> None:
    class SemanticIds:
        tokenizer_manifest_sha256 = "d" * 64

        def load(
            self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
        ) -> Mapping[UUID, SonaSemanticId]:
            del recording_ids, tokenizer_sha256
            return {TRACK_A: SonaSemanticId(1, 2, 3)}

        def expand(
            self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
        ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
            del semantic_ids, tokenizer_sha256
            return {}

    class ForeignInference:
        def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
            return SonaInferenceOutput(
                request.request_sha256,
                (),
                (SonaRankedCandidate(OTHER, (0.4, 0.3, 0.2, 0.1), 0.5),),
            )

    result = SonaShadowService(
        SemanticIds(),
        ForeignInference(),
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    ).run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )

    assert result.degraded_to_p11
    assert result.reason == "SONA_OUTPUT_UNBOUND"
    assert result.output is None


def test_sona_shadow_service_rejects_incomplete_ranked_candidate_set() -> None:
    semantic_id = SonaSemanticId(1, 2, 3)

    class SemanticIds:
        tokenizer_manifest_sha256 = "d" * 64

        def load(
            self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
        ) -> Mapping[UUID, SonaSemanticId]:
            del recording_ids, tokenizer_sha256
            return {TRACK_A: semantic_id}

        def expand(
            self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
        ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
            del tokenizer_sha256
            return {semantic_id: (TRACK_A,)} if semantic_id in semantic_ids else {}

    class IncompleteInference:
        def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
            return SonaInferenceOutput(
                request.request_sha256,
                (SonaGeneratedCandidate(semantic_id, -0.1),),
                (),
            )

    result = SonaShadowService(
        SemanticIds(),
        IncompleteInference(),
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    ).run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )

    assert result.degraded_to_p11
    assert result.reason == "SONA_OUTPUT_UNBOUND"
    assert result.output is None


def test_sona_shadow_service_rejects_generated_sid_missing_from_tokenizer() -> None:
    class SemanticIds:
        tokenizer_manifest_sha256 = "d" * 64

        def load(
            self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
        ) -> Mapping[UUID, SonaSemanticId]:
            del recording_ids, tokenizer_sha256
            return {TRACK_A: SonaSemanticId(1, 2, 3)}

        def expand(
            self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
        ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
            del semantic_ids, tokenizer_sha256
            return {}

    class ForeignGeneration:
        def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
            return SonaInferenceOutput(
                request.request_sha256,
                (SonaGeneratedCandidate(SonaSemanticId(7, 8, 9), -0.1),),
                (SonaRankedCandidate(TRACK_A, (0.4, 0.3, 0.2, 0.1), 0.5),),
            )

    result = SonaShadowService(
        SemanticIds(),
        ForeignGeneration(),
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    ).run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )

    assert result.degraded_to_p11
    assert result.reason == "SONA_OUTPUT_UNBOUND"
    assert result.output is None


def test_sona_shadow_service_rejects_generated_sid_outside_bound_snapshot_candidates() -> None:
    generated_sid = SonaSemanticId(7, 8, 9)

    class SemanticIds:
        tokenizer_manifest_sha256 = "d" * 64

        def load(
            self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
        ) -> Mapping[UUID, SonaSemanticId]:
            del recording_ids, tokenizer_sha256
            return {TRACK_A: SonaSemanticId(1, 2, 3)}

        def expand(
            self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
        ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
            del tokenizer_sha256
            return {generated_sid: (OTHER,)} if generated_sid in semantic_ids else {}

    class MappedGeneration:
        def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
            return SonaInferenceOutput(
                request.request_sha256,
                (SonaGeneratedCandidate(generated_sid, -0.1),),
                (SonaRankedCandidate(TRACK_A, (0.4, 0.3, 0.2, 0.1), 0.5),),
            )

    result = SonaShadowService(
        SemanticIds(),
        MappedGeneration(),
        tokenizer_sha256="b" * 64,
        model_manifest_sha256="c" * 64,
    ).run(
        RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, shadow=True),
        _snapshot(),
        temporal_snapshot_id=TEMPORAL_ID,
        cutoff_at_ms=CUTOFF_MS,
        evidence=(_event(1),),
    )

    assert result.degraded_to_p11
    assert result.reason == "SONA_OUTPUT_UNBOUND"
    assert result.output is None
    assert result.postprocessed_ranking == ()


def test_sona_manifest_shares_one_model_across_generation_and_ranking() -> None:
    pipeline = sona_shadow_pipeline_definition(
        tokenizer_manifest_sha256="d" * 64,
        model_manifest_sha256="c" * 64,
    )
    model_components = {
        value.key: value.config_sha256
        for value in pipeline.components
        if value.key.startswith("sona-lite-") and value.key != "sona-lite-semantic-tokenizer"
    }

    assert pipeline.pipeline_key == "sona-lite-shadow"
    assert pipeline.lifecycle_status == "SHADOW"
    assert {value.key for value in pipeline.components} >= {
        "mandatory-filters",
        "deterministic-diversity-reranker",
    }
    assert pipeline.max_artist_repeat == baseline_pipeline_definition().max_artist_repeat
    assert pipeline.max_release_repeat == baseline_pipeline_definition().max_release_repeat
    assert set(model_components.values()) == {"c" * 64}
    assert (
        pipeline.manifest_sha256
        == sona_shadow_pipeline_definition(
            tokenizer_manifest_sha256="d" * 64,
            model_manifest_sha256="c" * 64,
        ).manifest_sha256
    )
    assert baseline_pipeline_definition().pipeline_key == "cpu-baseline"
