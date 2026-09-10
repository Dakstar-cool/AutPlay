"""Deterministic normalization of accepted sync truth from an exact 0026 restore."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from autplay.application.sona_source_acceptance import (
    SonaSourceProvenanceAcceptance,
    derive_reconstructed_server_profile_id,
    verify_sona_source_provenance_acceptance,
)
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    AdaptiveFeaturePolicy,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
    classify_event_time,
)

SONA_0026_EVIDENCE_ID_SCHEME: Final = "UUID5_ACCEPTANCE_OWNER_SOURCE_EVENT_SIGNAL_DERIVATION_V1"
SONA_0026_EVIDENCE_NAMESPACE: Final = uuid5(
    NAMESPACE_URL,
    "https://autplay.local/sona/0026-normalized-evidence-v1",
)
SONA_0026_NORMALIZATION_POLICY: Final = "SYNC_APPLIED_EVENT_CONTRACT_WEIGHTS_FULL_CONFIDENCE_V1"

_SOURCE_EVENT_TYPES = frozenset(
    {
        "USER_TRACK_PREFERENCE_SET",
        "LISTENING_EVENT_RECORDED",
        "RECOMMENDATION_FEEDBACK_RECORDED",
    }
)
_LISTENING_ORIGINS = frozenset({"ORGANIC", "RECOMMENDED", "PLAYLIST", "SEARCH", "WAVE"})
_PREFERENCES = frozenset({"NEUTRAL", "LIKED", "DISLIKED"})
_FEEDBACK_TYPES = frozenset({"SELECTED", "DISMISSED"})

type _EvidenceSpecification = tuple[TemporalSignal, str, OriginLane, float, float, bool]


@dataclass(frozen=True, slots=True)
class Sona0026AcceptedEvent:
    """Typed owner-bearing facts retained across the 0026 sync/inbox projections."""

    source_event_id: UUID
    owner_user_id: UUID
    device_id: UUID
    device_sequence: int
    server_sequence: int
    source_event_type: str
    recording_id: UUID
    dimensions: tuple[TemporalDimension, ...]
    occurred_at_ms: int
    received_at_ms: int
    source_request_sha256: str
    preference: str | None = None
    event_origin: str | None = None
    played_ms: int | None = None
    completion_ratio: float | None = None
    excluded_from_taste: bool = False
    feedback_type: str | None = None
    recommendation_request_id: UUID | None = None
    impression_event_id: UUID | None = None
    recommendation_source_rank: int | None = None

    def __post_init__(self) -> None:
        if self.device_sequence < 1 or self.server_sequence < 1:
            raise ValueError("0026 source event sequences must be positive")
        if self.source_event_type not in _SOURCE_EVENT_TYPES:
            raise ValueError("0026 source event type is unsupported")
        if not 1 <= len(self.dimensions) <= 32 or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("0026 source event dimensions are not unique and bounded")
        if min(self.occurred_at_ms, self.received_at_ms) < 0:
            raise ValueError("0026 source event times must be non-negative")
        if len(self.source_request_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.source_request_sha256
        ):
            raise ValueError("0026 source request hash is invalid")
        causal = (
            self.recommendation_request_id,
            self.impression_event_id,
            self.recommendation_source_rank,
        )
        causal_complete = all(value is not None for value in causal)
        causal_absent = all(value is None for value in causal)
        if not (causal_complete or causal_absent):
            raise ValueError("0026 recommendation causal references are incomplete")
        if self.recommendation_source_rank is not None and not (
            1 <= self.recommendation_source_rank <= 1_000
        ):
            raise ValueError("0026 recommendation source rank is invalid")

        if self.source_event_type == "USER_TRACK_PREFERENCE_SET":
            if (
                self.preference not in _PREFERENCES
                or self.event_origin is not None
                or self.played_ms is not None
                or self.completion_ratio is not None
                or self.feedback_type is not None
                or not causal_absent
            ):
                raise ValueError("0026 preference source event shape is invalid")
        elif self.source_event_type == "LISTENING_EVENT_RECORDED":
            if (
                self.preference is not None
                or self.event_origin not in _LISTENING_ORIGINS
                or isinstance(self.played_ms, bool)
                or not isinstance(self.played_ms, int)
                or self.played_ms < 0
                or self.feedback_type is not None
                or (
                    self.completion_ratio is not None
                    and (
                        isinstance(self.completion_ratio, bool)
                        or not isinstance(self.completion_ratio, int | float)
                        or not 0.0 <= float(self.completion_ratio) <= 1.0
                    )
                )
                or (self.event_origin == "RECOMMENDED") != causal_complete
            ):
                raise ValueError("0026 listening source event shape is invalid")
        elif (
            self.preference is not None
            or self.event_origin is not None
            or self.played_ms is not None
            or self.completion_ratio is not None
            or self.excluded_from_taste
            or self.feedback_type not in _FEEDBACK_TYPES
            or not causal_complete
        ):
            raise ValueError("0026 recommendation feedback source event shape is invalid")


def classify_sona_0026_listening_outcome(
    event: Sona0026AcceptedEvent,
    *,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> TemporalSignal | None:
    """Apply the frozen exclusion/skip/completion/neutral classifier exactly once."""

    if event.source_event_type != "LISTENING_EVENT_RECORDED":
        raise ValueError("Sona outcome classifier requires a listening event")
    assert event.played_ms is not None
    return classify_sona_0026_listening_values(
        played_ms=event.played_ms,
        completion_ratio=event.completion_ratio,
        excluded_from_taste=event.excluded_from_taste,
        policy=policy,
    )


def classify_sona_0026_listening_values(
    *,
    played_ms: int,
    completion_ratio: float | None,
    excluded_from_taste: bool,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> TemporalSignal | None:
    """Classify an exact listening projection without manufacturing an event identity."""

    if (
        isinstance(played_ms, bool)
        or not isinstance(played_ms, int)
        or played_ms < 0
        or (
            completion_ratio is not None
            and (
                isinstance(completion_ratio, bool)
                or not isinstance(completion_ratio, int | float)
                or not 0.0 <= float(completion_ratio) <= 1.0
            )
        )
        or not isinstance(excluded_from_taste, bool)
    ):
        raise ValueError("Sona listening outcome values are invalid")
    if excluded_from_taste:
        return TemporalSignal.EXCLUDE_FROM_TASTE
    ratio = completion_ratio
    if (
        ratio is not None and float(ratio) <= policy.skip_completion_threshold
    ) or played_ms < policy.skip_played_ms_threshold:
        return TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP
    if ratio is not None and float(ratio) >= policy.completion_threshold:
        return TemporalSignal.FINALIZED_COMPLETION
    return None


def normalize_sona_0026_event(
    event: Sona0026AcceptedEvent,
    *,
    acceptance: SonaSourceProvenanceAcceptance,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> tuple[TemporalEvidence, ...]:
    """Expand one accepted event into canonical R1 evidence without persisting it."""

    verify_sona_source_provenance_acceptance(acceptance)
    decision = classify_event_time(event.occurred_at_ms, event.received_at_ms, policy)
    specifications: tuple[_EvidenceSpecification, ...]
    if event.excluded_from_taste:
        specifications = (
            (
                TemporalSignal.EXCLUDE_FROM_TASTE,
                "EXCLUSION_PROJECTION_V1",
                OriginLane.EXCLUSION,
                0.0,
                0.0,
                False,
            ),
        )
    elif event.source_event_type == "USER_TRACK_PREFERENCE_SET":
        if event.preference == "LIKED":
            specifications = (
                (
                    TemporalSignal.EXPLICIT_LIKE,
                    "PREFERENCE_TRANSITION_V1",
                    OriginLane.EXPLICIT,
                    _signal_weight(policy, TemporalSignal.EXPLICIT_LIKE),
                    1.0,
                    False,
                ),
            )
        elif event.preference == "DISLIKED":
            specifications = (
                (
                    TemporalSignal.EXPLICIT_DISLIKE,
                    "PREFERENCE_TRANSITION_V1",
                    OriginLane.EXPLICIT,
                    0.0,
                    1.0,
                    False,
                ),
            )
        else:
            specifications = ()
    elif event.source_event_type == "RECOMMENDATION_FEEDBACK_RECORDED":
        signal = (
            TemporalSignal.RECOMMENDATION_SELECTED
            if event.feedback_type == "SELECTED"
            else TemporalSignal.RECOMMENDATION_DISMISSED
        )
        specifications = (
            (
                signal,
                "RECOMMENDATION_FEEDBACK_V1",
                OriginLane.RECOMMENDATION,
                _signal_weight(policy, signal),
                1.0,
                True,
            ),
        )
    else:
        origin = _listening_origin(event)
        values: list[_EvidenceSpecification] = []
        if origin is OriginLane.ORGANIC:
            signal = TemporalSignal.FINALIZED_ORGANIC_LISTEN
            values.append(
                (signal, "BASE_LISTEN_V1", origin, _signal_weight(policy, signal), 1.0, False)
            )
        elif origin is OriginLane.RECOMMENDATION:
            signal = TemporalSignal.FINALIZED_RECOMMENDATION_LISTEN
            values.append(
                (signal, "BASE_LISTEN_V1", origin, _signal_weight(policy, signal), 1.0, True)
            )
        outcome = classify_sona_0026_listening_outcome(event, policy=policy)
        if outcome in {
            TemporalSignal.FINALIZED_COMPLETION,
            TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP,
        }:
            values.append(
                (
                    outcome,
                    "OUTCOME_CLASSIFIER_V1",
                    origin,
                    _signal_weight(policy, outcome),
                    1.0,
                    origin is OriginLane.RECOMMENDATION,
                )
            )
        specifications = tuple(values)

    server_profile_id = derive_reconstructed_server_profile_id(
        acceptance,
        event.owner_user_id,
    )
    return tuple(
        _evidence(
            event,
            acceptance=acceptance,
            server_profile_id=server_profile_id,
            decision_effective_at_ms=decision.effective_at_ms,
            decision_classification=decision.classification,
            signal=signal,
            derivation_key=derivation_key,
            origin=origin,
            signed_strength=signed_strength,
            quality_weight=quality_weight,
            causal=causal,
        )
        for signal, derivation_key, origin, signed_strength, quality_weight, causal in (
            specifications
        )
    )


def normalize_sona_0026_events(
    events: tuple[Sona0026AcceptedEvent, ...],
    *,
    acceptance: SonaSourceProvenanceAcceptance,
    policy: AdaptiveFeaturePolicy = DEFAULT_ADAPTIVE_FEATURE_POLICY,
) -> tuple[TemporalEvidence, ...]:
    """Deduplicate transport identity, reject conflicts and return stable evidence order."""

    source_events: dict[tuple[UUID, UUID], Sona0026AcceptedEvent] = {}
    for event in events:
        key = (event.owner_user_id, event.source_event_id)
        prior = source_events.setdefault(key, event)
        if prior != event:
            raise ValueError("0026 source event identity conflict")
    evidence = [
        value
        for event in source_events.values()
        for value in normalize_sona_0026_event(event, acceptance=acceptance, policy=policy)
    ]
    evidence_ids = tuple(value.evidence_id for value in evidence)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("0026 normalized evidence identity conflict")
    return tuple(
        sorted(
            evidence,
            key=lambda value: (
                value.owner_user_id.hex,
                value.effective_at_ms,
                value.received_at_ms,
                value.server_sequence,
                value.evidence_id.hex,
            ),
        )
    )


def _signal_weight(policy: AdaptiveFeaturePolicy, signal: TemporalSignal) -> float:
    try:
        return dict(policy.signal_weights)[signal]
    except KeyError as error:
        raise ValueError("Sona 0026 normalization signal weight is unavailable") from error


def _listening_origin(event: Sona0026AcceptedEvent) -> OriginLane:
    if event.event_origin == "ORGANIC":
        return OriginLane.ORGANIC
    if event.event_origin == "RECOMMENDED":
        return OriginLane.RECOMMENDATION
    return OriginLane.SOURCE_QUEUE


def _evidence(
    event: Sona0026AcceptedEvent,
    *,
    acceptance: SonaSourceProvenanceAcceptance,
    server_profile_id: UUID,
    decision_effective_at_ms: int,
    decision_classification: TimeClassification,
    signal: TemporalSignal,
    derivation_key: str,
    origin: OriginLane,
    signed_strength: float,
    quality_weight: float,
    causal: bool,
) -> TemporalEvidence:
    evidence_id = uuid5(
        SONA_0026_EVIDENCE_NAMESPACE,
        ":".join(
            (
                acceptance.acceptance_sha256,
                event.owner_user_id.hex,
                event.source_event_id.hex,
                signal.value,
                derivation_key,
            )
        ),
    )
    return TemporalEvidence(
        evidence_id=evidence_id,
        source_event_id=event.source_event_id,
        owner_user_id=event.owner_user_id,
        server_profile_id=server_profile_id,
        device_id=event.device_id,
        device_sequence=event.device_sequence,
        server_sequence=event.server_sequence,
        source_event_type=event.source_event_type,
        signal=signal,
        derivation_key=derivation_key,
        recording_id=event.recording_id,
        dimensions=tuple(sorted(event.dimensions)),
        occurred_at_ms=event.occurred_at_ms,
        received_at_ms=event.received_at_ms,
        effective_at_ms=decision_effective_at_ms,
        time_classification=decision_classification,
        origin_lane=origin,
        signed_strength=signed_strength,
        quality_weight=quality_weight,
        excluded_from_taste=signal is TemporalSignal.EXCLUDE_FROM_TASTE,
        source_request_sha256=event.source_request_sha256,
        recommendation_request_id=(event.recommendation_request_id if causal else None),
        impression_event_id=event.impression_event_id if causal else None,
        recommendation_source_rank=event.recommendation_source_rank if causal else None,
    )


__all__ = (
    "SONA_0026_EVIDENCE_ID_SCHEME",
    "SONA_0026_EVIDENCE_NAMESPACE",
    "SONA_0026_NORMALIZATION_POLICY",
    "Sona0026AcceptedEvent",
    "classify_sona_0026_listening_outcome",
    "classify_sona_0026_listening_values",
    "normalize_sona_0026_event",
    "normalize_sona_0026_events",
)
