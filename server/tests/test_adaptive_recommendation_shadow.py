"""R1B shadow integration reuses P11 ports without serving or impression writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from autplay.application.adaptive_recommendations import (
    SHADOW_PIPELINE_KEY,
    AdaptiveRecommendationShadowService,
    ShadowAdaptiveUserRepresentation,
    ShadowAdaptiveUserRepresentationProvider,
    adaptive_profile_document,
    adaptive_profile_from_document,
    shadow_pipeline_definition,
)
from autplay.application.recommendations import (
    BaselineUserRepresentation,
    baseline_pipeline_definition,
)
from autplay.domain.adaptive_recommendations import (
    AdaptiveProfile,
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
    project_temporal_profile,
)
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)
CUTOFF_MS = int(NOW.timestamp() * 1000)
OWNER = UUID("00000000-0000-7000-8000-000000000001")
SNAPSHOT_ID = UUID("00000000-0000-7000-8000-000000000501")
HASH_A = "a" * 64
HASH_B = "b" * 64


@dataclass(slots=True)
class _ProfileReader:
    profile: AdaptiveProfile | None
    calls: int = 0
    unavailable: bool = False

    def load(self, user_id: UUID, baseline_snapshot_id: UUID) -> AdaptiveProfile | None:
        assert user_id == OWNER
        assert baseline_snapshot_id == SNAPSHOT_ID
        self.calls += 1
        if self.unavailable:
            raise RuntimeError("temporal repository unavailable")
        return self.profile


def _track(
    recording_id: UUID,
    artist_key: str,
    *,
    preference: str = "NEUTRAL",
    authorized: bool = True,
) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=recording_id,
        user_track_ref_id=None,
        artist_key=artist_key,
        release_key=None,
        metadata_tokens=("tag",),
        availability="VAULT",
        authorized=authorized,
        identity_status="ACTIVE",
        preference=preference,
        excluded=False,
        play_count=0,
        organic_play_count=0,
        recommended_play_count=0,
        last_played_at_ms=None,
        added_at_ms=CUTOFF_MS - 1_000,
        release_date_ordinal=739_000,
    )


def _snapshot(tracks: tuple[SnapshotTrack, ...]) -> RecommendationInputSnapshot:
    return RecommendationInputSnapshot(
        RecommendationSnapshotRef(SNAPSHOT_ID, HASH_A, 7, 11, HASH_B, HASH_A),
        tracks,
        NOW + timedelta(days=30),
    )


def _profile(artist_key: str) -> AdaptiveProfile:
    evidence = tuple(
        TemporalEvidence(
            evidence_id=UUID(f"00000000-0000-7000-9000-{index:012x}"),
            source_event_id=UUID(f"00000000-0000-7000-8000-{index:012x}"),
            owner_user_id=OWNER,
            server_profile_id=UUID("00000000-0000-7000-8000-000000000011"),
            device_id=UUID("00000000-0000-7000-8000-000000000201"),
            device_sequence=index,
            server_sequence=index,
            source_event_type="USER_TRACK_PREFERENCE_SET",
            signal=TemporalSignal.EXPLICIT_LIKE,
            derivation_key="PREFERENCE_TRANSITION_V1",
            recording_id=UUID(f"00000000-0000-7000-a000-{index:012x}"),
            dimensions=(TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, artist_key),),
            occurred_at_ms=CUTOFF_MS - index * 1_000,
            received_at_ms=CUTOFF_MS - index * 900,
            effective_at_ms=CUTOFF_MS - index * 1_000,
            time_classification=TimeClassification.TRUSTED_EVENT_TIME,
            origin_lane=OriginLane.EXPLICIT,
            signed_strength=1.0,
            quality_weight=1.0,
            excluded_from_taste=False,
            source_request_sha256=HASH_B,
        )
        for index in range(1, 9)
    )
    return project_temporal_profile(
        owner_user_id=OWNER,
        cutoff_at_ms=CUTOFF_MS,
        interaction_watermark=7,
        evidence=evidence,
    )


def _query(*, shadow: bool) -> RecommendationQuery:
    return RecommendationQuery(
        OWNER,
        RecommendationSurface.RECOMMENDATIONS,
        limit=10,
        seed=42,
        shadow=shadow,
    )


def test_shadow_pipeline_is_new_immutable_identity_and_p11_v1_is_unchanged() -> None:
    baseline_before = baseline_pipeline_definition()
    shadow = shadow_pipeline_definition()
    baseline_after = baseline_pipeline_definition()

    assert baseline_before == baseline_after
    assert shadow.pipeline_key == SHADOW_PIPELINE_KEY
    assert shadow.version == "1"
    assert shadow.lifecycle_status == "SHADOW"
    assert shadow.manifest_sha256 != baseline_before.manifest_sha256
    assert any(value.key == "adaptive_temporal" for value in shadow.components)


def test_provider_reads_temporal_state_only_for_shadow_and_falls_back_on_unavailability() -> None:
    snapshot = _snapshot((_track(UUID(int=1), "artist-a"),))
    reader = _ProfileReader(_profile("artist-a"))
    provider = ShadowAdaptiveUserRepresentationProvider(reader)

    served = provider.prepare(_query(shadow=False), snapshot)
    assert isinstance(served, ShadowAdaptiveUserRepresentation)
    assert served.adaptive_profile is None
    assert reader.calls == 0

    shadow = provider.prepare(_query(shadow=True), snapshot)
    assert isinstance(shadow, ShadowAdaptiveUserRepresentation)
    assert shadow.adaptive_profile == reader.profile
    assert reader.calls == 1

    reader.unavailable = True
    fallback = provider.prepare(_query(shadow=True), snapshot)
    assert isinstance(fallback, ShadowAdaptiveUserRepresentation)
    assert fallback.adaptive_profile is None


def test_missing_temporal_profile_degrades_to_byte_semantic_baseline_order() -> None:
    snapshot = _snapshot(
        (
            _track(UUID(int=1), "artist-a", preference="LIKED"),
            _track(UUID(int=2), "artist-b"),
        )
    )
    comparison = AdaptiveRecommendationShadowService(_ProfileReader(None)).compare(
        _query(shadow=True), snapshot
    )
    assert comparison.degraded_to_baseline is True
    assert comparison.shadow_items == comparison.baseline_items


def test_temporal_profile_affects_shadow_scores_but_mandatory_filter_remains_fail_closed() -> None:
    favored = UUID(int=2)
    blocked = UUID(int=3)
    snapshot = _snapshot(
        (
            _track(UUID(int=1), "artist-a", preference="LIKED"),
            _track(favored, "artist-b"),
            _track(blocked, "artist-b", authorized=False),
        )
    )
    comparison = AdaptiveRecommendationShadowService(_ProfileReader(_profile("artist-b"))).compare(
        _query(shadow=True), snapshot
    )
    baseline_favored = next(
        value for value in comparison.baseline_items if value.recording_id == favored
    )
    shadow_favored = next(
        value for value in comparison.shadow_items if value.recording_id == favored
    )

    assert comparison.degraded_to_baseline is False
    assert shadow_favored.score > baseline_favored.score
    assert "ADAPTIVE_TEMPORAL_CONTEXT" in shadow_favored.reason_codes
    assert blocked not in {value.recording_id for value in comparison.shadow_items}


def test_shadow_service_has_no_trace_pack_or_impression_write_dependency() -> None:
    service = AdaptiveRecommendationShadowService(_ProfileReader(None))
    assert set(vars(service)) == {
        "_baseline_pipeline",
        "_shadow_pipeline",
        "_baseline",
        "_shadow",
    }
    assert BaselineUserRepresentation.__dataclass_fields__["version"]


def test_adaptive_profile_canonical_document_round_trips_and_rejects_tamper() -> None:
    profile = _profile("artist-a")
    document = adaptive_profile_document(profile)
    assert adaptive_profile_from_document(document) == profile

    document["cutoff_at_ms"] = CUTOFF_MS + 1
    try:
        adaptive_profile_from_document(document)
    except ValueError as error:
        assert "hash mismatch" in str(error)
    else:
        raise AssertionError("tampered adaptive profile was accepted")
