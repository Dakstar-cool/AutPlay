"""Deterministic R1B temporal-profile projection; never a serving authority."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import pow
from uuid import UUID

MAX_SOURCE_EVENTS = 10_000
MAX_DIMENSIONS = 512
MAX_SOURCE_REFERENCES_PER_DIMENSION = 64


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


class DimensionKind(StrEnum):
    CANONICAL_ARTIST_ID = "CANONICAL_ARTIST_ID"
    CANONICAL_RELEASE_ID = "CANONICAL_RELEASE_ID"
    BOUNDED_METADATA_TOKEN = "BOUNDED_METADATA_TOKEN"


class Horizon(StrEnum):
    EPISODE = "EPISODE"
    H12 = "H12"
    H24 = "H24"
    D3 = "D3"
    D7 = "D7"


class OriginLane(StrEnum):
    EXPLICIT = "EXPLICIT"
    ORGANIC = "ORGANIC"
    SOURCE_QUEUE = "SOURCE_QUEUE"
    RECOMMENDATION = "RECOMMENDATION"
    EXCLUSION = "EXCLUSION"


class TemporalSignal(StrEnum):
    EXPLICIT_LIKE = "EXPLICIT_LIKE"
    EXPLICIT_DISLIKE = "EXPLICIT_DISLIKE"
    EXCLUDE_FROM_TASTE = "EXCLUDE_FROM_TASTE"
    FINALIZED_ORGANIC_LISTEN = "FINALIZED_ORGANIC_LISTEN"
    FINALIZED_RECOMMENDATION_LISTEN = "FINALIZED_RECOMMENDATION_LISTEN"
    RECOMMENDATION_SELECTED = "RECOMMENDATION_SELECTED"
    RECOMMENDATION_DISMISSED = "RECOMMENDATION_DISMISSED"
    FINALIZED_COMPLETION = "FINALIZED_COMPLETION"
    FINALIZED_SHORT_LISTEN_SKIP = "FINALIZED_SHORT_LISTEN_SKIP"


class TimeClassification(StrEnum):
    TRUSTED_EVENT_TIME = "TRUSTED_EVENT_TIME"
    DELAYED_WITHIN_POLICY = "DELAYED_WITHIN_POLICY"
    FUTURE_SKEW_CLAMPED = "FUTURE_SKEW_CLAMPED"
    PAST_SKEW_RECENT_DISABLED = "PAST_SKEW_RECENT_DISABLED"
    RECEIPT_TIME_ONLY = "RECEIPT_TIME_ONLY"
    LOCAL_UNSYNCED_TIME = "LOCAL_UNSYNCED_TIME"


@dataclass(frozen=True, slots=True)
class AdaptiveFeaturePolicy:
    """Immutable evaluation-candidate policy accepted by ADR-048."""

    key: str = "adaptive-taste"
    version: str = "1"
    content_sha256: str = "6fc6c3348475618696eec0ad171515d50e6f586ebf515f67f5add4ef245179db"
    episode_inactivity_gap_ms: int = 1_800_000
    future_clock_tolerance_ms: int = 300_000
    maximum_recent_backfill_ms: int = 2_592_000_000
    completion_threshold: float = 0.8
    skip_completion_threshold: float = 0.2
    skip_played_ms_threshold: int = 30_000
    active_view_weights: tuple[tuple[Horizon, float], ...] = (
        (Horizon.EPISODE, 0.30),
        (Horizon.H12, 0.25),
        (Horizon.H24, 0.20),
        (Horizon.D3, 0.15),
        (Horizon.D7, 0.10),
    )
    horizon_window_ms: tuple[tuple[Horizon, int], ...] = (
        (Horizon.H12, 43_200_000),
        (Horizon.H24, 86_400_000),
        (Horizon.D3, 259_200_000),
        (Horizon.D7, 604_800_000),
    )
    signal_weights: tuple[tuple[TemporalSignal, float], ...] = (
        (TemporalSignal.EXPLICIT_LIKE, 1.0),
        (TemporalSignal.FINALIZED_ORGANIC_LISTEN, 0.4),
        (TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN, 0.2),
        (TemporalSignal.RECOMMENDATION_SELECTED, 0.25),
        (TemporalSignal.RECOMMENDATION_DISMISSED, -0.35),
        (TemporalSignal.FINALIZED_COMPLETION, 0.3),
        (TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP, -0.45),
    )
    origin_weights: tuple[tuple[OriginLane, float], ...] = (
        (OriginLane.EXPLICIT, 1.0),
        (OriginLane.ORGANIC, 0.8),
        (OriginLane.SOURCE_QUEUE, 0.6),
        (OriginLane.RECOMMENDATION, 0.35),
        (OriginLane.EXCLUSION, 0.0),
    )
    daily_fatigue_decay: float = 0.75
    skip_gain: float = 0.20
    completion_recovery: float = 0.10
    maturity_term_weights: tuple[tuple[str, float], ...] = (
        ("EFFECTIVE_SIGNAL_MASS", 0.25),
        ("TRACK_AND_ARTIST_COVERAGE", 0.20),
        ("OBSERVATION_SPAN", 0.15),
        ("RECENCY", 0.15),
        ("CONSISTENCY", 0.15),
        ("ORGANIC_SHARE", 0.10),
    )
    effective_mass_saturation: float = 200.0
    track_coverage_saturation: int = 100
    artist_coverage_saturation: int = 30
    observation_span_saturation_ms: int = 7_776_000_000
    recency_half_life_ms: int = 1_209_600_000
    minimum_plasticity: float = 0.5
    maximum_plasticity: float = 2.0
    recent_interest_cap: float = 0.35
    momentum_cap: float = 0.15
    fatigue_cap: float = 0.40
    total_absolute_cap: float = 0.75

    def __post_init__(self) -> None:
        if len(self.content_sha256) != 64:
            raise ValueError("feature policy hash must be SHA-256")
        if abs(sum(dict(self.active_view_weights).values()) - 1.0) > 1e-12:
            raise ValueError("active view weights must sum to one")
        if abs(sum(dict(self.maturity_term_weights).values()) - 1.0) > 1e-12:
            raise ValueError("maturity weights must sum to one")
        if (
            not (0.0 <= self.skip_completion_threshold < self.completion_threshold <= 1.0)
            or self.skip_played_ms_threshold < 0
        ):
            raise ValueError("outcome classifier thresholds are invalid")
        signal_weights = dict(self.signal_weights)
        if set(signal_weights) != {
            TemporalSignal.EXPLICIT_LIKE,
            TemporalSignal.FINALIZED_ORGANIC_LISTEN,
            TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN,
            TemporalSignal.RECOMMENDATION_SELECTED,
            TemporalSignal.RECOMMENDATION_DISMISSED,
            TemporalSignal.FINALIZED_COMPLETION,
            TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
        }:
            raise ValueError("adaptive signal weights are incomplete")
        if any(
            signal_weights[signal] <= 0.0
            for signal in (
                TemporalSignal.EXPLICIT_LIKE,
                TemporalSignal.FINALIZED_ORGANIC_LISTEN,
                TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN,
                TemporalSignal.RECOMMENDATION_SELECTED,
                TemporalSignal.FINALIZED_COMPLETION,
            )
        ) or any(
            signal_weights[signal] >= 0.0
            for signal in (
                TemporalSignal.RECOMMENDATION_DISMISSED,
                TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
            )
        ):
            raise ValueError("adaptive signal weight signs are invalid")
        if self.minimum_plasticity > self.maximum_plasticity:
            raise ValueError("plasticity range is invalid")


DEFAULT_ADAPTIVE_FEATURE_POLICY = AdaptiveFeaturePolicy()


@dataclass(frozen=True, order=True, slots=True)
class TemporalDimension:
    kind: DimensionKind
    key: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.key) <= 200:
            raise ValueError("dimension key length is invalid")


@dataclass(frozen=True, slots=True)
class TemporalEvidence:
    """One normalized evidence document after sync acceptance and classification."""

    evidence_id: UUID
    source_event_id: UUID
    owner_user_id: UUID
    server_profile_id: UUID
    device_id: UUID
    device_sequence: int
    server_sequence: int | None
    source_event_type: str
    signal: TemporalSignal
    derivation_key: str
    recording_id: UUID
    dimensions: tuple[TemporalDimension, ...]
    occurred_at_ms: int
    received_at_ms: int
    effective_at_ms: int
    time_classification: TimeClassification
    origin_lane: OriginLane
    signed_strength: float
    quality_weight: float
    excluded_from_taste: bool
    source_request_sha256: str
    recommendation_request_id: UUID | None = None
    impression_event_id: UUID | None = None
    recommendation_source_rank: int | None = None

    def __post_init__(self) -> None:
        if self.device_sequence < 1 or (
            self.server_sequence is not None and self.server_sequence < 1
        ):
            raise ValueError("event sequences must be positive")
        if not 1 <= len(self.dimensions) <= 32 or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("evidence dimensions must be unique and bounded")
        if min(self.occurred_at_ms, self.received_at_ms, self.effective_at_ms) < 0:
            raise ValueError("event times must be non-negative")
        if not -1.0 <= self.signed_strength <= 1.0:
            raise ValueError("signed strength must be bounded")
        if not 0.0 <= self.quality_weight <= 1.0:
            raise ValueError("quality weight must be bounded")
        if len(self.source_request_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.source_request_sha256
        ):
            raise ValueError("source request hash must be lowercase SHA-256")
        recommendation_refs = (
            self.recommendation_request_id,
            self.impression_event_id,
            self.recommendation_source_rank,
        )
        if any(value is not None for value in recommendation_refs) != all(
            value is not None for value in recommendation_refs
        ):
            raise ValueError("recommendation causal references must be complete")
        if self.origin_lane is OriginLane.RECOMMENDATION and not all(
            value is not None for value in recommendation_refs
        ):
            raise ValueError("recommendation evidence requires causal impression references")
        if self.recommendation_source_rank is not None and not (
            1 <= self.recommendation_source_rank <= 1000
        ):
            raise ValueError("recommendation source rank is invalid")
        if self.time_classification is TimeClassification.LOCAL_UNSYNCED_TIME:
            if self.server_sequence is not None:
                raise ValueError("local unsynced evidence cannot have a server sequence")
        elif self.server_sequence is None:
            raise ValueError("server evidence requires an applied server sequence")

    @property
    def recent_eligible(self) -> bool:
        return self.time_classification is not TimeClassification.PAST_SKEW_RECENT_DISABLED


@dataclass(frozen=True, slots=True)
class EventTimeDecision:
    effective_at_ms: int
    classification: TimeClassification
    recent_eligible: bool
    confidence_multiplier: float


@dataclass(frozen=True, slots=True)
class HorizonValue:
    score: float
    confidence: float
    evidence_mass: float


@dataclass(frozen=True, slots=True)
class MaturityState:
    score: float
    effective_signal_mass: float
    track_coverage: int
    artist_coverage: int
    observation_span_ms: int
    recency_age_ms: int
    recency: float
    consistency: float
    organic_share: float
    plasticity_multiplier: float


@dataclass(frozen=True, slots=True)
class AdaptiveDimensionState:
    dimension: TemporalDimension
    long_term: HorizonValue
    recent: tuple[tuple[Horizon, HorizonValue], ...]
    persistence: float
    momentum: float
    fatigue: float
    recent_adjustment: float
    source_event_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AdaptiveProfile:
    owner_user_id: UUID
    cutoff_at_ms: int
    interaction_watermark: int
    feature_policy_key: str
    feature_policy_version: str
    feature_policy_sha256: str
    maturity: MaturityState
    dimensions: tuple[AdaptiveDimensionState, ...]
    source_event_ids: tuple[UUID, ...]


class TemporalProjectionError(ValueError):
    """Projection input violates a fail-closed R1A invariant."""


def classify_event_time(
    occurred_at_ms: int | None,
    received_at_ms: int,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> EventTimeDecision:
    if received_at_ms < 0 or (occurred_at_ms is not None and occurred_at_ms < 0):
        raise ValueError("event times must be non-negative")
    if occurred_at_ms is None:
        return EventTimeDecision(received_at_ms, TimeClassification.RECEIPT_TIME_ONLY, True, 0.75)
    if occurred_at_ms > received_at_ms + policy.future_clock_tolerance_ms:
        return EventTimeDecision(received_at_ms, TimeClassification.FUTURE_SKEW_CLAMPED, True, 0.75)
    age_ms = received_at_ms - occurred_at_ms
    if age_ms > policy.maximum_recent_backfill_ms:
        return EventTimeDecision(
            occurred_at_ms, TimeClassification.PAST_SKEW_RECENT_DISABLED, False, 0.75
        )
    if age_ms > policy.future_clock_tolerance_ms:
        return EventTimeDecision(
            occurred_at_ms, TimeClassification.DELAYED_WITHIN_POLICY, True, 1.0
        )
    return EventTimeDecision(occurred_at_ms, TimeClassification.TRUSTED_EVENT_TIME, True, 1.0)


def normalized_view_coefficients(
    active_views: Iterable[Horizon],
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> dict[Horizon, float]:
    weights = dict(policy.active_view_weights)
    active = tuple(dict.fromkeys(active_views))
    denominator = sum(weights[value] for value in active)
    if denominator <= 0.0:
        return {}
    return {value: weights[value] / denominator for value in active}


def maturity_from_observation(
    *,
    effective_signal_mass: float,
    track_coverage: int,
    artist_coverage: int,
    observation_span_ms: int,
    recency_age_ms: int,
    consistency: float,
    organic_share: float,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> MaturityState:
    recency = pow(0.5, recency_age_ms / policy.recency_half_life_ms)
    coverage = (
        _clamp(track_coverage / policy.track_coverage_saturation)
        + _clamp(artist_coverage / policy.artist_coverage_saturation)
    ) / 2.0
    components = {
        "EFFECTIVE_SIGNAL_MASS": _clamp(effective_signal_mass / policy.effective_mass_saturation),
        "TRACK_AND_ARTIST_COVERAGE": coverage,
        "OBSERVATION_SPAN": _clamp(observation_span_ms / policy.observation_span_saturation_ms),
        "RECENCY": recency,
        "CONSISTENCY": _clamp(consistency),
        "ORGANIC_SHARE": _clamp(organic_share),
    }
    maturity = sum(weight * components[key] for key, weight in policy.maturity_term_weights)
    plasticity = policy.maximum_plasticity - maturity * (
        policy.maximum_plasticity - policy.minimum_plasticity
    )
    return MaturityState(
        score=maturity,
        effective_signal_mass=effective_signal_mass,
        track_coverage=track_coverage,
        artist_coverage=artist_coverage,
        observation_span_ms=observation_span_ms,
        recency_age_ms=recency_age_ms,
        recency=recency,
        consistency=_clamp(consistency),
        organic_share=_clamp(organic_share),
        plasticity_multiplier=plasticity,
    )


@dataclass(slots=True)
class _Mass:
    positive: float = 0.0
    negative: float = 0.0

    def add(self, value: float) -> None:
        if value >= 0:
            self.positive += value
        else:
            self.negative += -value

    @property
    def total(self) -> float:
        return self.positive + self.negative

    def value(self) -> HorizonValue:
        if self.total == 0.0:
            return HorizonValue(0.0, 0.0, 0.0)
        score = (self.positive - self.negative) / self.total
        contradiction = 2.0 * min(self.positive, self.negative) / self.total
        base_confidence = self.total / (self.total + 1.0)
        return HorizonValue(score, base_confidence * (1.0 - contradiction), self.total)


@dataclass(frozen=True, slots=True)
class _WeightedEvidence:
    event: TemporalEvidence
    signed_mass: float


_DURABLE_SIGNALS = frozenset(
    {
        TemporalSignal.EXPLICIT_LIKE,
        TemporalSignal.FINALIZED_ORGANIC_LISTEN,
        TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN,
        TemporalSignal.FINALIZED_COMPLETION,
    }
)
_FATIGUE_SIGNALS = frozenset(
    {
        TemporalSignal.RECOMMENDATION_DISMISSED,
        TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
    }
)


def project_temporal_profile(
    *,
    owner_user_id: UUID,
    cutoff_at_ms: int,
    interaction_watermark: int,
    evidence: Sequence[TemporalEvidence],
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> AdaptiveProfile:
    """Project one immutable owner/cutoff/watermark profile in stable event order."""

    if cutoff_at_ms < 0 or interaction_watermark < 0:
        raise TemporalProjectionError("cutoff and watermark must be non-negative")
    if len(evidence) > MAX_SOURCE_EVENTS:
        raise TemporalProjectionError("temporal snapshot source-event limit exceeded")
    eligible = _eligible_deduplicated(owner_user_id, cutoff_at_ms, interaction_watermark, evidence)
    weighted = _causal_weights(eligible, policy)
    episode_ids = _current_episode_ids(weighted, cutoff_at_ms, policy)
    dimension_events: dict[TemporalDimension, list[_WeightedEvidence]] = defaultdict(list)
    for value in weighted:
        if value.event.excluded_from_taste:
            continue
        for dimension in value.event.dimensions:
            dimension_events[dimension].append(value)
    if len(dimension_events) > MAX_DIMENSIONS:
        raise TemporalProjectionError("temporal snapshot dimension limit exceeded")

    maturity = _maturity(weighted, cutoff_at_ms, policy)
    dimensions = tuple(
        _project_dimension(
            dimension,
            values,
            episode_ids,
            cutoff_at_ms,
            maturity.plasticity_multiplier,
            policy,
        )
        for dimension, values in sorted(dimension_events.items())
    )
    return AdaptiveProfile(
        owner_user_id=owner_user_id,
        cutoff_at_ms=cutoff_at_ms,
        interaction_watermark=interaction_watermark,
        feature_policy_key=policy.key,
        feature_policy_version=policy.version,
        feature_policy_sha256=policy.content_sha256,
        maturity=maturity,
        dimensions=dimensions,
        source_event_ids=tuple(value.event.evidence_id for value in weighted),
    )


def _eligible_deduplicated(
    owner_user_id: UUID,
    cutoff_at_ms: int,
    interaction_watermark: int,
    evidence: Sequence[TemporalEvidence],
) -> tuple[TemporalEvidence, ...]:
    by_id: dict[UUID, TemporalEvidence] = {}
    source_hashes: dict[UUID, str] = {}
    for event in evidence:
        if event.owner_user_id != owner_user_id:
            raise TemporalProjectionError("cross-owner temporal evidence rejected")
        prior_hash = source_hashes.setdefault(event.source_event_id, event.source_request_sha256)
        if prior_hash != event.source_request_sha256:
            raise TemporalProjectionError("source event request hash conflict")
        prior = by_id.setdefault(event.evidence_id, event)
        if prior != event:
            raise TemporalProjectionError("normalized evidence identity conflict")
    selected = (
        event
        for event in by_id.values()
        if event.server_sequence is not None
        and event.received_at_ms <= cutoff_at_ms
        and event.server_sequence <= interaction_watermark
    )
    return tuple(
        sorted(
            selected,
            key=lambda value: (
                value.effective_at_ms,
                value.received_at_ms,
                value.server_sequence,
                value.evidence_id.hex,
            ),
        )
    )


def _causal_weights(
    evidence: Sequence[TemporalEvidence], policy: AdaptiveFeaturePolicy
) -> tuple[_WeightedEvidence, ...]:
    groups: dict[tuple[object, ...], list[TemporalEvidence]] = defaultdict(list)
    for event in evidence:
        if event.recommendation_request_id is not None:
            key: tuple[object, ...] = (
                "recommendation",
                event.recommendation_request_id,
                event.impression_event_id,
                event.recommendation_source_rank,
                event.recording_id,
            )
        elif event.source_event_type == "LISTENING_EVENT_RECORDED":
            key = ("listening", event.source_event_id)
        else:
            key = ("source", event.source_event_id)
        groups[key].append(event)
    origin_weights = dict(policy.origin_weights)
    result: list[_WeightedEvidence] = []
    for values in groups.values():
        denominator = max(1.0, sum(abs(value.signed_strength) for value in values))
        for event in values:
            signed_mass = (
                event.signed_strength
                / denominator
                * event.quality_weight
                * origin_weights[event.origin_lane]
            )
            result.append(_WeightedEvidence(event, signed_mass))
    result.sort(
        key=lambda value: (
            value.event.effective_at_ms,
            value.event.received_at_ms,
            value.event.server_sequence,
            value.event.evidence_id.hex,
        )
    )
    return tuple(result)


def _current_episode_ids(
    weighted: Sequence[_WeightedEvidence],
    cutoff_at_ms: int,
    policy: AdaptiveFeaturePolicy,
) -> frozenset[UUID]:
    recent = [
        value.event
        for value in weighted
        if value.event.recent_eligible and value.event.effective_at_ms <= cutoff_at_ms
    ]
    if not recent or cutoff_at_ms - recent[-1].effective_at_ms > policy.episode_inactivity_gap_ms:
        return frozenset()
    start = len(recent) - 1
    while start > 0:
        gap = recent[start].effective_at_ms - recent[start - 1].effective_at_ms
        if gap > policy.episode_inactivity_gap_ms:
            break
        start -= 1
    return frozenset(value.evidence_id for value in recent[start:])


def _active_views(
    event: TemporalEvidence,
    episode_ids: frozenset[UUID],
    cutoff_at_ms: int,
    policy: AdaptiveFeaturePolicy,
) -> tuple[Horizon, ...]:
    if not event.recent_eligible:
        return ()
    result: list[Horizon] = []
    if event.evidence_id in episode_ids:
        result.append(Horizon.EPISODE)
    age = cutoff_at_ms - event.effective_at_ms
    for horizon, window_ms in policy.horizon_window_ms:
        if 0 <= age <= window_ms:
            result.append(horizon)
    return tuple(result)


def _maturity(
    weighted: Sequence[_WeightedEvidence],
    cutoff_at_ms: int,
    policy: AdaptiveFeaturePolicy,
) -> MaturityState:
    contributing = [
        value
        for value in weighted
        if not value.event.excluded_from_taste and value.signed_mass != 0
    ]
    total_mass = sum(abs(value.signed_mass) for value in contributing)
    positive = sum(max(0.0, value.signed_mass) for value in contributing)
    negative = sum(max(0.0, -value.signed_mass) for value in contributing)
    contradiction = 0.0 if total_mass == 0 else 2.0 * min(positive, negative) / total_mass
    organic_mass = sum(
        abs(value.signed_mass)
        for value in contributing
        if value.event.origin_lane is OriginLane.ORGANIC
    )
    times = [value.event.effective_at_ms for value in contributing]
    latest = max(times, default=cutoff_at_ms)
    artist_keys = {
        dimension.key
        for value in contributing
        for dimension in value.event.dimensions
        if dimension.kind is DimensionKind.CANONICAL_ARTIST_ID
    }
    return maturity_from_observation(
        effective_signal_mass=total_mass,
        track_coverage=len({value.event.recording_id for value in contributing}),
        artist_coverage=len(artist_keys),
        observation_span_ms=0 if not times else max(times) - min(times),
        recency_age_ms=max(0, cutoff_at_ms - latest),
        consistency=1.0 - contradiction,
        organic_share=0.0 if total_mass == 0 else organic_mass / total_mass,
        policy=policy,
    )


def _project_dimension(
    dimension: TemporalDimension,
    weighted: Sequence[_WeightedEvidence],
    episode_ids: frozenset[UUID],
    cutoff_at_ms: int,
    plasticity: float,
    policy: AdaptiveFeaturePolicy,
) -> AdaptiveDimensionState:
    long_term_mass = _Mass()
    horizon_mass = {horizon: _Mass() for horizon in Horizon}
    fused_mass = _Mass()
    fatigue = 0.0
    fatigue_at_ms = weighted[0].event.effective_at_ms if weighted else cutoff_at_ms
    references: list[UUID] = []
    for value in weighted:
        event = value.event
        if event.evidence_id not in references:
            references.append(event.evidence_id)
        if event.signal in _DURABLE_SIGNALS:
            long_term_mass.add(value.signed_mass)
        active = _active_views(event, episode_ids, cutoff_at_ms, policy)
        if active:
            for horizon in active:
                horizon_mass[horizon].add(value.signed_mass)
            for coefficient in normalized_view_coefficients(active, policy).values():
                fused_mass.add(value.signed_mass * coefficient)
        elapsed_days = max(0.0, (event.effective_at_ms - fatigue_at_ms) / 86_400_000)
        fatigue *= pow(policy.daily_fatigue_decay, elapsed_days)
        if event.signal in _FATIGUE_SIGNALS:
            fatigue += abs(value.signed_mass) * policy.skip_gain
        elif event.signal is TemporalSignal.FINALIZED_COMPLETION:
            fatigue -= abs(value.signed_mass) * policy.completion_recovery
        fatigue = _clamp(fatigue)
        fatigue_at_ms = max(fatigue_at_ms, event.effective_at_ms)
    fatigue *= pow(
        policy.daily_fatigue_decay, max(0.0, (cutoff_at_ms - fatigue_at_ms) / 86_400_000)
    )
    fatigue = _clamp(fatigue)

    recent = tuple((horizon, horizon_mass[horizon].value()) for horizon in Horizon)
    recent_by_horizon = dict(recent)
    momentum = _clamp(
        recent_by_horizon[Horizon.EPISODE].score - recent_by_horizon[Horizon.D7].score,
        -1.0,
        1.0,
    )
    observed = [value for value in recent_by_horizon.values() if value.evidence_mass > 0]
    persistence = (
        0.0 if not observed else sum(value.confidence for value in observed) / len(observed)
    )
    fused = fused_mass.value()
    components = [
        _clamp(
            fused.score * fused.confidence * plasticity,
            -policy.recent_interest_cap,
            policy.recent_interest_cap,
        ),
        momentum * policy.momentum_cap,
        -fatigue * policy.fatigue_cap,
    ]
    absolute = sum(abs(value) for value in components)
    if absolute > policy.total_absolute_cap:
        scale = policy.total_absolute_cap / absolute
        components = [value * scale for value in components]
    return AdaptiveDimensionState(
        dimension=dimension,
        long_term=long_term_mass.value(),
        recent=recent,
        persistence=_clamp(persistence),
        momentum=momentum,
        fatigue=fatigue,
        recent_adjustment=_clamp(sum(components), -1.0, 1.0),
        source_event_ids=tuple(references[-MAX_SOURCE_REFERENCES_PER_DIMENSION:]),
    )


__all__ = (
    "DEFAULT_ADAPTIVE_FEATURE_POLICY",
    "MAX_DIMENSIONS",
    "MAX_SOURCE_EVENTS",
    "MAX_SOURCE_REFERENCES_PER_DIMENSION",
    "AdaptiveDimensionState",
    "AdaptiveFeaturePolicy",
    "AdaptiveProfile",
    "DimensionKind",
    "EventTimeDecision",
    "Horizon",
    "HorizonValue",
    "MaturityState",
    "OriginLane",
    "TemporalDimension",
    "TemporalEvidence",
    "TemporalProjectionError",
    "TemporalSignal",
    "TimeClassification",
    "classify_event_time",
    "maturity_from_observation",
    "normalized_view_coefficients",
    "project_temporal_profile",
)
