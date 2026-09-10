"""Sona-Lite request construction and shadow-only model orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

import rfc8785

from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    pipeline_manifest_document,
)
from autplay.domain.adaptive_recommendations import OriginLane, TemporalEvidence, TemporalSignal
from autplay.domain.recommendations import (
    ComponentVersionRef,
    JsonValue,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
)
from autplay.domain.sona import (
    SONA_MAX_HISTORY_EVENTS,
    UNKNOWN_SEMANTIC_ID,
    SonaAction,
    SonaCandidate,
    SonaHistoryEvent,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaOrigin,
    SonaSemanticId,
)
from autplay.ports.recommendations import SonaInferenceGateway, SonaSemanticIdReader

_ACTION_IDS = {
    TemporalSignal.FINALIZED_ORGANIC_LISTEN: SonaAction.ORGANIC_LISTEN,
    TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN: SonaAction.RECOMMENDATION_LISTEN,
    TemporalSignal.FINALIZED_COMPLETION: SonaAction.COMPLETION,
    TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP: SonaAction.SHORT_SKIP,
    TemporalSignal.EXPLICIT_LIKE: SonaAction.LIKE,
    TemporalSignal.EXPLICIT_DISLIKE: SonaAction.DISLIKE,
    TemporalSignal.EXCLUDE_FROM_TASTE: SonaAction.EXCLUDE,
    TemporalSignal.RECOMMENDATION_SELECTED: SonaAction.RECOMMENDATION_SELECTED,
    TemporalSignal.RECOMMENDATION_DISMISSED: SonaAction.RECOMMENDATION_DISMISSED,
}
_ORIGIN_IDS = {
    OriginLane.EXPLICIT: SonaOrigin.EXPLICIT,
    OriginLane.ORGANIC: SonaOrigin.ORGANIC,
    OriginLane.SOURCE_QUEUE: SonaOrigin.SOURCE_QUEUE,
    OriginLane.RECOMMENDATION: SonaOrigin.RECOMMENDATION,
    OriginLane.EXCLUSION: SonaOrigin.EXCLUSION,
}
SONA_SHADOW_PIPELINE_KEY = "sona-lite-shadow"
SONA_SHADOW_PIPELINE_VERSION = "1"


@dataclass(frozen=True, slots=True)
class SonaShadowResult:
    """Ephemeral Sona output that is never persisted as served recommendation truth."""

    request: SonaInferenceRequest | None
    output: SonaInferenceOutput | None
    degraded_to_p11: bool
    reason: str | None
    postprocessed_ranking: tuple[RankedRecommendation, ...] = ()
    generated_recording_ids: tuple[UUID, ...] = ()
    generated_expansion: tuple[tuple[SonaSemanticId, tuple[UUID, ...]], ...] = ()


class SonaShadowService:
    """Build one immutable Sona request and fail closed to the independent P11 path."""

    def __init__(
        self,
        semantic_ids: SonaSemanticIdReader,
        inference: SonaInferenceGateway,
        *,
        tokenizer_sha256: str,
        model_manifest_sha256: str,
        postprocessor: RecommendationPipelineRunner | None = None,
    ) -> None:
        self._semantic_ids = semantic_ids
        self._inference = inference
        self._tokenizer_sha256 = tokenizer_sha256
        self._tokenizer_manifest_sha256 = semantic_ids.tokenizer_manifest_sha256
        self._model_manifest_sha256 = model_manifest_sha256
        self._postprocessor = postprocessor or RecommendationPipelineRunner()
        self._pipeline = sona_shadow_pipeline_definition(
            tokenizer_manifest_sha256=self._tokenizer_manifest_sha256,
            model_manifest_sha256=model_manifest_sha256,
        )

    @property
    def tokenizer_sha256(self) -> str:
        return self._tokenizer_sha256

    @property
    def model_manifest_sha256(self) -> str:
        return self._model_manifest_sha256

    @property
    def tokenizer_manifest_sha256(self) -> str:
        return self._tokenizer_manifest_sha256

    @property
    def pipeline(self) -> PipelineDefinition:
        return self._pipeline

    def run(
        self,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        *,
        temporal_snapshot_id: UUID,
        cutoff_at_ms: int,
        evidence: Sequence[TemporalEvidence],
    ) -> SonaShadowResult:
        if not query.shadow:
            return SonaShadowResult(None, None, True, "SHADOW_NOT_REQUESTED")
        if any(value.owner_user_id != query.user_id for value in evidence):
            raise ValueError("cross-owner Sona history rejected")
        recording_ids = tuple(
            dict.fromkeys(
                (
                    *[value.recording_id for value in evidence],
                    *[value.recording_id for value in baseline.tracks],
                )
            )
        )
        request: SonaInferenceRequest | None = None
        try:
            semantic_ids = self._semantic_ids.load(
                recording_ids, tokenizer_sha256=self._tokenizer_sha256
            )
            request = build_sona_inference_request(
                query,
                baseline,
                temporal_snapshot_id=temporal_snapshot_id,
                cutoff_at_ms=cutoff_at_ms,
                evidence=evidence,
                semantic_ids=semantic_ids,
                tokenizer_sha256=self._tokenizer_sha256,
                model_manifest_sha256=self._model_manifest_sha256,
                candidate_authority=self._postprocessor,
            )
            output = self._inference.infer(request)
        except RuntimeError, ValueError:
            return SonaShadowResult(request, None, True, "SONA_UNAVAILABLE")
        candidate_recording_ids = {value.recording_id for value in request.candidates}
        ranked_recording_ids = tuple(value.recording_id for value in output.ranked)
        generated_semantic_ids = tuple(value.semantic_id for value in output.generated)
        try:
            generated_expansion = self._semantic_ids.expand(
                generated_semantic_ids,
                tokenizer_sha256=self._tokenizer_sha256,
            )
        except RuntimeError, ValueError:
            return SonaShadowResult(request, None, True, "SONA_OUTPUT_UNBOUND")
        generated_recording_ids = {
            recording_id
            for semantic_id in generated_semantic_ids
            for recording_id in generated_expansion.get(semantic_id, ())
        }
        if (
            output.request_sha256 != request.request_sha256
            or len(output.generated) != 1
            or len(ranked_recording_ids) != len(candidate_recording_ids)
            or set(ranked_recording_ids) != candidate_recording_ids
            or not generated_recording_ids
            or not generated_recording_ids <= candidate_recording_ids
            or any(
                any(code == 0 for code in value.semantic_id.values)
                or not generated_expansion.get(value.semantic_id)
                for value in output.generated
            )
        ):
            return SonaShadowResult(request, None, True, "SONA_OUTPUT_UNBOUND")
        try:
            postprocessed = self._postprocessor.rank_external_scores(
                query,
                baseline,
                tuple(
                    (value.recording_id, value.combined_score)
                    for value in output.ranked
                    if value.recording_id in generated_recording_ids
                ),
                self._pipeline,
                source_key="sona-lite-ranking-module",
                source_version=SONA_SHADOW_PIPELINE_VERSION,
            )
        except RuntimeError, ValueError:
            return SonaShadowResult(request, None, True, "SONA_OUTPUT_UNBOUND")
        if not postprocessed:
            return SonaShadowResult(request, None, True, "SONA_OUTPUT_UNBOUND")
        generated_pool = tuple(
            value.recording_id
            for value in output.ranked
            if value.recording_id in generated_recording_ids
        )
        canonical_expansion = tuple(
            (value.semantic_id, tuple(generated_expansion[value.semantic_id]))
            for value in output.generated
        )
        return SonaShadowResult(
            request,
            output,
            False,
            None,
            postprocessed,
            generated_pool,
            canonical_expansion,
        )


def build_sona_inference_request(
    query: RecommendationQuery,
    baseline: RecommendationInputSnapshot,
    *,
    temporal_snapshot_id: UUID,
    cutoff_at_ms: int,
    evidence: Sequence[TemporalEvidence],
    semantic_ids: Mapping[UUID, SonaSemanticId],
    tokenizer_sha256: str,
    model_manifest_sha256: str,
    candidate_authority: RecommendationPipelineRunner | None = None,
) -> SonaInferenceRequest:
    """Apply the same bounded chronological selection in training and serving."""

    if any(value.owner_user_id != query.user_id for value in evidence):
        raise ValueError("cross-owner Sona history rejected")
    selected = sorted(
        (
            value
            for value in evidence
            if value.server_sequence is not None
            and value.server_sequence <= baseline.reference.interaction_watermark
            and value.received_at_ms <= cutoff_at_ms
            and value.effective_at_ms <= cutoff_at_ms
        ),
        key=lambda value: (
            value.effective_at_ms,
            value.server_sequence,
            value.evidence_id.hex,
        ),
    )[-SONA_MAX_HISTORY_EVENTS:]
    history = tuple(
        SonaHistoryEvent(
            evidence_id=value.evidence_id,
            recording_id=value.recording_id,
            semantic_id=semantic_ids.get(value.recording_id, UNKNOWN_SEMANTIC_ID),
            action=_ACTION_IDS[value.signal],
            origin=_ORIGIN_IDS[value.origin_lane],
            age_bucket=_age_bucket(cutoff_at_ms - value.effective_at_ms),
            effective_at_ms=value.effective_at_ms,
            server_sequence=value.server_sequence,
        )
        for value in selected
        if value.server_sequence is not None
    )
    authority = candidate_authority or RecommendationPipelineRunner()
    mandatory_tracks = authority.filter_snapshot_tracks(query, baseline)
    candidates = tuple(
        SonaCandidate(track.recording_id, semantic_ids[track.recording_id])
        for track in sorted(mandatory_tracks, key=lambda value: value.recording_id.hex)
        if track.recording_id in semantic_ids
        and semantic_ids[track.recording_id] != UNKNOWN_SEMANTIC_ID
    )
    if not candidates:
        raise RuntimeError("no Sona-tokenized candidates are available")
    document = sona_inference_request_document(
        owner_user_id=query.user_id,
        temporal_snapshot_id=temporal_snapshot_id,
        baseline_snapshot_id=baseline.reference.snapshot_id,
        cutoff_at_ms=cutoff_at_ms,
        interaction_watermark=baseline.reference.interaction_watermark,
        tokenizer_sha256=tokenizer_sha256,
        model_manifest_sha256=model_manifest_sha256,
        seed=query.seed,
        history=history,
        candidates=candidates,
    )
    request_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaInferenceRequest(
        owner_user_id=query.user_id,
        temporal_snapshot_id=temporal_snapshot_id,
        baseline_snapshot_id=baseline.reference.snapshot_id,
        cutoff_at_ms=cutoff_at_ms,
        interaction_watermark=baseline.reference.interaction_watermark,
        tokenizer_sha256=tokenizer_sha256,
        model_manifest_sha256=model_manifest_sha256,
        seed=query.seed,
        history=history,
        candidates=candidates,
        request_sha256=request_sha256,
    )


def sona_inference_request_document(
    request: SonaInferenceRequest | None = None,
    *,
    owner_user_id: UUID | None = None,
    temporal_snapshot_id: UUID | None = None,
    baseline_snapshot_id: UUID | None = None,
    cutoff_at_ms: int | None = None,
    interaction_watermark: int | None = None,
    tokenizer_sha256: str | None = None,
    model_manifest_sha256: str | None = None,
    seed: int | None = None,
    history: tuple[SonaHistoryEvent, ...] | None = None,
    candidates: tuple[SonaCandidate, ...] | None = None,
) -> dict[str, JsonValue]:
    """Return the sole canonical document whose digest identifies a Sona request."""

    if request is not None:
        owner_user_id = request.owner_user_id
        temporal_snapshot_id = request.temporal_snapshot_id
        baseline_snapshot_id = request.baseline_snapshot_id
        cutoff_at_ms = request.cutoff_at_ms
        interaction_watermark = request.interaction_watermark
        tokenizer_sha256 = request.tokenizer_sha256
        model_manifest_sha256 = request.model_manifest_sha256
        seed = request.seed
        history = request.history
        candidates = request.candidates
    if (
        owner_user_id is None
        or temporal_snapshot_id is None
        or baseline_snapshot_id is None
        or cutoff_at_ms is None
        or interaction_watermark is None
        or tokenizer_sha256 is None
        or model_manifest_sha256 is None
        or seed is None
        or history is None
        or candidates is None
    ):
        raise ValueError("Sona request document inputs are incomplete")
    return {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_ENCODER_V1",
        "owner_user_id": str(owner_user_id),
        "temporal_snapshot_id": str(temporal_snapshot_id),
        "baseline_snapshot_id": str(baseline_snapshot_id),
        "cutoff_at_ms": cutoff_at_ms,
        "interaction_watermark": interaction_watermark,
        "tokenizer_sha256": tokenizer_sha256,
        "model_manifest_sha256": model_manifest_sha256,
        "seed": seed,
        "history": [
            {
                "evidence_id": str(value.evidence_id),
                "recording_id": str(value.recording_id),
                "semantic_id": list(value.semantic_id.values),
                "action": int(value.action),
                "origin": int(value.origin),
                "age_bucket": value.age_bucket,
                "effective_at_ms": value.effective_at_ms,
                "server_sequence": value.server_sequence,
            }
            for value in history
        ],
        "candidates": [
            {
                "recording_id": str(value.recording_id),
                "semantic_id": list(value.semantic_id.values),
            }
            for value in candidates
        ],
    }


def sona_shadow_pipeline_definition(
    *, tokenizer_manifest_sha256: str, model_manifest_sha256: str
) -> PipelineDefinition:
    """Freeze one shared-model shadow graph without changing P11's manifest."""

    p11 = baseline_pipeline_definition()
    shared_authorities = tuple(
        component
        for component in p11.components
        if component.key in {"mandatory-filters", "deterministic-diversity-reranker"}
    )
    components = (
        ComponentVersionRef(
            "sona-lite-semantic-tokenizer", "tokenizer", "1", tokenizer_manifest_sha256
        ),
        ComponentVersionRef(
            "sona-lite-shared-encoder", "representation", "1", model_manifest_sha256
        ),
        ComponentVersionRef(
            "sona-lite-semantic-decoder", "candidate_generator", "1", model_manifest_sha256
        ),
        ComponentVersionRef("sona-lite-ranking-module", "ranker", "1", model_manifest_sha256),
        *shared_authorities,
    )
    draft = PipelineDefinition(
        pipeline_key=SONA_SHADOW_PIPELINE_KEY,
        version=SONA_SHADOW_PIPELINE_VERSION,
        implementation_revision="r1b-sona-lite-shadow-v1",
        manifest_sha256="0" * 64,
        components=components,
        generator_budgets=(("sona-lite-semantic-decoder", 1),),
        max_artist_repeat=p11.max_artist_repeat,
        max_release_repeat=p11.max_release_repeat,
        lifecycle_status="SHADOW",
    )
    manifest_sha256 = sha256(rfc8785.dumps(pipeline_manifest_document(draft))).hexdigest()
    return PipelineDefinition(
        pipeline_key=draft.pipeline_key,
        version=draft.version,
        implementation_revision=draft.implementation_revision,
        manifest_sha256=manifest_sha256,
        components=draft.components,
        generator_budgets=draft.generator_budgets,
        max_artist_repeat=draft.max_artist_repeat,
        max_release_repeat=draft.max_release_repeat,
        lifecycle_status=draft.lifecycle_status,
    )


def _age_bucket(age_ms: int) -> int:
    seconds = max(0, age_ms // 1_000)
    return min(63, seconds.bit_length())


__all__ = (
    "SONA_SHADOW_PIPELINE_KEY",
    "SONA_SHADOW_PIPELINE_VERSION",
    "SonaShadowResult",
    "SonaShadowService",
    "build_sona_inference_request",
    "sona_inference_request_document",
    "sona_shadow_pipeline_definition",
)
