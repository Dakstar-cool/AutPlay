"""Pure P11 CPU pipeline, replay, pack and evaluator evidence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.application.recommendations import (
    BaselineUserRepresentationProvider,
    DeterministicOfflineEvaluator,
    EvaluationCase,
    EvaluationDataset,
    FreshnessCandidateGenerator,
    HistoryCandidateGenerator,
    RecommendationPipelineRunner,
    RecommendationService,
    StaticRecommendationVersionRegistry,
    baseline_pipeline_definition,
)
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    ReplayInputUnavailable,
    SnapshotTrack,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


class _MemoryRuntime:
    def __init__(self, snapshot: RecommendationInputSnapshot) -> None:
        self.snapshot = snapshot
        self.responses: dict[UUID, RecommendationResponse] = {}

    def capture(self, user_id: UUID, *, retained_until: datetime) -> RecommendationInputSnapshot:
        assert user_id == self.snapshot.tracks[0].recording_id or self.snapshot.tracks == ()
        del retained_until
        return self.snapshot

    def load(self, user_id: UUID, snapshot_id: UUID) -> RecommendationInputSnapshot | None:
        del user_id
        return self.snapshot if self.snapshot.reference.snapshot_id == snapshot_id else None

    def ensure_pipeline(self, pipeline: object) -> None:
        del pipeline

    def save(self, response: RecommendationResponse, **values: object) -> None:
        assert not values
        self.responses[response.request.recommendation_request_id] = response

    def exact(self, user_id: UUID, request_id: UUID) -> RecommendationResponse | None:
        response = self.responses.get(request_id)
        return (
            response if response is not None and response.request.query.user_id == user_id else None
        )

    def request(self, user_id: UUID, request_id: UUID) -> RecommendationRequestTrace | None:
        response = self.exact(user_id, request_id)
        return None if response is None else response.request


class _MemoryPackRepository:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

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
    ) -> None:
        del (
            user_id,
            device_id,
            response,
            offline_pack_id,
            payload_sha256,
            created_at,
            expires_at,
        )
        self.payloads.append(payload)


def _track(
    user_id: UUID,
    *,
    artist: str = "artist",
    release: str | None = None,
    availability: str = "LOCAL",
    authorized: bool = True,
    identity: str = "ACTIVE",
    preference: str = "NEUTRAL",
    excluded: bool = False,
    plays: int = 0,
    organic: int = 0,
    recommended: int = 0,
    has_ref: bool = True,
    added: int = 1_000,
) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=user_id,
        user_track_ref_id=uuid4() if has_ref else None,
        artist_key=artist,
        release_key=release,
        metadata_tokens=("STUDIO", "tag"),
        availability=availability,
        authorized=authorized,
        identity_status=identity,
        preference=preference,
        excluded=excluded,
        play_count=plays,
        organic_play_count=organic,
        recommended_play_count=recommended,
        last_played_at_ms=None if plays == 0 else 500,
        added_at_ms=added,
        release_date_ordinal=739_000,
    )


def _snapshot(tracks: tuple[SnapshotTrack, ...]) -> RecommendationInputSnapshot:
    return RecommendationInputSnapshot(
        RecommendationSnapshotRef(uuid4(), HASH_A, 7, 11, HASH_B, HASH_A),
        tracks,
        NOW + timedelta(days=30),
    )


def _query(user_id: UUID, *, limit: int = 10, seed: int = 42) -> RecommendationQuery:
    return RecommendationQuery(
        user_id,
        RecommendationSurface.RECOMMENDATIONS,
        limit=limit,
        seed=seed,
    )


def test_cold_start_without_embedding_or_sequential_surfaces_authorized_non_library_track() -> None:
    recording_id = uuid4()
    snapshot = _snapshot((_track(recording_id, has_ref=False, availability="VAULT"),))
    ranked = RecommendationPipelineRunner().run(
        _query(recording_id), snapshot, baseline_pipeline_definition()
    )

    assert [item.recording_id for item in ranked] == [recording_id]
    assert "CONTROLLED_EXPLORATION" in ranked[0].reason_codes
    assert all(
        "sequential" not in component.key for component in baseline_pipeline_definition().components
    )


def test_mandatory_filters_fail_closed_for_every_disallowed_state() -> None:
    user_id = uuid4()
    tracks = (
        _track(uuid4(), availability="PENDING"),
        _track(uuid4(), authorized=False),
        _track(uuid4(), identity="MERGED"),
        _track(uuid4(), preference="DISLIKED"),
        _track(uuid4(), excluded=True),
    )
    ranked = RecommendationPipelineRunner().run(
        _query(user_id), _snapshot(tracks), baseline_pipeline_definition()
    )
    assert ranked == ()


def test_fixed_seed_snapshot_is_deterministic_and_dedupe_preserves_all_sources() -> None:
    user_id = uuid4()
    liked = _track(user_id, preference="LIKED", plays=3, organic=2, recommended=1)
    snapshot = _snapshot((liked, _track(uuid4(), artist="other")))
    runner = RecommendationPipelineRunner()
    pipeline = baseline_pipeline_definition()

    first = runner.run(_query(user_id), snapshot, pipeline)
    second = runner.run(_query(user_id), snapshot, pipeline)

    assert first == second
    liked_item = next(item for item in first if item.recording_id == user_id)
    assert {value.source_key for value in liked_item.contributions} >= {
        "preferences",
        "history",
        "artist_release_metadata",
        "freshness",
        "exploration",
    }


def test_diversity_caps_artist_and_release_repeats_deterministically() -> None:
    user_id = uuid4()
    tracks = tuple(
        _track(
            uuid4(),
            artist="same" if index < 5 else f"artist-{index}",
            release="one" if index < 4 else f"release-{index}",
            preference="LIKED",
        )
        for index in range(8)
    )
    ranked = RecommendationPipelineRunner().run(
        _query(user_id, limit=8), _snapshot(tracks), baseline_pipeline_definition()
    )

    assert sum(item.artist_key == "same" for item in ranked) <= 2
    assert sum(item.release_key == "one" for item in ranked) <= 1


def test_recommended_only_history_is_not_treated_as_organic_affinity() -> None:
    recording_id = uuid4()
    track = _track(recording_id, plays=4, organic=0, recommended=4)
    batch = HistoryCandidateGenerator().generate(
        _query(recording_id),
        _snapshot((track,)),
        BaselineUserRepresentationProvider().prepare(_query(recording_id), _snapshot((track,))),
        10,
    )
    assert batch == ()


def test_fresh_releases_follow_artist_affinity_with_cold_start_fallback() -> None:
    user_id = uuid4()
    relevant = replace(
        _track(uuid4(), artist="liked-artist", preference="LIKED"),
        release_date_ordinal=738_900,
    )
    unrelated_newer = replace(
        _track(uuid4(), artist="unrelated"),
        release_date_ordinal=739_100,
    )
    snapshot = _snapshot((relevant, unrelated_newer))
    query = _query(user_id)
    representation = BaselineUserRepresentationProvider().prepare(query, snapshot)

    batch = FreshnessCandidateGenerator().generate(query, snapshot, representation, 10)

    assert [candidate.track.recording_id for candidate in batch] == [relevant.recording_id]

    cold_snapshot = _snapshot((replace(relevant, preference="NEUTRAL"), unrelated_newer))
    cold_representation = BaselineUserRepresentationProvider().prepare(query, cold_snapshot)
    cold_batch = FreshnessCandidateGenerator().generate(
        query, cold_snapshot, cold_representation, 10
    )
    assert {candidate.track.recording_id for candidate in cold_batch} == {
        relevant.recording_id,
        unrelated_newer.recording_id,
    }


def test_exact_and_algorithmic_replay_match_and_missing_retained_input_fails() -> None:
    user_id = uuid4()
    snapshot = _snapshot((_track(user_id, preference="LIKED"),))
    runtime = _MemoryRuntime(snapshot)
    ids = iter((uuid4(), uuid4()))
    service = RecommendationService(
        snapshots=runtime,
        traces=runtime,
        registry=StaticRecommendationVersionRegistry(),
        ids=lambda: next(ids),
        clock=lambda: NOW,
    )

    served = service.recommend(_query(user_id))
    assert service.exact_replay(user_id, served.request.recommendation_request_id) == served
    assert service.algorithmic_replay(user_id, served.request.recommendation_request_id) == served

    runtime.snapshot = _snapshot(snapshot.tracks)
    with pytest.raises(ReplayInputUnavailable):
        service.algorithmic_replay(user_id, served.request.recommendation_request_id)


def test_evaluation_report_is_reproducible_and_includes_candidate_metrics() -> None:
    user_id = uuid4()
    relevant = uuid4()
    snapshot = _snapshot((_track(relevant), _track(uuid4(), artist="other")))
    dataset = EvaluationDataset(
        dataset_id="fixture",
        dataset_version="1",
        split_id="temporal-1",
        snapshot_sha256=HASH_A,
        event_schema_version=1,
        code_revision="test",
        environment="cpu-fixture",
        catalog_recording_count=2,
        cases=(EvaluationCase(_query(user_id), snapshot, frozenset({relevant})),),
    )

    def timer() -> Iterator[float]:
        while True:
            yield 10.0
            yield 10.005

    ticks = timer()
    evaluator = DeterministicOfflineEvaluator(
        RecommendationPipelineRunner(),
        StaticRecommendationVersionRegistry(),
        monotonic=lambda: next(ticks),
    )
    first = evaluator.evaluate(dataset, k=2)
    second = evaluator.evaluate(dataset, k=2)

    assert first == second
    assert first.candidate_recall_at_100 == 1.0
    assert first.candidate_recall_at_500 == 1.0
    assert first.candidate_recall_at_1000 == 1.0
    assert first.latency_p95_ms == 5.0
    assert first.component_versions


def test_offline_pack_contract_preserves_source_rank_and_rejects_tamper_version_expiry() -> None:
    user_id = uuid4()
    runtime = _MemoryRuntime(_snapshot((_track(user_id, preference="LIKED"),)))
    packs = _MemoryPackRepository()
    ids = iter((uuid4(), uuid4()))
    service = RecommendationService(
        snapshots=runtime,
        traces=runtime,
        registry=StaticRecommendationVersionRegistry(),
        ids=lambda: next(ids),
        clock=lambda: NOW,
        packs=packs,
    )
    pack = service.offline_pack(
        RecommendationQuery(user_id, RecommendationSurface.OFFLINE_PACK, seed=8),
        device_id=uuid4(),
    )
    document = json.loads(pack.payload)

    assert document["request"]["surface"] == "offline_pack"
    assert document["items"][0]["source_rank"] == 1
    assert document["items"][0]["pack_position"] == 1
    assert document["items"][0]["offline_pack_id"] == str(pack.offline_pack_id)
    assert packs.payloads == [pack.payload]
    with pytest.raises(ValueError, match="hash"):
        replace(pack, payload=b"tampered")
    with pytest.raises(ValueError, match="format"):
        replace(pack, payload_version=2)
    with pytest.raises(ValueError, match="expiry"):
        replace(pack, expires_at=pack.created_at)
