"""Private read-derived temporal values; never original capture or admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    AdaptiveProfile,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
    classify_event_time,
)
from autplay.domain.sona_native_source import NativeSourceSelection

MAX_NATIVE_TEMPORAL_SOURCES = 256
MAX_NATIVE_TEMPORAL_BYTES = 2 * 1024 * 1024
NATIVE_TEMPORAL_NAMESPACE = uuid5(NAMESPACE_URL, "https://autplay.local/native-temporal-v2")


class NativeTemporalBoundary(Protocol):
    """Normalization needs boundaries, not the authority to choose membership."""

    @property
    def owner(self) -> UUID: ...

    @property
    def event_ids(self) -> tuple[UUID, ...]: ...

    @property
    def cutoff_at_ms(self) -> int: ...

    @property
    def interaction_watermark(self) -> int: ...


@dataclass(frozen=True, slots=True)
class NativeTemporalSelection:
    """Caller-pinned exact subset, not a claim of complete owner history."""

    owner: UUID
    baseline_request_id: UUID
    event_ids: tuple[UUID, ...]
    cutoff_at_ms: int
    interaction_watermark: int
    artifact_generations: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if (
            type(self.owner) is not UUID
            or type(self.baseline_request_id) is not UUID
            or not 1 <= len(self.event_ids) <= MAX_NATIVE_TEMPORAL_SOURCES
            or any(type(value) is not UUID for value in self.event_ids)
            or self.event_ids != tuple(sorted(set(self.event_ids)))
            or type(self.cutoff_at_ms) is not int
            or not 0 <= self.cutoff_at_ms <= 2**53 - 1
            or type(self.interaction_watermark) is not int
            or not 1 <= self.interaction_watermark <= 2**53 - 1
        ):
            raise ValueError("NATIVE_TEMPORAL_SELECTION_BOUND")
        NativeSourceSelection((self.baseline_request_id,), self.artifact_generations)


@dataclass(frozen=True, slots=True)
class NativeTemporalCausality:
    """Exact accepted association, including independent client/server labels."""

    association_id: UUID
    association_sha256: str
    request_id: UUID
    served_rank: int
    decision_sha256: str
    served_item_sha256: str
    serving_provenance: str
    request_surface: str
    presentation_id: UUID
    presentation_source: str
    presentation_surface: str
    impression_association_id: UUID | None
    impression_event_id: UUID | None


@dataclass(frozen=True, slots=True)
class NativeTemporalFact:
    """Typed persisted source, constructed only by the trusted private SQL reader."""

    event_id: UUID
    owner: UUID
    device: UUID
    journal_epoch: UUID
    device_sequence: int
    server_sequence: int
    event_type: str
    recording_id: UUID
    dimensions: tuple[TemporalDimension, ...]
    occurred_at_ms: int
    received_at_ms: int
    source_request_sha256: str
    normalized_payload_sha256: str
    retained_until_ms: int
    event_origin: str | None = None
    played_ms: int | None = None
    completion_ratio: float | None = None
    excluded_from_taste: bool = False
    feedback: str | None = None
    causality: NativeTemporalCausality | None = None
    explicit_feedback: str = "NONE"


@dataclass(frozen=True, slots=True)
class NativeTemporalSnapshot:
    """Transient normalized snapshot; re-read SQL before any downstream use."""

    facts: tuple[NativeTemporalFact, ...]
    evidence: tuple[TemporalEvidence, ...]
    profile: AdaptiveProfile
    document: bytes
    sha256: str


def normalize_native_temporal(
    facts: tuple[NativeTemporalFact, ...], selection: NativeTemporalSelection
) -> tuple[TemporalEvidence, ...]:
    """Normalize actual v2 facts without routing through reconstructed/v1 input."""
    if not 1 <= len(facts) <= MAX_NATIVE_TEMPORAL_SOURCES * 2:
        raise ValueError("NATIVE_TEMPORAL_FACT_BOUND")
    return normalize_native_temporal_facts(facts, selection)


def normalize_native_temporal_facts(
    facts: tuple[NativeTemporalFact, ...], selection: NativeTemporalBoundary
) -> tuple[TemporalEvidence, ...]:
    """Shared pure rules; retained capture discovers membership separately in SQL."""
    from autplay.domain.adaptive_recommendations import MAX_SOURCE_EVENTS

    if len(facts) > MAX_SOURCE_EVENTS:
        raise ValueError("NATIVE_TEMPORAL_FACT_BOUND")
    by_id = {fact.event_id: fact for fact in facts}
    if len(by_id) != len(facts):
        raise ValueError("NATIVE_TEMPORAL_FACT_BOUNDARY")
    support = {
        fact.causality.impression_event_id
        for fact in facts
        if fact.event_id in selection.event_ids and fact.causality is not None
    } - {None}
    if set(by_id) != set(selection.event_ids) | support:
        raise ValueError("NATIVE_TEMPORAL_SOURCE_MEMBERSHIP")
    seen: set[UUID] = set()
    result: list[TemporalEvidence] = []
    policy = DEFAULT_ADAPTIVE_FEATURE_POLICY
    weights = dict(policy.signal_weights)
    for fact in facts:
        if (
            fact.event_id in seen
            or fact.owner != selection.owner
            or fact.server_sequence > selection.interaction_watermark
            or fact.received_at_ms > selection.cutoff_at_ms
            or fact.received_at_ms < 0
            or fact.occurred_at_ms < 0
        ):
            raise ValueError("NATIVE_TEMPORAL_FACT_BOUNDARY")
        seen.add(fact.event_id)
        specs: list[tuple[TemporalSignal, str, OriginLane]] = []
        if fact.event_type == "RECOMMENDATION_IMPRESSION_RECORDED":
            if (
                fact.causality is None
                or fact.feedback is not None
                or fact.causality.impression_event_id is not None
                or fact.causality.impression_association_id is not None
            ):
                raise ValueError("NATIVE_TEMPORAL_IMPRESSION_BINDING")
            continue  # Causal support, never a taste signal or fabricated listen.
        if fact.event_type == "RECOMMENDATION_FEEDBACK_RECORDED":
            causal = fact.causality
            if (
                causal is None
                or causal.impression_event_id is None
                or causal.impression_association_id is None
                or fact.feedback not in {"SELECTED", "DISMISSED"}
            ):
                raise ValueError("NATIVE_TEMPORAL_FEEDBACK_BINDING")
            _validate_parent(fact, by_id)
            signal = (
                TemporalSignal.RECOMMENDATION_SELECTED
                if fact.feedback == "SELECTED"
                else TemporalSignal.RECOMMENDATION_DISMISSED
            )
            specs.append((signal, "RECOMMENDATION_FEEDBACK_V1", OriginLane.RECOMMENDATION))
        elif fact.event_type == "LISTENING_EVENT_RECORDED":
            if (
                fact.explicit_feedback not in {"NONE", "LIKE", "DISLIKE"}
                or (fact.causality is None and fact.explicit_feedback != "NONE")
                or fact.feedback is not None
                or fact.event_origin not in {"ORGANIC", "PLAYLIST", "SEARCH", "WAVE", "RECOMMENDED"}
                or (fact.event_origin == "RECOMMENDED") != (fact.causality is not None)
                or type(fact.played_ms) is not int
                or fact.played_ms < 0
                or (fact.completion_ratio is not None and not 0 <= fact.completion_ratio <= 1)
            ):
                raise ValueError("NATIVE_TEMPORAL_LISTEN_BINDING")
            if fact.causality is not None:
                _validate_parent(fact, by_id)
                lane = OriginLane.RECOMMENDATION
            else:
                lane = (
                    OriginLane.ORGANIC
                    if fact.event_origin == "ORGANIC"
                    else OriginLane.SOURCE_QUEUE
                )
            if fact.excluded_from_taste:
                specs.append(
                    (
                        TemporalSignal.EXCLUDE_FROM_TASTE,
                        "EXCLUSION_PROJECTION_V1",
                        OriginLane.EXCLUSION,
                    )
                )
            else:
                if lane is OriginLane.ORGANIC:
                    specs.append((TemporalSignal.FINALIZED_ORGANIC_LISTEN, "BASE_LISTEN_V1", lane))
                elif lane is OriginLane.RECOMMENDATION:
                    specs.append(
                        (TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN, "BASE_LISTEN_V1", lane)
                    )
                if (
                    fact.completion_ratio is not None
                    and fact.completion_ratio <= policy.skip_completion_threshold
                ) or fact.played_ms < policy.skip_played_ms_threshold:
                    specs.append(
                        (TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP, "OUTCOME_CLASSIFIER_V1", lane)
                    )
                elif (
                    fact.completion_ratio is not None
                    and fact.completion_ratio >= policy.completion_threshold
                ):
                    specs.append(
                        (TemporalSignal.FINALIZED_COMPLETION, "OUTCOME_CLASSIFIER_V1", lane)
                    )
        else:
            raise ValueError("NATIVE_TEMPORAL_SOURCE_TYPE")
        time = classify_event_time(fact.occurred_at_ms, fact.received_at_ms)
        effective = min(time.effective_at_ms, selection.cutoff_at_ms)
        classification = time.classification
        if time.effective_at_ms > selection.cutoff_at_ms:
            effective, classification = fact.received_at_ms, TimeClassification.FUTURE_SKEW_CLAMPED
        for signal, derivation, lane in specs:
            # The exact fact keeps explicit listen feedback and causality. Only
            # preference transitions can produce EXPLICIT_LIKE/DISLIKE; an
            # exclusion has no score or causal evidence mass under frozen v1.
            causal = fact.causality if lane is OriginLane.RECOMMENDATION else None
            result.append(
                TemporalEvidence(
                    evidence_id=uuid5(
                        NATIVE_TEMPORAL_NAMESPACE,
                        f"{fact.owner}:{fact.event_id}:{signal}:{derivation}",
                    ),
                    source_event_id=fact.event_id,
                    owner_user_id=fact.owner,
                    server_profile_id=uuid5(NATIVE_TEMPORAL_NAMESPACE, str(fact.owner)),
                    device_id=fact.device,
                    device_sequence=fact.device_sequence,
                    server_sequence=fact.server_sequence,
                    source_event_type=fact.event_type,
                    signal=signal,
                    derivation_key=derivation,
                    recording_id=fact.recording_id,
                    dimensions=fact.dimensions,
                    occurred_at_ms=fact.occurred_at_ms,
                    received_at_ms=fact.received_at_ms,
                    effective_at_ms=effective,
                    time_classification=classification,
                    origin_lane=lane,
                    signed_strength=weights.get(signal, 0.0),
                    quality_weight=0.0 if signal is TemporalSignal.EXCLUDE_FROM_TASTE else 1.0,
                    excluded_from_taste=signal is TemporalSignal.EXCLUDE_FROM_TASTE,
                    source_request_sha256=fact.source_request_sha256,
                    recommendation_request_id=causal.request_id if causal else None,
                    impression_event_id=causal.impression_event_id if causal else None,
                    recommendation_source_rank=causal.served_rank if causal else None,
                )
            )
    if not set(selection.event_ids) <= seen:
        raise ValueError("NATIVE_TEMPORAL_SOURCE_MEMBERSHIP")
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.effective_at_ms,
                item.received_at_ms,
                item.server_sequence or 0,
                item.evidence_id.hex,
            ),
        )
    )


def _validate_parent(fact: NativeTemporalFact, by_id: dict[UUID, NativeTemporalFact]) -> None:
    """One accepted impression supports the exact child; labels are independent."""
    causal = fact.causality
    if causal is None:
        raise ValueError("NATIVE_TEMPORAL_PARENT_CLOSURE")
    parent = by_id.get(causal.impression_event_id) if causal.impression_event_id else None
    if parent is None or parent.causality is None:
        raise ValueError("NATIVE_TEMPORAL_PARENT_CLOSURE")
    binding = parent.causality
    if (
        parent.event_type != "RECOMMENDATION_IMPRESSION_RECORDED"
        or parent.owner != fact.owner
        or (fact.event_type == "LISTENING_EVENT_RECORDED" and parent.device != fact.device)
        or parent.recording_id != fact.recording_id
        or parent.server_sequence >= fact.server_sequence
        or binding.association_id != causal.impression_association_id
        or binding.impression_event_id is not None
        or binding.impression_association_id is not None
        or any(
            getattr(binding, key) != getattr(causal, key)
            for key in (
                "request_id",
                "served_rank",
                "decision_sha256",
                "served_item_sha256",
                "serving_provenance",
                "request_surface",
                "presentation_id",
            )
        )
    ):
        raise ValueError("NATIVE_TEMPORAL_PARENT_BINDING")
