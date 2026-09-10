"""R1B shadow-only integration through the existing P11 pipeline ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import cast
from uuid import UUID

import rfc8785

from autplay.application.recommendations import (
    ArtistReleaseMetadataCandidateGenerator,
    BaselineUserRepresentation,
    BaselineUserRepresentationProvider,
    DeterministicHeuristicRanker,
    ExplorationCandidateGenerator,
    FreshnessCandidateGenerator,
    HistoryCandidateGenerator,
    PreferenceCandidateGenerator,
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    pipeline_manifest_document,
)
from autplay.domain.adaptive_recommendations import (
    AdaptiveDimensionState,
    AdaptiveProfile,
    DimensionKind,
    Horizon,
    HorizonValue,
    MaturityState,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
)
from autplay.domain.recommendations import (
    Candidate,
    CandidateContribution,
    ComponentVersionRef,
    JsonValue,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
    ScoredCandidate,
    SnapshotTrack,
)
from autplay.ports.recommendations import (
    AdaptiveProfileReader,
    PreparedUserRepresentation,
)

SHADOW_PIPELINE_KEY = "adaptive-shadow-cpu"
SHADOW_PIPELINE_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ShadowAdaptiveUserRepresentation:
    """P11-compatible baseline values plus an optional immutable temporal profile."""

    version: str
    liked_artists: frozenset[str]
    listened_artists: frozenset[str]
    liked_tokens: frozenset[str]
    adaptive_profile: AdaptiveProfile | None


class ShadowAdaptiveUserRepresentationProvider:
    """Read R1 state only for an explicitly shadow-marked query, with CPU fallback."""

    key = "adaptive-shadow-user-representation"
    version = "1"

    def __init__(
        self,
        profiles: AdaptiveProfileReader,
        baseline: BaselineUserRepresentationProvider | None = None,
    ) -> None:
        self._profiles = profiles
        self._baseline = baseline or BaselineUserRepresentationProvider()

    def prepare(
        self, query: RecommendationQuery, snapshot: RecommendationInputSnapshot
    ) -> PreparedUserRepresentation:
        baseline = cast(BaselineUserRepresentation, self._baseline.prepare(query, snapshot))
        profile: AdaptiveProfile | None = None
        if query.shadow:
            try:
                candidate = self._profiles.load(query.user_id, snapshot.reference.snapshot_id)
            except RuntimeError:
                candidate = None
            if candidate is not None:
                if candidate.owner_user_id != query.user_id:
                    raise ValueError("adaptive profile owner mismatch")
                if candidate.interaction_watermark != snapshot.reference.interaction_watermark:
                    raise ValueError("adaptive profile watermark mismatch")
                profile = candidate
        return ShadowAdaptiveUserRepresentation(
            version=self.version,
            liked_artists=baseline.liked_artists,
            listened_artists=baseline.listened_artists,
            liked_tokens=baseline.liked_tokens,
            adaptive_profile=profile,
        )


class AdaptiveTemporalCandidateGenerator:
    """Attach bounded temporal adjustments without bypassing P11 mandatory filters."""

    key = "adaptive_temporal"
    version = "1"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        if not query.shadow or not isinstance(representation, ShadowAdaptiveUserRepresentation):
            return ()
        profile = representation.adaptive_profile
        if profile is None:
            return ()
        rows = []
        for track in snapshot.tracks:
            matched = _matching_dimensions(track, profile.dimensions)
            if not matched:
                continue
            score = sum(_dimension_adjustment(value) for value in matched) / len(matched)
            rows.append((track, score, matched))
        rows.sort(key=lambda value: (-value[1], value[0].recording_id.hex))
        return tuple(
            Candidate(
                track,
                (
                    CandidateContribution(
                        self.key,
                        self.version,
                        rank,
                        round(score, 8),
                        {
                            "feature_policy": (
                                f"{profile.feature_policy_key}:{profile.feature_policy_version}"
                            ),
                            "feature_policy_sha256": profile.feature_policy_sha256,
                            "matched_dimension_count": len(matched),
                            "maturity": profile.maturity.score,
                        },
                    ),
                ),
            )
            for rank, (track, score, matched) in enumerate(rows[:limit], 1)
        )


class AdaptiveShadowRanker(DeterministicHeuristicRanker):
    """P11 heuristic ranker plus the bounded R1 temporal contribution."""

    key = "adaptive-shadow-heuristic-ranker"
    version = "1"

    def _source_weight(self, source_key: str) -> float:
        return 1.0 if source_key == "adaptive_temporal" else super()._source_weight(source_key)

    def score(
        self, query: RecommendationQuery, candidates: Sequence[Candidate]
    ) -> Sequence[ScoredCandidate]:
        scored = super().score(query, candidates)
        return tuple(
            replace(
                value,
                reason_codes=(
                    (*value.reason_codes, "ADAPTIVE_TEMPORAL_CONTEXT")
                    if any(
                        item.source_key == "adaptive_temporal"
                        for item in value.candidate.contributions
                    )
                    else value.reason_codes
                ),
            )
            for value in scored
        )


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    """Ephemeral comparison result; it is neither a response nor an impression."""

    baseline_items: tuple[RankedRecommendation, ...]
    shadow_items: tuple[RankedRecommendation, ...]
    baseline_pipeline_sha256: str
    shadow_pipeline_sha256: str
    degraded_to_baseline: bool


class AdaptiveRecommendationShadowService:
    """Run baseline and R1B shadow graphs without trace, pack or impression writes."""

    def __init__(self, profiles: AdaptiveProfileReader) -> None:
        self._baseline_pipeline = baseline_pipeline_definition()
        self._shadow_pipeline = shadow_pipeline_definition()
        generators = (
            PreferenceCandidateGenerator(),
            HistoryCandidateGenerator(),
            ArtistReleaseMetadataCandidateGenerator(),
            FreshnessCandidateGenerator(),
            ExplorationCandidateGenerator(),
            AdaptiveTemporalCandidateGenerator(),
        )
        self._baseline = RecommendationPipelineRunner(generators)
        self._shadow = RecommendationPipelineRunner(
            generators,
            ranker=AdaptiveShadowRanker(),
            representation_provider=ShadowAdaptiveUserRepresentationProvider(profiles),
        )

    def compare(
        self, query: RecommendationQuery, snapshot: RecommendationInputSnapshot
    ) -> ShadowComparison:
        baseline_query = replace(query, shadow=False)
        shadow_query = replace(query, shadow=True)
        baseline = self._baseline.run(baseline_query, snapshot, self._baseline_pipeline)
        shadow = self._shadow.run(shadow_query, snapshot, self._shadow_pipeline)
        degraded = not any("ADAPTIVE_TEMPORAL_CONTEXT" in item.reason_codes for item in shadow)
        return ShadowComparison(
            baseline_items=baseline,
            shadow_items=shadow,
            baseline_pipeline_sha256=self._baseline_pipeline.manifest_sha256,
            shadow_pipeline_sha256=self._shadow_pipeline.manifest_sha256,
            degraded_to_baseline=degraded,
        )


def shadow_pipeline_definition() -> PipelineDefinition:
    """Return the immutable R1B shadow graph; P11 cpu-baseline:1 is untouched."""

    baseline = baseline_pipeline_definition()
    components = (
        *(value for value in baseline.components if value.kind not in {"ranker", "representation"}),
        ComponentVersionRef(
            "adaptive_temporal", "candidate_generator", "1", _empty_config_sha256()
        ),
        ComponentVersionRef(
            "adaptive-shadow-heuristic-ranker", "ranker", "1", _empty_config_sha256()
        ),
        ComponentVersionRef(
            "adaptive-shadow-user-representation",
            "representation",
            "1",
            "6fc6c3348475618696eec0ad171515d50e6f586ebf515f67f5add4ef245179db",
        ),
    )
    draft = PipelineDefinition(
        pipeline_key=SHADOW_PIPELINE_KEY,
        version=SHADOW_PIPELINE_VERSION,
        implementation_revision="r1b-shadow-cpu-v1",
        manifest_sha256="0" * 64,
        components=components,
        generator_budgets=(*baseline.generator_budgets, ("adaptive_temporal", 1_000)),
        max_artist_repeat=baseline.max_artist_repeat,
        max_release_repeat=baseline.max_release_repeat,
        lifecycle_status="SHADOW",
    )
    digest = sha256(_canonical_bytes(pipeline_manifest_document(draft))).hexdigest()
    return replace(draft, manifest_sha256=digest)


def temporal_evidence_document(value: TemporalEvidence) -> dict[str, JsonValue]:
    """Serialize and self-hash one accepted normalized evidence document."""

    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "evidence_id": str(value.evidence_id),
        "source_event_id": str(value.source_event_id),
        "owner_user_id": str(value.owner_user_id),
        "server_profile_id": str(value.server_profile_id),
        "device_id": str(value.device_id),
        "device_sequence": value.device_sequence,
        "server_sequence": value.server_sequence,
        "source_event_type": value.source_event_type,
        "signal_key": value.signal.value,
        "derivation_key": value.derivation_key,
        "recording_id": str(value.recording_id),
        "dimensions": [{"kind": item.kind.value, "key": item.key} for item in value.dimensions],
        "occurred_at_ms": value.occurred_at_ms,
        "received_at_ms": value.received_at_ms,
        "effective_at_ms": value.effective_at_ms,
        "time_classification": value.time_classification.value,
        "origin_lane": value.origin_lane.value,
        "signed_strength": value.signed_strength,
        "quality_weight": value.quality_weight,
        "excluded_from_taste": value.excluded_from_taste,
        "source_request_sha256": value.source_request_sha256,
    }
    if value.recommendation_request_id is not None:
        document.update(
            {
                "recommendation_request_id": str(value.recommendation_request_id),
                "impression_event_id": str(value.impression_event_id),
                "recommendation_source_rank": value.recommendation_source_rank,
            }
        )
    document["normalized_evidence_sha256"] = canonical_sha256(document)
    return document


def temporal_evidence_from_document(value: Mapping[str, object]) -> TemporalEvidence:
    """Parse one retained normalized event without accepting current-state substitution."""

    document = dict(value)
    declared_hash = _string(document, "normalized_evidence_sha256")
    unhashed = {key: item for key, item in document.items() if key != "normalized_evidence_sha256"}
    if canonical_sha256(cast(dict[str, JsonValue], unhashed)) != declared_hash:
        raise ValueError("temporal evidence hash mismatch")
    base_keys = {
        "schema_version",
        "evidence_id",
        "source_event_id",
        "owner_user_id",
        "server_profile_id",
        "device_id",
        "device_sequence",
        "server_sequence",
        "source_event_type",
        "signal_key",
        "derivation_key",
        "recording_id",
        "dimensions",
        "occurred_at_ms",
        "received_at_ms",
        "effective_at_ms",
        "time_classification",
        "origin_lane",
        "signed_strength",
        "quality_weight",
        "excluded_from_taste",
        "source_request_sha256",
        "normalized_evidence_sha256",
    }
    causal_keys = {
        "recommendation_request_id",
        "impression_event_id",
        "recommendation_source_rank",
    }
    present_causal = causal_keys.intersection(document)
    if set(document) != base_keys | present_causal or present_causal not in (set(), causal_keys):
        raise ValueError("temporal evidence keys are invalid")
    if _integer(document, "schema_version") != 1:
        raise ValueError("temporal evidence schema is unsupported")
    excluded = document.get("excluded_from_taste")
    if not isinstance(excluded, bool):
        raise ValueError("temporal evidence exclusion flag is invalid")
    server_sequence_value = document.get("server_sequence")
    if server_sequence_value is not None and (
        isinstance(server_sequence_value, bool) or not isinstance(server_sequence_value, int)
    ):
        raise ValueError("temporal evidence server sequence is invalid")
    dimensions = tuple(
        TemporalDimension(
            DimensionKind(_string(_as_mapping(item), "kind")),
            _string(_as_mapping(item), "key"),
        )
        for item in _sequence(document, "dimensions")
    )
    return TemporalEvidence(
        evidence_id=_uuid(document, "evidence_id"),
        source_event_id=_uuid(document, "source_event_id"),
        owner_user_id=_uuid(document, "owner_user_id"),
        server_profile_id=_uuid(document, "server_profile_id"),
        device_id=_uuid(document, "device_id"),
        device_sequence=_integer(document, "device_sequence"),
        server_sequence=server_sequence_value,
        source_event_type=_string(document, "source_event_type"),
        signal=TemporalSignal(_string(document, "signal_key")),
        derivation_key=_string(document, "derivation_key"),
        recording_id=_uuid(document, "recording_id"),
        dimensions=dimensions,
        occurred_at_ms=_integer(document, "occurred_at_ms"),
        received_at_ms=_integer(document, "received_at_ms"),
        effective_at_ms=_integer(document, "effective_at_ms"),
        time_classification=TimeClassification(_string(document, "time_classification")),
        origin_lane=OriginLane(_string(document, "origin_lane")),
        signed_strength=_number(document, "signed_strength"),
        quality_weight=_number(document, "quality_weight"),
        excluded_from_taste=excluded,
        source_request_sha256=_string(document, "source_request_sha256"),
        recommendation_request_id=(
            _uuid(document, "recommendation_request_id") if present_causal else None
        ),
        impression_event_id=(_uuid(document, "impression_event_id") if present_causal else None),
        recommendation_source_rank=(
            _integer(document, "recommendation_source_rank") if present_causal else None
        ),
    )


def adaptive_profile_document(value: AdaptiveProfile) -> dict[str, JsonValue]:
    """Serialize and self-hash one deterministic adaptive profile."""

    maturity = value.maturity
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "owner_user_id": str(value.owner_user_id),
        "cutoff_at_ms": value.cutoff_at_ms,
        "interaction_watermark": value.interaction_watermark,
        "feature_policy": {
            "key": value.feature_policy_key,
            "version": value.feature_policy_version,
            "content_sha256": value.feature_policy_sha256,
        },
        "maturity": {
            "score": maturity.score,
            "effective_signal_mass": maturity.effective_signal_mass,
            "track_coverage": maturity.track_coverage,
            "artist_coverage": maturity.artist_coverage,
            "observation_span_ms": maturity.observation_span_ms,
            "recency_age_ms": maturity.recency_age_ms,
            "recency": maturity.recency,
            "consistency": maturity.consistency,
            "organic_share": maturity.organic_share,
            "plasticity_multiplier": maturity.plasticity_multiplier,
        },
        "dimensions": [_dimension_document(item) for item in value.dimensions],
    }
    document["profile_sha256"] = canonical_sha256(document)
    return document


def adaptive_profile_from_document(value: Mapping[str, object]) -> AdaptiveProfile:
    """Parse a retained profile while checking its immutable self-hash."""

    document = dict(value)
    declared_hash = _string(document, "profile_sha256")
    unhashed = {key: item for key, item in document.items() if key != "profile_sha256"}
    if canonical_sha256(cast(dict[str, JsonValue], unhashed)) != declared_hash:
        raise ValueError("adaptive profile hash mismatch")
    policy = _mapping(document, "feature_policy")
    maturity = _mapping(document, "maturity")
    dimensions = _sequence(document, "dimensions")
    parsed_dimensions = tuple(_dimension_from_document(_as_mapping(item)) for item in dimensions)
    return AdaptiveProfile(
        owner_user_id=_uuid(document, "owner_user_id"),
        cutoff_at_ms=_integer(document, "cutoff_at_ms"),
        interaction_watermark=_integer(document, "interaction_watermark"),
        feature_policy_key=_string(policy, "key"),
        feature_policy_version=_string(policy, "version"),
        feature_policy_sha256=_string(policy, "content_sha256"),
        maturity=MaturityState(
            score=_number(maturity, "score"),
            effective_signal_mass=_number(maturity, "effective_signal_mass"),
            track_coverage=_integer(maturity, "track_coverage"),
            artist_coverage=_integer(maturity, "artist_coverage"),
            observation_span_ms=_integer(maturity, "observation_span_ms"),
            recency_age_ms=_integer(maturity, "recency_age_ms"),
            recency=_number(maturity, "recency"),
            consistency=_number(maturity, "consistency"),
            organic_share=_number(maturity, "organic_share"),
            plasticity_multiplier=_number(maturity, "plasticity_multiplier"),
        ),
        dimensions=parsed_dimensions,
        source_event_ids=tuple(
            dict.fromkeys(
                source_id
                for dimension in parsed_dimensions
                for source_id in dimension.source_event_ids
            )
        ),
    )


def canonical_sha256(document: JsonValue) -> str:
    return sha256(rfc8785.dumps(document)).hexdigest()


def _matching_dimensions(
    track: SnapshotTrack, dimensions: Sequence[AdaptiveDimensionState]
) -> tuple[AdaptiveDimensionState, ...]:
    keys = {
        (DimensionKind.CANONICAL_ARTIST_ID, track.artist_key),
        *((DimensionKind.BOUNDED_METADATA_TOKEN, value) for value in track.metadata_tokens),
    }
    if track.release_key is not None:
        keys.add((DimensionKind.CANONICAL_RELEASE_ID, track.release_key))
    return tuple(
        value for value in dimensions if (value.dimension.kind, value.dimension.key) in keys
    )


def _dimension_adjustment(value: AdaptiveDimensionState) -> float:
    durable = value.long_term.score * value.long_term.confidence
    return min(1.0, max(-1.0, durable + value.recent_adjustment))


def _empty_config_sha256() -> str:
    return sha256(b"{}").hexdigest()


def _canonical_bytes(document: dict[str, JsonValue]) -> bytes:
    return rfc8785.dumps(document)


def _dimension_document(value: AdaptiveDimensionState) -> dict[str, JsonValue]:
    return {
        "kind": value.dimension.kind.value,
        "key": value.dimension.key,
        "long_term": _horizon_document(value.long_term),
        "recent": {horizon.value: _horizon_document(item) for horizon, item in value.recent},
        "persistence": value.persistence,
        "momentum": value.momentum,
        "fatigue": value.fatigue,
        "recent_adjustment": value.recent_adjustment,
        "source_event_ids": [str(item) for item in value.source_event_ids],
    }


def _horizon_document(value: HorizonValue) -> dict[str, JsonValue]:
    return {
        "score": value.score,
        "confidence": value.confidence,
        "evidence_mass": value.evidence_mass,
    }


def _dimension_from_document(value: Mapping[str, object]) -> AdaptiveDimensionState:
    recent = _mapping(value, "recent")
    return AdaptiveDimensionState(
        dimension=TemporalDimension(DimensionKind(_string(value, "kind")), _string(value, "key")),
        long_term=_horizon_from_document(_mapping(value, "long_term")),
        recent=tuple(
            (horizon, _horizon_from_document(_mapping(recent, horizon.value)))
            for horizon in Horizon
        ),
        persistence=_number(value, "persistence"),
        momentum=_number(value, "momentum"),
        fatigue=_number(value, "fatigue"),
        recent_adjustment=_number(value, "recent_adjustment"),
        source_event_ids=tuple(
            UUID(_as_string(item)) for item in _sequence(value, "source_event_ids")
        ),
    )


def _horizon_from_document(value: Mapping[str, object]) -> HorizonValue:
    return HorizonValue(
        _number(value, "score"),
        _number(value, "confidence"),
        _number(value, "evidence_mass"),
    )


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    return _as_mapping(value.get(key))


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("adaptive profile object is invalid")
    return cast(dict[str, object], value)


def _sequence(value: Mapping[str, object], key: str) -> Sequence[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"adaptive profile {key} is invalid")
    return cast(list[object], item)


def _string(value: Mapping[str, object], key: str) -> str:
    return _as_string(value.get(key))


def _as_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("adaptive profile string is invalid")
    return value


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"adaptive profile {key} is invalid")
    return item


def _number(value: Mapping[str, object], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise ValueError(f"adaptive profile {key} is invalid")
    return float(item)


def _uuid(value: Mapping[str, object], key: str) -> UUID:
    return UUID(_string(value, key))


__all__ = (
    "SHADOW_PIPELINE_KEY",
    "SHADOW_PIPELINE_VERSION",
    "AdaptiveRecommendationShadowService",
    "AdaptiveShadowRanker",
    "AdaptiveTemporalCandidateGenerator",
    "ShadowAdaptiveUserRepresentation",
    "ShadowAdaptiveUserRepresentationProvider",
    "ShadowComparison",
    "adaptive_profile_document",
    "adaptive_profile_from_document",
    "canonical_sha256",
    "shadow_pipeline_definition",
    "temporal_evidence_document",
    "temporal_evidence_from_document",
)
