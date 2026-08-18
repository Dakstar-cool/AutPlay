"""Pure, model-independent recommendation values for the CPU baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

SUPPORTED_CONTEXTS = frozenset({"GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"})
MAX_RECOMMENDATION_ITEMS = 100
MAX_PACK_BYTES = 524_288


class RecommendationSurface(StrEnum):
    """Stable public surfaces; values contain no serving implementation detail."""

    RECOMMENDATIONS = "recommendations"
    HOME = "home"
    OFFLINE_PACK = "offline_pack"


@dataclass(frozen=True, slots=True)
class RecommendationQuery:
    """One bounded, replayable recommendation request."""

    user_id: UUID
    surface: RecommendationSurface
    context: str = "GENERAL"
    limit: int = 25
    exploration: float = 0.2
    seed: int = 0
    schema_version: int = 1
    canonicalization_version: int = 1
    shadow: bool = False

    def __post_init__(self) -> None:
        if self.context not in SUPPORTED_CONTEXTS:
            raise ValueError("recommendation context is unsupported")
        if not 1 <= self.limit <= MAX_RECOMMENDATION_ITEMS:
            raise ValueError("recommendation limit must be between 1 and 100")
        if not 0.0 <= self.exploration <= 1.0:
            raise ValueError("exploration must be between 0 and 1")
        if self.schema_version != 1 or self.canonicalization_version != 1:
            raise ValueError("recommendation request version is unsupported")


@dataclass(frozen=True, slots=True)
class RecommendationSnapshotRef:
    """Immutable references required for deterministic algorithmic replay."""

    snapshot_id: UUID
    input_snapshot_sha256: str
    interaction_watermark: int
    catalog_snapshot: int
    availability_snapshot: str
    policy_snapshot_sha256: str

    def __post_init__(self) -> None:
        for value in (self.input_snapshot_sha256, self.policy_snapshot_sha256):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("snapshot hashes must be lowercase SHA-256")
        if self.interaction_watermark < 0 or self.catalog_snapshot < 0:
            raise ValueError("snapshot watermarks must be non-negative")


@dataclass(frozen=True, slots=True)
class SnapshotTrack:
    """Bounded catalog/user evidence consumed by every baseline component."""

    recording_id: UUID
    user_track_ref_id: UUID | None
    artist_key: str
    release_key: str | None
    metadata_tokens: tuple[str, ...]
    availability: str
    authorized: bool
    identity_status: str
    preference: str
    excluded: bool
    play_count: int
    organic_play_count: int
    recommended_play_count: int
    last_played_at_ms: int | None
    added_at_ms: int
    release_date_ordinal: int | None


@dataclass(frozen=True, slots=True)
class RecommendationInputSnapshot:
    """Retained immutable input document plus its parsed baseline values."""

    reference: RecommendationSnapshotRef
    tracks: tuple[SnapshotTrack, ...]
    retained_until: datetime


@dataclass(frozen=True, slots=True)
class ComponentVersionRef:
    """Immutable identity for one configured component."""

    key: str
    kind: str
    version: str
    config_sha256: str


@dataclass(frozen=True, slots=True)
class CandidateContribution:
    """One generator's complete contribution to a canonical Recording candidate."""

    source_key: str
    source_version: str
    source_rank: int
    raw_score: float
    provenance: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_rank < 1:
            raise ValueError("candidate source rank must be positive")


@dataclass(frozen=True, slots=True)
class Candidate:
    """Recording-deduplicated candidate retaining every source contribution."""

    track: SnapshotTrack
    contributions: tuple[CandidateContribution, ...]

    @property
    def recording_id(self) -> UUID:
        return self.track.recording_id


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """Internal deterministic heuristic score; never a probability."""

    candidate: Candidate
    score: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankedRecommendation:
    """Stable public ranked item with original source rank semantics."""

    recording_id: UUID
    source_rank: int
    score: float
    reason_code: str
    reason_codes: tuple[str, ...]
    contributions: tuple[CandidateContribution, ...]
    artist_key: str
    release_key: str | None
    section: str = "recommendations"


@dataclass(frozen=True, slots=True)
class PipelineDefinition:
    """Immutable registered pipeline manifest resolved before execution."""

    pipeline_key: str
    version: str
    implementation_revision: str
    manifest_sha256: str
    components: tuple[ComponentVersionRef, ...]
    generator_budgets: tuple[tuple[str, int], ...]
    max_artist_repeat: int = 2
    max_release_repeat: int = 1
    lifecycle_status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class RecommendationRequestTrace:
    """Replay-complete immutable request trace."""

    recommendation_request_id: UUID
    query: RecommendationQuery
    pipeline: PipelineDefinition
    snapshot: RecommendationSnapshotRef
    request_sha256: str
    canonical_request: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecommendationResponse:
    """Model-independent response returned by all recommendation backends."""

    request: RecommendationRequestTrace
    items: tuple[RankedRecommendation, ...]
    degraded_components: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HomeSection:
    """One bounded home section backed by an immutable request."""

    key: str
    title: str
    items: tuple[RankedRecommendation, ...]


@dataclass(frozen=True, slots=True)
class HomeFeed:
    """Deterministic collection of home sections."""

    recommendation_request_id: UUID
    sections: tuple[HomeSection, ...]


@dataclass(frozen=True, slots=True)
class OfflinePack:
    """Canonical RAW_JSON offline pack and its integrity metadata."""

    offline_pack_id: UUID
    recommendation_request_id: UUID
    payload_version: int
    payload_encoding: str
    payload: bytes
    payload_sha256: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.payload_version != 1 or self.payload_encoding != "RAW_JSON":
            raise ValueError("offline pack format is unsupported")
        if len(self.payload) > MAX_PACK_BYTES:
            raise ValueError("offline pack is too large")
        if sha256(self.payload).hexdigest() != self.payload_sha256:
            raise ValueError("offline pack hash mismatch")
        if self.expires_at <= self.created_at:
            raise ValueError("offline pack expiry must follow creation")


class RecommendationError(RuntimeError):
    """Stable base error for recommendation application failures."""

    code = "RECOMMENDATION_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


class ReplayInputUnavailable(RecommendationError):
    """The retained inputs required for algorithmic replay are unavailable."""

    code = "REPLAY_INPUT_UNAVAILABLE"


class RecommendationNotFound(RecommendationError):
    """A request is missing or belongs to another owner."""

    code = "RECOMMENDATION_NOT_FOUND"


__all__ = (
    "MAX_PACK_BYTES",
    "MAX_RECOMMENDATION_ITEMS",
    "SUPPORTED_CONTEXTS",
    "Candidate",
    "CandidateContribution",
    "ComponentVersionRef",
    "HomeFeed",
    "HomeSection",
    "JsonValue",
    "OfflinePack",
    "PipelineDefinition",
    "RankedRecommendation",
    "RecommendationError",
    "RecommendationInputSnapshot",
    "RecommendationNotFound",
    "RecommendationQuery",
    "RecommendationRequestTrace",
    "RecommendationResponse",
    "RecommendationSnapshotRef",
    "RecommendationSurface",
    "ReplayInputUnavailable",
    "ScoredCandidate",
    "SnapshotTrack",
)
