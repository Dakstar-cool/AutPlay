"""Explicit model-independent ports for recommendation serving and evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from autplay.domain.adaptive_recommendations import AdaptiveProfile
from autplay.domain.recommendations import (
    Candidate,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    ScoredCandidate,
)
from autplay.domain.sona import (
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaSemanticId,
    SonaShadowEvidence,
    SonaTemporalSnapshot,
)


class CandidateGenerator(Protocol):
    """Produce a bounded replaceable source batch."""

    key: str
    version: str

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]: ...


class CandidatePoolComposer(Protocol):
    """Compose source batches and deduplicate by canonical Recording."""

    def compose(self, batches: Sequence[Sequence[Candidate]]) -> Sequence[Candidate]: ...


class RecommendationFilter(Protocol):
    """Apply mandatory fail-closed serving filters."""

    def apply(
        self, query: RecommendationQuery, candidates: Sequence[Candidate]
    ) -> Sequence[Candidate]: ...


class Ranker(Protocol):
    """Assign internal deterministic scores."""

    key: str
    version: str

    def score(
        self, query: RecommendationQuery, candidates: Sequence[Candidate]
    ) -> Sequence[ScoredCandidate]: ...


class Reranker(Protocol):
    """Enforce diversity and repeat control while retaining source rank."""

    key: str
    version: str

    def rerank(
        self,
        query: RecommendationQuery,
        candidates: Sequence[ScoredCandidate],
        *,
        limit: int,
        pipeline: PipelineDefinition,
    ) -> Sequence[RankedRecommendation]: ...


class PreparedUserRepresentation(Protocol):
    """Request-scoped opaque state shared without exposing model types."""

    @property
    def version(self) -> str: ...


class UserRepresentationProvider(Protocol):
    """Prepare one request-scoped representation."""

    key: str
    version: str

    def prepare(
        self, query: RecommendationQuery, snapshot: RecommendationInputSnapshot
    ) -> PreparedUserRepresentation: ...


class AdaptiveProfileReader(Protocol):
    """Load an immutable R1 profile bound to the P11 input snapshot."""

    def load(self, user_id: UUID, baseline_snapshot_id: UUID) -> AdaptiveProfile | None: ...


class SonaSemanticIdReader(Protocol):
    """Resolve learned three-level IDs from one immutable tokenizer version."""

    @property
    def tokenizer_manifest_sha256(self) -> str: ...

    def load(
        self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
    ) -> Mapping[UUID, SonaSemanticId]: ...

    def expand(
        self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
    ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]: ...


class SonaInferenceGateway(Protocol):
    """Run the isolated shared-encoder generation-and-ranking model."""

    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput: ...


class SonaTemporalSnapshotReader(Protocol):
    """Reload one complete retained R1 snapshot for exact model replay."""

    def load_sona_snapshot(
        self, user_id: UUID, temporal_snapshot_id: UUID
    ) -> SonaTemporalSnapshot | None: ...


class SonaShadowEvidenceRepository(Protocol):
    """Attach and reload one immutable model-shadow result for a served P11 request."""

    def save(self, evidence: SonaShadowEvidence) -> None: ...

    def load(self, user_id: UUID, recommendation_request_id: UUID) -> SonaShadowEvidence | None: ...


class RecommendationVersionRegistry(Protocol):
    """Resolve an immutable pipeline manifest."""

    def resolve(self, pipeline_key: str, version: str | None = None) -> PipelineDefinition: ...


class RecommendationSnapshotRepository(Protocol):
    """Create and reload immutable user/catalog input snapshots."""

    def capture(
        self, user_id: UUID, *, retained_until: datetime
    ) -> RecommendationInputSnapshot: ...

    def load(self, user_id: UUID, snapshot_id: UUID) -> RecommendationInputSnapshot | None: ...


class RecommendationTraceRepository(Protocol):
    """Atomically persist and owner-filter immutable request/item traces."""

    def ensure_pipeline(self, pipeline: PipelineDefinition) -> None: ...

    def save(self, response: RecommendationResponse) -> None: ...

    def exact(self, user_id: UUID, request_id: UUID) -> RecommendationResponse | None: ...

    def request(self, user_id: UUID, request_id: UUID) -> RecommendationRequestTrace | None: ...


class OfflinePackRepository(Protocol):
    """Persist one integrity-bound pack for the authenticated device."""

    def save(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        response: RecommendationResponse,
        offline_pack_id: UUID,
        payload: bytes,
        payload_sha256: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class TrackEmbeddingReader(Protocol):
    """Optional read-only P12 seam; the P11 baseline never requires it."""

    def exact_neighbors(
        self,
        user_id: UUID,
        recording_ids: Sequence[UUID],
        *,
        embedding_model_id: UUID,
        limit: int,
    ) -> Sequence[UUID]: ...


class OfflineRecommendationEvaluator(Protocol):
    """Evaluate the same pipeline runner against immutable fixture cases."""

    def evaluate(self, dataset: object) -> object: ...


__all__ = (
    "AdaptiveProfileReader",
    "CandidateGenerator",
    "CandidatePoolComposer",
    "OfflinePackRepository",
    "OfflineRecommendationEvaluator",
    "PreparedUserRepresentation",
    "Ranker",
    "RecommendationFilter",
    "RecommendationSnapshotRepository",
    "RecommendationTraceRepository",
    "RecommendationVersionRegistry",
    "Reranker",
    "SonaInferenceGateway",
    "SonaSemanticIdReader",
    "SonaShadowEvidenceRepository",
    "SonaTemporalSnapshotReader",
    "TrackEmbeddingReader",
    "UserRepresentationProvider",
)
