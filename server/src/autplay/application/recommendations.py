"""Deterministic CPU recommendation pipeline, replay, packs and evaluation."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import pairwise
from statistics import median
from time import perf_counter
from typing import Final, cast
from uuid import UUID

import rfc8785

from autplay.domain.recommendations import (
    MAX_PACK_BYTES,
    Candidate,
    CandidateContribution,
    ComponentVersionRef,
    HomeFeed,
    HomeSection,
    JsonValue,
    OfflinePack,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationNotFound,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    ReplayInputUnavailable,
    ScoredCandidate,
    SnapshotTrack,
)
from autplay.ports.recommendations import (
    CandidateGenerator,
    CandidatePoolComposer,
    OfflinePackRepository,
    PreparedUserRepresentation,
    Ranker,
    RecommendationFilter,
    RecommendationSnapshotRepository,
    RecommendationTraceRepository,
    RecommendationVersionRegistry,
    Reranker,
    UserRepresentationProvider,
)

DEFAULT_PIPELINE_KEY: Final = "cpu-baseline"
DEFAULT_PIPELINE_VERSION: Final = "1"
_COMPONENT_CONFIG_SHA256: Final = sha256(b"{}").hexdigest()
_SOURCE_REASON: Final = {
    "preferences": "LIKED_TRACK",
    "history": "LISTENING_HISTORY",
    "artist_release_metadata": "RELATED_ARTIST_RELEASE_METADATA",
    "freshness": "FRESH_CATALOG_ITEM",
    "exploration": "CONTROLLED_EXPLORATION",
}


@dataclass(frozen=True, slots=True)
class BaselineUserRepresentation:
    """Small non-model representation prepared once per request."""

    version: str
    liked_artists: frozenset[str]
    listened_artists: frozenset[str]
    liked_tokens: frozenset[str]


class BaselineUserRepresentationProvider:
    """Prepare explicit preference/history affinity without embeddings."""

    key = "baseline-user-representation"
    version = "1"

    def prepare(
        self, query: RecommendationQuery, snapshot: RecommendationInputSnapshot
    ) -> PreparedUserRepresentation:
        del query
        liked = tuple(track for track in snapshot.tracks if track.preference == "LIKED")
        listened = tuple(track for track in snapshot.tracks if track.organic_play_count > 0)
        return BaselineUserRepresentation(
            version=self.version,
            liked_artists=frozenset(track.artist_key for track in liked),
            listened_artists=frozenset(track.artist_key for track in listened),
            liked_tokens=frozenset(token for track in liked for token in track.metadata_tokens),
        )


class _BaseGenerator:
    version = "1"
    key = "base"

    def _batch(
        self,
        tracks: Iterable[tuple[SnapshotTrack, float, dict[str, JsonValue]]],
        limit: int,
    ) -> tuple[Candidate, ...]:
        ordered = sorted(tracks, key=lambda row: (-row[1], row[0].recording_id.hex))[:limit]
        return tuple(
            Candidate(
                track,
                (
                    CandidateContribution(
                        self.key,
                        self.version,
                        rank,
                        round(score, 8),
                        provenance,
                    ),
                ),
            )
            for rank, (track, score, provenance) in enumerate(ordered, 1)
        )


class PreferenceCandidateGenerator(_BaseGenerator):
    key = "preferences"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        del query, representation
        return self._batch(
            (
                (track, 1.0 + 0.05 * min(track.play_count, 5), {"preference": "LIKE"})
                for track in snapshot.tracks
                if track.preference == "LIKED"
            ),
            limit,
        )


class HistoryCandidateGenerator(_BaseGenerator):
    key = "history"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        del representation
        now_anchor = max((track.added_at_ms for track in snapshot.tracks), default=0)

        def rows() -> Iterable[tuple[SnapshotTrack, float, dict[str, JsonValue]]]:
            for track in snapshot.tracks:
                if track.organic_play_count <= 0:
                    continue
                age_days = (
                    365.0
                    if track.last_played_at_ms is None
                    else max(0.0, (now_anchor - track.last_played_at_ms) / 86_400_000)
                )
                forgotten = min(age_days / 180.0, 1.0)
                organic_weight = 0.9 if query.context == "GENERAL" else 0.8
                yield (
                    track,
                    organic_weight + 0.15 * forgotten,
                    {
                        "organic_play_count": track.organic_play_count,
                        "recommended_play_count_ignored_for_affinity": track.recommended_play_count,
                        "forgotten": forgotten >= 0.5,
                    },
                )

        return self._batch(rows(), limit)


class ArtistReleaseMetadataCandidateGenerator(_BaseGenerator):
    key = "artist_release_metadata"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        del query
        prepared = cast(BaselineUserRepresentation, representation)

        def rows() -> Iterable[tuple[SnapshotTrack, float, dict[str, JsonValue]]]:
            for track in snapshot.tracks:
                artist_match = track.artist_key in prepared.liked_artists
                history_artist = track.artist_key in prepared.listened_artists
                token_overlap = len(set(track.metadata_tokens) & prepared.liked_tokens)
                if not (artist_match or history_artist or token_overlap):
                    continue
                score = 0.55 + 0.3 * artist_match + 0.1 * history_artist + 0.02 * token_overlap
                yield (
                    track,
                    score,
                    {
                        "artist_affinity": artist_match or history_artist,
                        "metadata_token_overlap": token_overlap,
                        "release_key": track.release_key,
                    },
                )

        return self._batch(rows(), limit)


class FreshnessCandidateGenerator(_BaseGenerator):
    key = "freshness"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        del query
        prepared = cast(BaselineUserRepresentation, representation)
        affinity_artists = prepared.liked_artists | prepared.listened_artists
        newest_release = max(
            (track.release_date_ordinal or 0 for track in snapshot.tracks), default=0
        )
        return self._batch(
            (
                (
                    track,
                    0.4
                    + 0.6
                    * (
                        1.0
                        if newest_release == 0
                        else max(
                            0.0,
                            1.0 - (newest_release - (track.release_date_ordinal or 0)) / 730.0,
                        )
                    ),
                    {
                        "release_date_ordinal": track.release_date_ordinal,
                        "artist_affinity": track.artist_key in affinity_artists,
                        "cold_start_fallback": not affinity_artists,
                    },
                )
                for track in snapshot.tracks
                if not affinity_artists or track.artist_key in affinity_artists
            ),
            limit,
        )


class ExplorationCandidateGenerator(_BaseGenerator):
    key = "exploration"

    def generate(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        representation: PreparedUserRepresentation,
        limit: int,
    ) -> Sequence[Candidate]:
        del representation

        def score(track: SnapshotTrack) -> float:
            digest = sha256(f"{query.seed}:{track.recording_id}".encode()).digest()
            return int.from_bytes(digest[:8], "big") / float(2**64 - 1)

        return self._batch(
            (
                (
                    track,
                    score(track),
                    {"seeded": True, "previously_unplayed": track.play_count == 0},
                )
                for track in snapshot.tracks
                if track.preference != "LIKED" or query.exploration > 0
            ),
            limit,
        )


class DeterministicCandidatePoolComposer:
    """Deduplicate by Recording and preserve every contribution in source order."""

    def compose(self, batches: Sequence[Sequence[Candidate]]) -> Sequence[Candidate]:
        tracks: dict[UUID, SnapshotTrack] = {}
        contributions: dict[UUID, list[CandidateContribution]] = {}
        for batch in batches:
            for candidate in batch:
                tracks.setdefault(candidate.recording_id, candidate.track)
                contributions.setdefault(candidate.recording_id, []).extend(candidate.contributions)
        return tuple(
            Candidate(tracks[recording_id], tuple(contributions[recording_id]))
            for recording_id in sorted(tracks, key=lambda value: value.hex)
        )


class MandatoryRecommendationFilter:
    """Fail closed for ACL, availability, identity, dislike and explicit exclusion."""

    _ONLINE_AVAILABILITY = frozenset({"LOCAL", "VAULT", "EXTERNAL"})
    _OFFLINE_AVAILABILITY = frozenset({"LOCAL"})

    def apply(
        self, query: RecommendationQuery, candidates: Sequence[Candidate]
    ) -> Sequence[Candidate]:
        allowed = (
            self._OFFLINE_AVAILABILITY
            if query.surface is RecommendationSurface.OFFLINE_PACK
            else self._ONLINE_AVAILABILITY
        )
        return tuple(
            candidate
            for candidate in candidates
            if candidate.track.authorized
            and candidate.track.availability in allowed
            and candidate.track.identity_status == "ACTIVE"
            and candidate.track.preference != "DISLIKED"
            and not candidate.track.excluded
        )


class DeterministicHeuristicRanker:
    """Score source evidence without interpreting the result as probability."""

    key = "deterministic-heuristic-ranker"
    version = "1"
    _WEIGHTS: Final = {
        "preferences": 1.0,
        "history": 0.6,
        "artist_release_metadata": 0.8,
        "freshness": 0.5,
        "exploration": 0.35,
    }

    def score(
        self, query: RecommendationQuery, candidates: Sequence[Candidate]
    ) -> Sequence[ScoredCandidate]:
        results: list[ScoredCandidate] = []
        for candidate in candidates:
            total = 0.0
            reasons: list[str] = []
            for contribution in candidate.contributions:
                weight = self._WEIGHTS.get(contribution.source_key, 0.0)
                if contribution.source_key == "exploration":
                    weight *= query.exploration
                total += weight * contribution.raw_score
                reason = _SOURCE_REASON.get(contribution.source_key)
                if reason is not None and reason not in reasons:
                    reasons.append(reason)
            repeat_penalty = min(candidate.track.play_count, 20) * 0.0125
            total -= repeat_penalty
            if repeat_penalty:
                reasons.append("REPEAT_SUPPRESSED")
            results.append(ScoredCandidate(candidate, round(total, 8), tuple(reasons)))
        return tuple(results)


class DeterministicDiversityReranker:
    """Apply stable diversity/repeat caps with a seeded tie-break."""

    key = "deterministic-diversity-reranker"
    version = "1"

    def rerank(
        self,
        query: RecommendationQuery,
        candidates: Sequence[ScoredCandidate],
        *,
        limit: int,
        pipeline: PipelineDefinition,
    ) -> Sequence[RankedRecommendation]:
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.score,
                sha256(f"{query.seed}:{item.candidate.recording_id}".encode()).hexdigest(),
            ),
        )
        artist_counts: Counter[str] = Counter()
        release_counts: Counter[str] = Counter()
        selected: list[ScoredCandidate] = []
        for item in ordered:
            track = item.candidate.track
            if artist_counts[track.artist_key] >= pipeline.max_artist_repeat:
                continue
            if track.release_key is not None and (
                release_counts[track.release_key] >= pipeline.max_release_repeat
            ):
                continue
            selected.append(item)
            artist_counts[track.artist_key] += 1
            if track.release_key is not None:
                release_counts[track.release_key] += 1
            if len(selected) == limit:
                break
        return tuple(
            RankedRecommendation(
                recording_id=item.candidate.recording_id,
                source_rank=rank,
                score=item.score,
                reason_code=item.reason_codes[0] if item.reason_codes else "BASELINE_AFFINITY",
                reason_codes=item.reason_codes or ("BASELINE_AFFINITY",),
                contributions=item.candidate.contributions,
                artist_key=item.candidate.track.artist_key,
                release_key=item.candidate.track.release_key,
            )
            for rank, item in enumerate(selected, 1)
        )


class StaticRecommendationVersionRegistry:
    """In-process resolver for one immutable, replaceable CPU pipeline."""

    def __init__(self, pipeline: PipelineDefinition | None = None) -> None:
        self._pipeline = pipeline or baseline_pipeline_definition()

    def resolve(self, pipeline_key: str, version: str | None = None) -> PipelineDefinition:
        if pipeline_key != self._pipeline.pipeline_key or (
            version is not None and version != self._pipeline.version
        ):
            raise ReplayInputUnavailable
        return self._pipeline


def baseline_pipeline_definition() -> PipelineDefinition:
    """Return the immutable manifest identity for the initial CPU graph."""
    component_specs = (
        ("preferences", "candidate_generator", "1"),
        ("history", "candidate_generator", "1"),
        ("artist_release_metadata", "candidate_generator", "1"),
        ("freshness", "candidate_generator", "1"),
        ("exploration", "candidate_generator", "1"),
        ("mandatory-filters", "filter", "1"),
        ("deterministic-heuristic-ranker", "ranker", "1"),
        ("deterministic-diversity-reranker", "reranker", "1"),
        ("baseline-user-representation", "representation", "1"),
    )
    components = tuple(
        ComponentVersionRef(key, kind, version, _COMPONENT_CONFIG_SHA256)
        for key, kind, version in component_specs
    )
    budgets = tuple(
        (key, 1_000) for key, kind, _ in component_specs if kind == "candidate_generator"
    )
    pipeline = PipelineDefinition(
        pipeline_key=DEFAULT_PIPELINE_KEY,
        version=DEFAULT_PIPELINE_VERSION,
        implementation_revision="p11-cpu-v1",
        manifest_sha256="0" * 64,
        components=components,
        generator_budgets=budgets,
    )
    manifest = pipeline_manifest_document(pipeline)
    return PipelineDefinition(
        pipeline_key=pipeline.pipeline_key,
        version=pipeline.version,
        implementation_revision=pipeline.implementation_revision,
        manifest_sha256=sha256(_canonical_bytes(manifest)).hexdigest(),
        components=pipeline.components,
        generator_budgets=pipeline.generator_budgets,
        max_artist_repeat=pipeline.max_artist_repeat,
        max_release_repeat=pipeline.max_release_repeat,
        lifecycle_status=pipeline.lifecycle_status,
    )


def pipeline_manifest_document(pipeline: PipelineDefinition) -> dict[str, JsonValue]:
    """Serialize immutable component/config identities without routing state."""
    return {
        "pipeline_key": pipeline.pipeline_key,
        "version": pipeline.version,
        "implementation_revision": pipeline.implementation_revision,
        "request_schema_version": 1,
        "canonicalization_version": 1,
        "components": [
            {
                "key": component.key,
                "kind": component.kind,
                "version": component.version,
                "config_sha256": component.config_sha256,
            }
            for component in pipeline.components
        ],
        "generator_budgets": [[key, budget] for key, budget in pipeline.generator_budgets],
        "max_artist_repeat": pipeline.max_artist_repeat,
        "max_release_repeat": pipeline.max_release_repeat,
    }


class RecommendationPipelineRunner:
    """Run configured pure stages in the only permitted order."""

    def __init__(
        self,
        generators: Sequence[CandidateGenerator] | None = None,
        *,
        composer: CandidatePoolComposer | None = None,
        mandatory_filter: RecommendationFilter | None = None,
        ranker: Ranker | None = None,
        reranker: Reranker | None = None,
        representation_provider: UserRepresentationProvider | None = None,
    ) -> None:
        configured = generators or (
            PreferenceCandidateGenerator(),
            HistoryCandidateGenerator(),
            ArtistReleaseMetadataCandidateGenerator(),
            FreshnessCandidateGenerator(),
            ExplorationCandidateGenerator(),
        )
        self._generators = {generator.key: generator for generator in configured}
        self._composer = composer or DeterministicCandidatePoolComposer()
        self._filter = mandatory_filter or MandatoryRecommendationFilter()
        self._ranker = ranker or DeterministicHeuristicRanker()
        self._reranker = reranker or DeterministicDiversityReranker()
        self._representation = representation_provider or BaselineUserRepresentationProvider()

    def run(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        pipeline: PipelineDefinition,
    ) -> tuple[RankedRecommendation, ...]:
        scored = self.candidate_pool(query, snapshot, pipeline)
        return self.rank(query, scored, pipeline)

    def rank(
        self,
        query: RecommendationQuery,
        scored: Sequence[ScoredCandidate],
        pipeline: PipelineDefinition,
    ) -> tuple[RankedRecommendation, ...]:
        """Rerank one already-generated pool without recomputing user state."""
        return tuple(self._reranker.rerank(query, scored, limit=query.limit, pipeline=pipeline))

    def candidate_pool(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        pipeline: PipelineDefinition,
    ) -> tuple[ScoredCandidate, ...]:
        """Return the bounded scored union used by candidate evaluation metrics."""
        representation = self._representation.prepare(query, snapshot)
        batches: list[Sequence[Candidate]] = []
        for key, budget in pipeline.generator_budgets:
            generator = self._generators.get(key)
            if generator is None:
                raise ReplayInputUnavailable
            batches.append(generator.generate(query, snapshot, representation, budget))
        pool = self._composer.compose(batches)
        filtered = self._filter.apply(query, pool)
        scored = self._ranker.score(query, filtered)
        return tuple(
            sorted(
                scored,
                key=lambda item: (
                    -item.score,
                    sha256(f"{query.seed}:{item.candidate.recording_id}".encode()).hexdigest(),
                ),
            )
        )


class RecommendationService:
    """Application orchestration for serve, home, exact replay and algorithmic replay."""

    def __init__(
        self,
        *,
        snapshots: RecommendationSnapshotRepository,
        traces: RecommendationTraceRepository,
        registry: RecommendationVersionRegistry,
        ids: Callable[[], UUID],
        clock: Callable[[], datetime],
        runner: RecommendationPipelineRunner | None = None,
        packs: OfflinePackRepository | None = None,
        snapshot_retention: timedelta = timedelta(days=30),
    ) -> None:
        self._snapshots = snapshots
        self._traces = traces
        self._registry = registry
        self._ids = ids
        self._clock = clock
        self._runner = runner or RecommendationPipelineRunner()
        self._packs = packs
        self._snapshot_retention = snapshot_retention

    def recommend(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str = DEFAULT_PIPELINE_KEY,
        pipeline_version: str | None = None,
    ) -> RecommendationResponse:
        now = self._clock()
        pipeline = self._registry.resolve(pipeline_key, pipeline_version)
        snapshot = self._snapshots.capture(
            query.user_id, retained_until=now + self._snapshot_retention
        )
        return self._serve(query, pipeline, snapshot, self._ids(), now, persist=True)

    def exact_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse:
        response = self._traces.exact(user_id, request_id)
        if response is None:
            raise RecommendationNotFound
        return response

    def algorithmic_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse:
        trace = self._traces.request(user_id, request_id)
        if trace is None:
            raise RecommendationNotFound
        pipeline = self._registry.resolve(trace.pipeline.pipeline_key, trace.pipeline.version)
        if pipeline.manifest_sha256 != trace.pipeline.manifest_sha256:
            raise ReplayInputUnavailable
        snapshot = self._snapshots.load(user_id, trace.snapshot.snapshot_id)
        if snapshot is None or snapshot.reference != trace.snapshot:
            raise ReplayInputUnavailable
        return self._serve(
            trace.query,
            pipeline,
            snapshot,
            trace.recommendation_request_id,
            trace.created_at,
            persist=False,
        )

    def home(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str = DEFAULT_PIPELINE_KEY,
        pipeline_version: str | None = None,
    ) -> HomeFeed:
        if query.surface is not RecommendationSurface.HOME:
            raise ValueError("home feed requires HOME surface")
        response = self.recommend(
            query, pipeline_key=pipeline_key, pipeline_version=pipeline_version
        )
        recent = tuple(
            item for item in response.items if "FRESH_CATALOG_ITEM" in item.reason_codes
        )[: min(10, query.limit)]
        recent_ids = {item.recording_id for item in recent}
        recommendations = tuple(
            item for item in response.items if item.recording_id not in recent_ids
        )
        sections: list[HomeSection] = []
        if recent:
            sections.append(HomeSection("recent_releases", "Recent releases", recent))
        sections.append(HomeSection("recommendations", "Recommendations", recommendations))
        return HomeFeed(response.request.recommendation_request_id, tuple(sections))

    def offline_pack(
        self,
        query: RecommendationQuery,
        *,
        device_id: UUID,
        ttl: timedelta = timedelta(days=7),
        pipeline_key: str = DEFAULT_PIPELINE_KEY,
        pipeline_version: str | None = None,
    ) -> OfflinePack:
        if query.surface is not RecommendationSurface.OFFLINE_PACK:
            raise ValueError("offline pack requires OFFLINE_PACK surface")
        if self._packs is None:
            raise RuntimeError("offline pack repository is unavailable")
        response = self.recommend(
            query, pipeline_key=pipeline_key, pipeline_version=pipeline_version
        )
        pack_id = self._ids()
        created_at = self._clock()
        expires_at = created_at + ttl
        document = offline_pack_document(
            pack_id=pack_id,
            device_id=device_id,
            response=response,
            created_at=created_at,
            expires_at=expires_at,
        )
        payload = _canonical_bytes(document)
        if len(payload) > MAX_PACK_BYTES:
            raise ValueError("offline pack is too large")
        digest = sha256(payload)
        self._packs.save(
            user_id=query.user_id,
            device_id=device_id,
            response=response,
            offline_pack_id=pack_id,
            payload=payload,
            payload_sha256=digest.digest(),
            created_at=created_at,
            expires_at=expires_at,
        )
        return OfflinePack(
            offline_pack_id=pack_id,
            recommendation_request_id=response.request.recommendation_request_id,
            payload_version=1,
            payload_encoding="RAW_JSON",
            payload=payload,
            payload_sha256=digest.hexdigest(),
            created_at=created_at,
            expires_at=expires_at,
        )

    def _serve(
        self,
        query: RecommendationQuery,
        pipeline: PipelineDefinition,
        snapshot: RecommendationInputSnapshot,
        request_id: UUID,
        created_at: datetime,
        *,
        persist: bool,
    ) -> RecommendationResponse:
        canonical_request = request_document(query, pipeline, snapshot.reference)
        request_sha256 = sha256(_canonical_bytes(canonical_request)).hexdigest()
        trace = RecommendationRequestTrace(
            request_id,
            query,
            pipeline,
            snapshot.reference,
            request_sha256,
            canonical_request,
            created_at,
        )
        response = RecommendationResponse(trace, self._runner.run(query, snapshot, pipeline))
        if persist:
            self._traces.ensure_pipeline(pipeline)
            self._traces.save(response)
        return response


def request_document(
    query: RecommendationQuery,
    pipeline: PipelineDefinition,
    snapshot: RecommendationSnapshotRef,
) -> dict[str, JsonValue]:
    """Build the canonical request document hashed by serving and replay."""
    return {
        "schema_version": query.schema_version,
        "canonicalization_version": query.canonicalization_version,
        "user_id": str(query.user_id),
        "surface": query.surface.value,
        "context": query.context,
        "limit": query.limit,
        "exploration": query.exploration,
        "seed": query.seed,
        "shadow": query.shadow,
        "pipeline": {
            "key": pipeline.pipeline_key,
            "version": pipeline.version,
            "manifest_sha256": pipeline.manifest_sha256,
        },
        "snapshot": {
            "snapshot_id": str(snapshot.snapshot_id),
            "input_snapshot_sha256": snapshot.input_snapshot_sha256,
            "interaction_watermark": snapshot.interaction_watermark,
            "catalog_snapshot": snapshot.catalog_snapshot,
            "availability_snapshot": snapshot.availability_snapshot,
            "policy_snapshot_sha256": snapshot.policy_snapshot_sha256,
        },
    }


def offline_pack_document(
    *,
    pack_id: UUID,
    device_id: UUID,
    response: RecommendationResponse,
    created_at: datetime,
    expires_at: datetime,
) -> dict[str, JsonValue]:
    """Build the single canonical RAW_JSON v1 Android/server pack contract."""
    trace = response.request
    request = trace.query
    return {
        "payload_version": 1,
        "offline_pack_id": str(pack_id),
        "recommendation_request_id": str(trace.recommendation_request_id),
        "user_id": str(request.user_id),
        "device_id": str(device_id),
        "pipeline": {
            "key": trace.pipeline.pipeline_key,
            "version": trace.pipeline.version,
            "manifest_sha256": trace.pipeline.manifest_sha256,
        },
        "input_snapshot_sha256": trace.snapshot.input_snapshot_sha256,
        "catalog_snapshot": trace.snapshot.catalog_snapshot,
        "availability_snapshot": trace.snapshot.availability_snapshot,
        "created_at_ms": _epoch_ms(created_at),
        "expires_at_ms": _epoch_ms(expires_at),
        "request": {
            "schema_version": request.schema_version,
            "canonicalization_version": request.canonicalization_version,
            "surface": request.surface.value,
            "context": request.context,
            "limit": request.limit,
            "exploration": request.exploration,
            "seed": request.seed,
            "shadow": request.shadow,
        },
        "items": [
            {
                "offline_pack_id": str(pack_id),
                "recording_id": str(item.recording_id),
                "source_rank": item.source_rank,
                "pack_position": position,
                "section": item.section,
                "score": item.score,
                "reason_code": item.reason_code,
                "reason_codes": list(item.reason_codes),
                "contributions": [_contribution_document(value) for value in item.contributions],
            }
            for position, item in enumerate(response.items, 1)
        ],
    }


def _contribution_document(value: CandidateContribution) -> dict[str, JsonValue]:
    return {
        "source_key": value.source_key,
        "source_version": value.source_version,
        "source_rank": value.source_rank,
        "raw_score": value.raw_score,
        "provenance": value.provenance,
    }


def _canonical_bytes(document: dict[str, JsonValue]) -> bytes:
    try:
        return rfc8785.dumps(document)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise ValueError("recommendation document cannot be canonicalized") from error


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone aware")
    return int(value.astimezone(UTC).timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One immutable temporal-split evaluation case."""

    query: RecommendationQuery
    snapshot: RecommendationInputSnapshot
    relevant_recording_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """Versioned immutable fixture manifest for reproducible offline evaluation."""

    dataset_id: str
    dataset_version: str
    split_id: str
    snapshot_sha256: str
    event_schema_version: int
    code_revision: str
    environment: str
    catalog_recording_count: int
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Reproducible baseline metrics and complete comparison manifest."""

    dataset_id: str
    dataset_version: str
    split_id: str
    snapshot_sha256: str
    event_schema_version: int
    pipeline_key: str
    pipeline_version: str
    pipeline_manifest_sha256: str
    code_revision: str
    environment: str
    component_versions: tuple[str, ...]
    component_config_sha256s: tuple[str, ...]
    seed: int
    k: int
    candidate_recall_at_100: float
    candidate_recall_at_500: float
    candidate_recall_at_1000: float
    precision: float
    recall: float
    ndcg: float
    mrr: float
    hit_rate: float
    coverage: float
    diversity: float
    novelty: float
    repeat_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    report_sha256: str


class DeterministicOfflineEvaluator:
    """Run the serving pipeline on immutable cases and report fixed metrics."""

    def __init__(
        self,
        runner: RecommendationPipelineRunner,
        registry: RecommendationVersionRegistry,
        *,
        monotonic: Callable[[], float] = perf_counter,
    ) -> None:
        self._runner, self._registry, self._monotonic = runner, registry, monotonic

    def evaluate(self, dataset: EvaluationDataset, *, k: int = 10) -> EvaluationReport:
        if not dataset.cases or not 1 <= k <= 100:
            raise ValueError("evaluation dataset and k must be bounded")
        pipeline = self._registry.resolve(DEFAULT_PIPELINE_KEY, DEFAULT_PIPELINE_VERSION)
        precision_values: list[float] = []
        recall_values: list[float] = []
        ndcg_values: list[float] = []
        reciprocal_ranks: list[float] = []
        hits: list[float] = []
        diversities: list[float] = []
        novelties: list[float] = []
        repeats: list[float] = []
        latencies: list[float] = []
        covered: set[UUID] = set()
        candidate_recall_100: list[float] = []
        candidate_recall_500: list[float] = []
        candidate_recall_1000: list[float] = []
        for case in dataset.cases:
            started = self._monotonic()
            candidate_pool = self._runner.candidate_pool(case.query, case.snapshot, pipeline)
            ranked = self._runner.rank(case.query, candidate_pool, pipeline)[:k]
            latencies.append(max(0.0, (self._monotonic() - started) * 1000.0))
            candidate_ids = [item.candidate.recording_id for item in candidate_pool]
            candidate_recall_100.append(
                _candidate_recall(candidate_ids, case.relevant_recording_ids, 100)
            )
            candidate_recall_500.append(
                _candidate_recall(candidate_ids, case.relevant_recording_ids, 500)
            )
            candidate_recall_1000.append(
                _candidate_recall(candidate_ids, case.relevant_recording_ids, 1_000)
            )
            ids = [item.recording_id for item in ranked]
            covered.update(candidate_ids)
            relevant_positions = [
                index for index, value in enumerate(ids, 1) if value in case.relevant_recording_ids
            ]
            hit_count = len(relevant_positions)
            precision_values.append(hit_count / k)
            recall_values.append(
                0.0
                if not case.relevant_recording_ids
                else hit_count / len(case.relevant_recording_ids)
            )
            dcg = sum(1.0 / math.log2(position + 1) for position in relevant_positions)
            ideal = sum(
                1.0 / math.log2(position + 1)
                for position in range(1, min(len(case.relevant_recording_ids), k) + 1)
            )
            ndcg_values.append(0.0 if ideal == 0 else dcg / ideal)
            reciprocal_ranks.append(0.0 if not relevant_positions else 1.0 / relevant_positions[0])
            hits.append(1.0 if relevant_positions else 0.0)
            artists = [item.artist_key for item in ranked]
            diversities.append(0.0 if not artists else len(set(artists)) / len(artists))
            track_by_id = {track.recording_id: track for track in case.snapshot.tracks}
            novelties.append(
                0.0
                if not ids
                else sum(track_by_id[value].play_count == 0 for value in ids) / len(ids)
            )
            repeats.append(
                0.0
                if len(artists) < 2
                else sum(left == right for left, right in pairwise(artists)) / (len(artists) - 1)
            )
        values: dict[str, JsonValue] = {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "split_id": dataset.split_id,
            "snapshot_sha256": dataset.snapshot_sha256,
            "event_schema_version": dataset.event_schema_version,
            "pipeline_key": pipeline.pipeline_key,
            "pipeline_version": pipeline.version,
            "pipeline_manifest_sha256": pipeline.manifest_sha256,
            "code_revision": dataset.code_revision,
            "environment": dataset.environment,
            "component_versions": [
                f"{component.kind}:{component.key}:{component.version}"
                for component in pipeline.components
            ],
            "component_config_sha256s": [
                component.config_sha256 for component in pipeline.components
            ],
            "seed": dataset.cases[0].query.seed,
            "k": k,
            "candidate_recall_at_100": _mean(candidate_recall_100),
            "candidate_recall_at_500": _mean(candidate_recall_500),
            "candidate_recall_at_1000": _mean(candidate_recall_1000),
            "precision": _mean(precision_values),
            "recall": _mean(recall_values),
            "ndcg": _mean(ndcg_values),
            "mrr": _mean(reciprocal_ranks),
            "hit_rate": _mean(hits),
            "coverage": min(1.0, len(covered) / max(1, dataset.catalog_recording_count)),
            "diversity": _mean(diversities),
            "novelty": _mean(novelties),
            "repeat_rate": _mean(repeats),
            "latency_p50_ms": round(median(latencies), 6),
            "latency_p95_ms": round(_percentile(latencies, 0.95), 6),
        }
        digest = sha256(_canonical_bytes(values)).hexdigest()
        return EvaluationReport(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            split_id=dataset.split_id,
            snapshot_sha256=dataset.snapshot_sha256,
            event_schema_version=dataset.event_schema_version,
            pipeline_key=pipeline.pipeline_key,
            pipeline_version=pipeline.version,
            pipeline_manifest_sha256=pipeline.manifest_sha256,
            code_revision=dataset.code_revision,
            environment=dataset.environment,
            component_versions=tuple(cast(list[str], values["component_versions"])),
            component_config_sha256s=tuple(cast(list[str], values["component_config_sha256s"])),
            seed=dataset.cases[0].query.seed,
            k=k,
            candidate_recall_at_100=_mean(candidate_recall_100),
            candidate_recall_at_500=_mean(candidate_recall_500),
            candidate_recall_at_1000=_mean(candidate_recall_1000),
            precision=_mean(precision_values),
            recall=_mean(recall_values),
            ndcg=_mean(ndcg_values),
            mrr=_mean(reciprocal_ranks),
            hit_rate=_mean(hits),
            coverage=min(1.0, len(covered) / max(1, dataset.catalog_recording_count)),
            diversity=_mean(diversities),
            novelty=_mean(novelties),
            repeat_rate=_mean(repeats),
            latency_p50_ms=round(median(latencies), 6),
            latency_p95_ms=round(_percentile(latencies, 0.95), 6),
            report_sha256=digest,
        )


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 8)


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _candidate_recall(
    candidate_ids: Sequence[UUID], relevant_ids: frozenset[UUID], cutoff: int
) -> float:
    if not relevant_ids:
        return 0.0
    return len(set(candidate_ids[:cutoff]) & relevant_ids) / len(relevant_ids)


__all__ = (
    "DEFAULT_PIPELINE_KEY",
    "DEFAULT_PIPELINE_VERSION",
    "ArtistReleaseMetadataCandidateGenerator",
    "BaselineUserRepresentationProvider",
    "DeterministicCandidatePoolComposer",
    "DeterministicDiversityReranker",
    "DeterministicHeuristicRanker",
    "DeterministicOfflineEvaluator",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationReport",
    "ExplorationCandidateGenerator",
    "FreshnessCandidateGenerator",
    "HistoryCandidateGenerator",
    "MandatoryRecommendationFilter",
    "PreferenceCandidateGenerator",
    "RecommendationPipelineRunner",
    "RecommendationService",
    "StaticRecommendationVersionRegistry",
    "baseline_pipeline_definition",
    "offline_pack_document",
    "pipeline_manifest_document",
    "request_document",
)
