"""Bounded model-facing values for the Sona-Lite shadow recommender."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from math import isfinite
from uuid import UUID

from autplay.domain.adaptive_recommendations import TemporalEvidence
from autplay.domain.recommendations import RankedRecommendation

SONA_SID_DEPTH = 3
SONA_CODEBOOK_SIZE = 32_000
SONA_MAX_HISTORY_EVENTS = 512
SONA_MAX_CANDIDATES = 1_024
SONA_MAX_SEED = (1 << 53) - 1
SONA_MAX_GENERATED_CANDIDATES = 128
SONA_RANKING_HEADS = ("completion", "like", "skip", "pairwise")


class SonaAction(IntEnum):
    """Stable event vocabulary shared by training and inference."""

    UNKNOWN = 0
    ORGANIC_LISTEN = 1
    RECOMMENDATION_LISTEN = 2
    COMPLETION = 3
    SHORT_SKIP = 4
    LIKE = 5
    DISLIKE = 6
    EXCLUDE = 7
    RECOMMENDATION_SELECTED = 8
    RECOMMENDATION_DISMISSED = 9


class SonaOrigin(IntEnum):
    """Stable source vocabulary; origin is never inferred from outcome."""

    UNKNOWN = 0
    EXPLICIT = 1
    ORGANIC = 2
    SOURCE_QUEUE = 3
    RECOMMENDATION = 4
    EXCLUSION = 5


class SonaShadowStatus(StrEnum):
    """Persisted execution state; neither value is serving truth."""

    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class SonaSemanticId:
    """Three-level learned Semantic ID with zero reserved for PAD/unknown."""

    level_1: int
    level_2: int
    level_3: int

    def __post_init__(self) -> None:
        if any(
            value < 0 or value >= SONA_CODEBOOK_SIZE
            for value in (self.level_1, self.level_2, self.level_3)
        ):
            raise ValueError("Sona Semantic ID is outside the codebook")

    @property
    def values(self) -> tuple[int, int, int]:
        return (self.level_1, self.level_2, self.level_3)


UNKNOWN_SEMANTIC_ID = SonaSemanticId(0, 0, 0)


@dataclass(frozen=True, slots=True)
class SonaHistoryEvent:
    """One chronological raw-event token consumed by the shared encoder."""

    evidence_id: UUID
    recording_id: UUID
    semantic_id: SonaSemanticId
    action: SonaAction
    origin: SonaOrigin
    age_bucket: int
    effective_at_ms: int
    server_sequence: int

    def __post_init__(self) -> None:
        if not 0 <= self.age_bucket <= 63:
            raise ValueError("Sona event age bucket is outside the vocabulary")
        if self.effective_at_ms < 0 or self.server_sequence < 1:
            raise ValueError("Sona event time or sequence is invalid")


@dataclass(frozen=True, slots=True)
class SonaCandidate:
    """One canonical track candidate expanded from a learned Semantic ID."""

    recording_id: UUID
    semantic_id: SonaSemanticId


@dataclass(frozen=True, slots=True)
class SonaInferenceRequest:
    """Immutable request-level input shared by generation and ranking."""

    owner_user_id: UUID
    temporal_snapshot_id: UUID
    baseline_snapshot_id: UUID
    cutoff_at_ms: int
    interaction_watermark: int
    tokenizer_sha256: str
    model_manifest_sha256: str
    seed: int
    history: tuple[SonaHistoryEvent, ...]
    candidates: tuple[SonaCandidate, ...]
    request_sha256: str

    def __post_init__(self) -> None:
        for digest in (
            self.tokenizer_sha256,
            self.model_manifest_sha256,
            self.request_sha256,
        ):
            if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
                raise ValueError("Sona request hash is invalid")
        if (
            self.cutoff_at_ms < 0
            or self.interaction_watermark < 0
            or not 0 <= self.seed <= SONA_MAX_SEED
        ):
            raise ValueError("Sona request watermark or seed is invalid")
        if len(self.history) > SONA_MAX_HISTORY_EVENTS:
            raise ValueError("Sona history exceeds the accepted bound")
        if not 1 <= len(self.candidates) <= SONA_MAX_CANDIDATES:
            raise ValueError("Sona candidate set exceeds the accepted bound")
        chronology = tuple(
            (value.effective_at_ms, value.server_sequence, value.evidence_id.hex)
            for value in self.history
        )
        if chronology != tuple(sorted(chronology)):
            raise ValueError("Sona history is not chronologically stable")
        if any(
            value.server_sequence > self.interaction_watermark
            or value.effective_at_ms > self.cutoff_at_ms
            for value in self.history
        ):
            raise ValueError("Sona history escapes the snapshot boundary")
        recording_ids = tuple(value.recording_id for value in self.candidates)
        if len(recording_ids) != len(set(recording_ids)):
            raise ValueError("Sona candidates contain duplicate recordings")


@dataclass(frozen=True, slots=True)
class SonaGeneratedCandidate:
    semantic_id: SonaSemanticId
    log_probability: float


@dataclass(frozen=True, slots=True)
class SonaRankedCandidate:
    recording_id: UUID
    head_scores: tuple[float, float, float, float]
    combined_score: float


@dataclass(frozen=True, slots=True)
class SonaInferenceOutput:
    """Ephemeral model output; it is not a served response or impression."""

    request_sha256: str
    generated: tuple[SonaGeneratedCandidate, ...]
    ranked: tuple[SonaRankedCandidate, ...]

    def __post_init__(self) -> None:
        _validate_sha256(self.request_sha256)
        if len(self.generated) > SONA_MAX_GENERATED_CANDIDATES:
            raise ValueError("Sona generation output exceeds the accepted bound")
        if len(self.ranked) > SONA_MAX_CANDIDATES:
            raise ValueError("Sona ranking output exceeds the accepted bound")
        if any(not isfinite(value.log_probability) for value in self.generated):
            raise ValueError("Sona generation output is not finite")
        if any(
            not isfinite(value.combined_score)
            or any(not isfinite(score) for score in value.head_scores)
            for value in self.ranked
        ):
            raise ValueError("Sona ranking output is not finite")
        recording_ids = tuple(value.recording_id for value in self.ranked)
        if len(recording_ids) != len(set(recording_ids)):
            raise ValueError("Sona ranking output contains duplicate recordings")
        stable_order = tuple(
            sorted(self.ranked, key=lambda value: (-value.combined_score, value.recording_id.hex))
        )
        if self.ranked != stable_order:
            raise ValueError("Sona ranking output is not deterministically ordered")


@dataclass(frozen=True, slots=True)
class SonaTemporalSnapshot:
    """Replay-complete R1 temporal input bound to one retained P11 snapshot."""

    owner_user_id: UUID
    temporal_snapshot_id: UUID
    temporal_snapshot_sha256: str
    feature_policy_sha256: str
    baseline_snapshot_id: UUID
    baseline_input_snapshot_sha256: str
    cutoff_at_ms: int
    interaction_watermark: int
    catalog_snapshot: int
    availability_snapshot_sha256: str
    evidence: tuple[TemporalEvidence, ...]
    retained_until: datetime

    def __post_init__(self) -> None:
        for digest in (
            self.temporal_snapshot_sha256,
            self.feature_policy_sha256,
            self.baseline_input_snapshot_sha256,
            self.availability_snapshot_sha256,
        ):
            _validate_sha256(digest)
        if self.cutoff_at_ms < 0 or self.interaction_watermark < 0 or self.catalog_snapshot < 0:
            raise ValueError("Sona temporal snapshot boundary is invalid")
        if self.retained_until.tzinfo is None:
            raise ValueError("Sona temporal snapshot retention must be timezone-aware")
        if any(value.owner_user_id != self.owner_user_id for value in self.evidence):
            raise ValueError("Sona temporal snapshot contains cross-owner evidence")
        if any(
            value.server_sequence is None
            or value.server_sequence > self.interaction_watermark
            or value.received_at_ms > self.cutoff_at_ms
            for value in self.evidence
        ):
            raise ValueError("Sona temporal evidence escapes the snapshot boundary")


@dataclass(frozen=True, slots=True)
class SonaShadowEvidence:
    """Immutable model evidence attached to a served P11 request without changing its items."""

    owner_user_id: UUID
    recommendation_request_id: UUID
    p11_request_sha256: str
    p11_pipeline_manifest_sha256: str
    p11_ranking_sha256: str
    baseline_snapshot_id: UUID
    baseline_input_snapshot_sha256: str
    temporal_snapshot_id: UUID
    temporal_snapshot_sha256: str
    feature_policy_sha256: str
    shadow_pipeline_key: str
    shadow_pipeline_version: str
    shadow_pipeline_manifest_sha256: str
    tokenizer_sha256: str
    model_manifest_sha256: str
    sona_request_sha256: str | None
    sona_output_sha256: str | None
    postprocessed_ranking_sha256: str | None
    evidence_sha256: str
    status: SonaShadowStatus
    reason: str | None
    output: SonaInferenceOutput | None
    postprocessed_ranking: tuple[RankedRecommendation, ...]
    generated_recording_ids: tuple[UUID, ...]
    generated_expansion: tuple[tuple[SonaSemanticId, tuple[UUID, ...]], ...]
    created_at: datetime

    def __post_init__(self) -> None:
        for digest in (
            self.p11_request_sha256,
            self.p11_pipeline_manifest_sha256,
            self.p11_ranking_sha256,
            self.baseline_input_snapshot_sha256,
            self.temporal_snapshot_sha256,
            self.feature_policy_sha256,
            self.shadow_pipeline_manifest_sha256,
            self.tokenizer_sha256,
            self.model_manifest_sha256,
            self.evidence_sha256,
        ):
            _validate_sha256(digest)
        for optional_digest in (
            self.sona_request_sha256,
            self.sona_output_sha256,
            self.postprocessed_ranking_sha256,
        ):
            if optional_digest is not None:
                _validate_sha256(optional_digest)
        if not self.shadow_pipeline_key or not self.shadow_pipeline_version:
            raise ValueError("Sona shadow pipeline identity is invalid")
        if self.created_at.tzinfo is None:
            raise ValueError("Sona shadow evidence time must be timezone-aware")
        if self.status is SonaShadowStatus.SUCCEEDED:
            if (
                self.reason is not None
                or self.output is None
                or self.sona_request_sha256 is None
                or self.sona_output_sha256 is None
                or self.postprocessed_ranking_sha256 is None
                or not self.postprocessed_ranking
                or not self.generated_recording_ids
                or not self.generated_expansion
                or len(set(self.generated_recording_ids)) != len(self.generated_recording_ids)
                or tuple(value.semantic_id for value in self.output.generated)
                != tuple(semantic_id for semantic_id, _ in self.generated_expansion)
                or set(self.generated_recording_ids)
                != {
                    recording_id
                    for _, recording_ids in self.generated_expansion
                    for recording_id in recording_ids
                }
                or any(not recording_ids for _, recording_ids in self.generated_expansion)
                or self.output.request_sha256 != self.sona_request_sha256
                or not {value.recording_id for value in self.postprocessed_ranking}
                <= set(self.generated_recording_ids)
                <= {value.recording_id for value in self.output.ranked}
            ):
                raise ValueError("successful Sona shadow evidence is incomplete")
        elif (
            self.reason is None
            or self.output is not None
            or self.sona_output_sha256 is not None
            or self.postprocessed_ranking_sha256 is not None
            or self.postprocessed_ranking
            or self.generated_recording_ids
            or self.generated_expansion
        ):
            raise ValueError("degraded Sona shadow evidence is invalid")


def _validate_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Sona hash is invalid")


__all__ = (
    "SONA_CODEBOOK_SIZE",
    "SONA_MAX_CANDIDATES",
    "SONA_MAX_GENERATED_CANDIDATES",
    "SONA_MAX_HISTORY_EVENTS",
    "SONA_MAX_SEED",
    "SONA_RANKING_HEADS",
    "SONA_SID_DEPTH",
    "UNKNOWN_SEMANTIC_ID",
    "SonaAction",
    "SonaCandidate",
    "SonaGeneratedCandidate",
    "SonaHistoryEvent",
    "SonaInferenceOutput",
    "SonaInferenceRequest",
    "SonaOrigin",
    "SonaRankedCandidate",
    "SonaSemanticId",
    "SonaShadowEvidence",
    "SonaShadowStatus",
    "SonaTemporalSnapshot",
)
