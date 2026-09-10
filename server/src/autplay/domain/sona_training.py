"""Immutable request-level supervision for Sona-Lite training."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from autplay.domain.sona import (
    SONA_RANKING_HEADS,
    UNKNOWN_SEMANTIC_ID,
    SonaInferenceRequest,
    SonaSemanticId,
)

SONA_TRAINING_SCHEMA_VERSION = 1
SONA_MAX_LABEL_DELAY_MS = 7 * 24 * 60 * 60 * 1_000

type SonaHeadValues = tuple[float, float, float, float]
type SonaHeadMask = tuple[bool, bool, bool, bool]


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} is not a lowercase SHA-256 digest")


def _validate_probabilities(values: SonaHeadValues, field: str) -> None:
    if len(values) != len(SONA_RANKING_HEADS) or any(
        not isfinite(value) or not 0.0 <= value <= 1.0 for value in values
    ):
        raise ValueError(f"{field} must contain one finite probability per ranking head")


@dataclass(frozen=True, slots=True)
class SonaObservedOutcome:
    """One observed post-request outcome; absence is distinct from a negative label."""

    owner_user_id: UUID
    source_request_sha256: str
    recording_id: UUID
    observed_at_ms: int
    completion: float | None = None
    like: float | None = None
    skip: float | None = None
    outcome_source_event_id: UUID | None = None
    outcome_source_request_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.source_request_sha256, "source_request_sha256")
        if self.observed_at_ms < 0:
            raise ValueError("Sona outcome observation time is invalid")
        for field, value in (
            ("completion", self.completion),
            ("like", self.like),
            ("skip", self.skip),
        ):
            if value is not None and (not isfinite(value) or not 0.0 <= value <= 1.0):
                raise ValueError(f"Sona {field} label must be a finite probability")
        if self.completion == 1.0 and self.skip == 1.0:
            raise ValueError("Sona completion and skip labels cannot both be positive")
        if (self.outcome_source_event_id is None) != (self.outcome_source_request_sha256 is None):
            raise ValueError("Sona outcome source identity is incomplete")
        if self.outcome_source_request_sha256 is not None:
            _validate_sha256(
                self.outcome_source_request_sha256,
                "outcome_source_request_sha256",
            )


@dataclass(frozen=True, slots=True)
class SonaTeacherTarget:
    """Offline teacher probabilities for one candidate in the frozen request."""

    recording_id: UUID
    probabilities: SonaHeadValues

    def __post_init__(self) -> None:
        _validate_probabilities(self.probabilities, "teacher probabilities")


@dataclass(frozen=True, slots=True)
class SonaTeacherSnapshot:
    """Immutable teacher output; the teacher is never a serving dependency."""

    source_request_sha256: str
    teacher_key: str
    teacher_version: str
    teacher_manifest_sha256: str
    targets: tuple[SonaTeacherTarget, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        _validate_sha256(self.source_request_sha256, "teacher source_request_sha256")
        _validate_sha256(self.teacher_manifest_sha256, "teacher_manifest_sha256")
        _validate_sha256(self.snapshot_sha256, "teacher snapshot_sha256")
        if not self.teacher_key or not self.teacher_version:
            raise ValueError("Sona teacher identity is required")
        recording_ids = tuple(value.recording_id for value in self.targets)
        if not recording_ids or len(recording_ids) != len(set(recording_ids)):
            raise ValueError("Sona teacher targets must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class SonaRankingTarget:
    """Masked observed labels plus teacher probabilities for one original candidate."""

    recording_id: UUID
    labels: SonaHeadValues
    label_mask: SonaHeadMask
    teacher_probabilities: SonaHeadValues

    def __post_init__(self) -> None:
        _validate_probabilities(self.labels, "ranking labels")
        _validate_probabilities(self.teacher_probabilities, "teacher probabilities")
        if len(self.label_mask) != len(SONA_RANKING_HEADS):
            raise ValueError("Sona ranking label mask has an invalid width")


@dataclass(frozen=True, slots=True)
class SonaTrainingExample:
    """Replay-safe training row committed to one original model request."""

    request: SonaInferenceRequest
    observed_at_ms: int
    target_recording_id: UUID
    target_semantic_id: SonaSemanticId
    teacher_key: str
    teacher_version: str
    teacher_manifest_sha256: str
    teacher_snapshot_sha256: str
    ranking_targets: tuple[SonaRankingTarget, ...]
    example_sha256: str
    outcome_source_event_id: UUID | None = None
    outcome_source_request_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.teacher_manifest_sha256, "teacher_manifest_sha256")
        _validate_sha256(self.teacher_snapshot_sha256, "teacher_snapshot_sha256")
        _validate_sha256(self.example_sha256, "example_sha256")
        if not self.teacher_key or not self.teacher_version:
            raise ValueError("Sona teacher identity is required")
        if (self.outcome_source_event_id is None) != (self.outcome_source_request_sha256 is None):
            raise ValueError("Sona training outcome source identity is incomplete")
        if self.outcome_source_request_sha256 is not None:
            _validate_sha256(
                self.outcome_source_request_sha256,
                "outcome_source_request_sha256",
            )
        if not (
            self.request.cutoff_at_ms
            < self.observed_at_ms
            <= self.request.cutoff_at_ms + SONA_MAX_LABEL_DELAY_MS
        ):
            raise ValueError("Sona outcome is outside the immutable label window")
        candidate_by_recording = {
            value.recording_id: value.semantic_id for value in self.request.candidates
        }
        if (
            candidate_by_recording.get(self.target_recording_id) != self.target_semantic_id
            or self.target_semantic_id == UNKNOWN_SEMANTIC_ID
        ):
            raise ValueError("Sona target is not an original tokenized candidate")
        candidate_order = tuple(value.recording_id for value in self.request.candidates)
        target_order = tuple(value.recording_id for value in self.ranking_targets)
        if target_order != candidate_order:
            raise ValueError("Sona ranking targets do not match the original candidate order")
        for target in self.ranking_targets:
            is_positive = target.recording_id == self.target_recording_id
            if not target.label_mask[3] or target.labels[3] != float(is_positive):
                raise ValueError("Sona pairwise labels must identify exactly one observed target")


@dataclass(frozen=True, slots=True)
class SonaTrainingLossWeights:
    """Immutable relative weights shared by reference and autograd objectives."""

    ntp: float = 1.0
    ranking: float = 1.0
    distillation: float = 0.5

    def __post_init__(self) -> None:
        values = (self.ntp, self.ranking, self.distillation)
        if any(not isfinite(value) or value < 0.0 for value in values) or sum(values) <= 0.0:
            raise ValueError("Sona loss weights must be finite, non-negative, and non-zero")


__all__ = (
    "SONA_MAX_LABEL_DELAY_MS",
    "SONA_TRAINING_SCHEMA_VERSION",
    "SonaHeadMask",
    "SonaHeadValues",
    "SonaObservedOutcome",
    "SonaRankingTarget",
    "SonaTeacherSnapshot",
    "SonaTeacherTarget",
    "SonaTrainingExample",
    "SonaTrainingLossWeights",
)
