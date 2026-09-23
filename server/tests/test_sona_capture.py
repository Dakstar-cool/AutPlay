from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import rfc8785

from autplay.application.adaptive_recommendations import canonical_sha256
from autplay.application.recommendations import (
    baseline_pipeline_definition,
    recommendation_input_snapshot_document,
    request_document,
)
from autplay.application.sona_capture import (
    SonaCaptureBundleV1,
    prepare_sona_capture_bundle,
)
from autplay.domain.recommendations import (
    JsonValue,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import SonaTemporalSnapshot

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
_GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "ml"
    / "sona-capture-v1-minimal.json"
)


def _track(number: int, *, authorized: bool = True) -> SnapshotTrack:
    return SnapshotTrack(
        recording_id=UUID(int=number + 1),
        user_track_ref_id=None,
        artist_key="artist",
        release_key=None,
        metadata_tokens=(),
        availability="VAULT",
        authorized=authorized,
        identity_status="ACTIVE",
        preference="NEUTRAL",
        excluded=False,
        play_count=0,
        organic_play_count=0,
        recommended_play_count=0,
        last_played_at_ms=None,
        added_at_ms=1_000,
        release_date_ordinal=None,
    )


def _case(
    tracks: tuple[SnapshotTrack, ...],
    *,
    ids: tuple[UUID, UUID, UUID, UUID] | None = None,
) -> tuple[
    RecommendationInputSnapshot,
    RecommendationResponse,
    SonaTemporalSnapshot,
    dict[str, JsonValue],
]:
    owner, snapshot_id, request_id, temporal_id = ids or (uuid4(), uuid4(), uuid4(), uuid4())
    retained = NOW + timedelta(days=180)
    baseline_hash = canonical_sha256(
        recommendation_input_snapshot_document(owner, interaction_watermark=7, tracks=tracks)
    )
    reference = RecommendationSnapshotRef(snapshot_id, baseline_hash, 7, 1_000, "b" * 64, "c" * 64)
    baseline = RecommendationInputSnapshot(reference, tracks, retained)
    query = RecommendationQuery(owner, RecommendationSurface.RECOMMENDATIONS)
    pipeline = baseline_pipeline_definition()
    canonical_request = request_document(query, pipeline, reference)
    trace = RecommendationRequestTrace(
        request_id,
        query,
        pipeline,
        reference,
        canonical_sha256(canonical_request),
        canonical_request,
        NOW,
    )
    response = RecommendationResponse(trace, ())
    document: dict[str, JsonValue] = {
        "schema_version": 2,
        "snapshot_kind": "RECOMMENDATION_TEMPORAL_SNAPSHOT_V2",
        "snapshot_id": str(temporal_id),
        "owner_user_id": str(owner),
        "cutoff_at_ms": int(NOW.timestamp() * 1000),
        "interaction_watermark": reference.interaction_watermark,
        "catalog_snapshot": reference.catalog_snapshot,
        "availability_snapshot_sha256": reference.availability_snapshot,
        "baseline_input_snapshot_sha256": baseline_hash,
        "feature_policy": {"content_sha256": "d" * 64},
        "source_evidence": [],
        "source_evidence_sha256": canonical_sha256([]),
        "retained_until_ms": int(retained.timestamp() * 1000),
    }
    temporal_hash = canonical_sha256(document)
    document["snapshot_sha256"] = temporal_hash
    temporal = SonaTemporalSnapshot(
        owner_user_id=owner,
        temporal_snapshot_id=temporal_id,
        temporal_snapshot_sha256=temporal_hash,
        feature_policy_sha256="d" * 64,
        baseline_snapshot_id=reference.snapshot_id,
        baseline_input_snapshot_sha256=baseline_hash,
        cutoff_at_ms=int(NOW.timestamp() * 1000),
        interaction_watermark=reference.interaction_watermark,
        catalog_snapshot=reference.catalog_snapshot,
        availability_snapshot_sha256=reference.availability_snapshot,
        evidence=(),
        retained_until=retained,
    )
    return baseline, response, temporal, document


def _prepare(
    case: tuple[
        RecommendationInputSnapshot,
        RecommendationResponse,
        SonaTemporalSnapshot,
        dict[str, JsonValue],
    ],
) -> SonaCaptureBundleV1:
    baseline, response, temporal, document = case
    return prepare_sona_capture_bundle(
        baseline=baseline,
        response=response,
        temporal=temporal,
        temporal_document=document,
        consent_receipt_sha256="e" * 64,
        consent_generation=1,
    )


def test_capture_preserves_complete_filtered_membership_and_canonical_hash() -> None:
    case = _case((_track(1), _track(2, authorized=False), _track(3)))
    bundle = _prepare(case)
    body = json.loads(bundle.document)
    assert bundle.universe_count == 3
    assert bundle.eligible_count == 2
    assert body["candidate_recording_ids"] == [str(UUID(int=2)), str(UUID(int=4))]
    assert bundle.candidate_membership_sha256 == canonical_sha256(body["candidate_recording_ids"])
    assert bundle.bundle_sha256 == canonical_sha256(body)
    assert bundle.ineligibility_reason is None


def test_capture_minimal_golden_bytes_and_digest_are_frozen() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    ids = tuple(UUID(value) for value in golden["ids"])
    assert len(ids) == 4
    bundle = _prepare(_case((_track(1),), ids=(ids[0], ids[1], ids[2], ids[3])))
    assert bundle.document == rfc8785.dumps(golden["document"])
    assert bundle.bundle_sha256 == golden["bundle_sha256"]


def test_capture_over_cap_keeps_every_candidate_without_shadow_eligibility() -> None:
    case = _case(tuple(_track(number) for number in range(1_025)))
    bundle = _prepare(case)
    body = json.loads(bundle.document)
    assert bundle.universe_count == bundle.eligible_count == 1_025
    assert len(body["candidate_recording_ids"]) == 1_025
    assert bundle.ineligibility_reason == "UNIVERSE_OVER_CAP"


def test_capture_rejects_temporal_tamper_owner_retention_and_request_mismatch() -> None:
    baseline, response, temporal, document = _case((_track(1),))
    with pytest.raises(ValueError, match="temporal document mismatch"):
        _prepare((baseline, response, temporal, {**document, "cutoff_at_ms": 0}))
    with pytest.raises(ValueError, match="temporal ancestry mismatch"):
        _prepare((baseline, response, replace(temporal, owner_user_id=uuid4()), document))
    with pytest.raises(ValueError, match="cutoff differs"):
        _prepare(
            (
                baseline,
                response,
                replace(temporal, cutoff_at_ms=temporal.cutoff_at_ms - 1),
                document,
            )
        )
    with pytest.raises(ValueError, match="180-day"):
        _prepare(
            (
                replace(baseline, retained_until=NOW + timedelta(days=179)),
                response,
                temporal,
                document,
            )
        )
    bad_trace = replace(response.request, request_sha256="f" * 64)
    with pytest.raises(ValueError, match="request hash mismatch"):
        _prepare((baseline, replace(response, request=bad_trace), temporal, document))
