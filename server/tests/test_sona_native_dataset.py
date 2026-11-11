"""Native capture admission, causal labels, and temporal leakage regression tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest
import rfc8785
from autplay.application.adaptive_recommendations import canonical_sha256
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    recommendation_snapshot_limit,
    request_document,
)
from autplay.application.sona_native_dataset import (
    NativeCapture,
    NativeCaptureRow,
    NativeDatasetRejected,
    build_native_training_cases,
    decode_native_capture,
    materialize_native_training_example,
)
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.application.sona_training import build_sona_teacher_snapshot
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import SonaInferenceRequest, SonaSemanticId
from autplay.domain.sona_training import SonaTeacherSnapshot, SonaTeacherTarget

OWNER, DEVICE, RECORDING, REQUEST, IMPRESSION, LISTEN = (UUID(int=i) for i in range(1, 7))
START = datetime(2026, 11, 1, tzinfo=UTC)
NOW = START + timedelta(days=30)
TRAIN_END = int((START + timedelta(days=10)).timestamp() * 1000)
VALIDATION_END = int((START + timedelta(days=20)).timestamp() * 1000)


def capture(
    facts: list[dict[str, Any]],
    *,
    number: int = 4,
    days: int = 0,
    status: str = "COLLECTED",
    identity_status: str = "ACTIVE",
    policy_version: str = "1",
) -> NativeCaptureRow:
    created = START + timedelta(days=days)
    tracks = tuple(
        SnapshotTrack(
            recording_id=recording,
            user_track_ref_id=UUID(int=110) if policy_version == "2" else None,
            artist_key="artist",
            release_key=None,
            metadata_tokens=("rock",),
            availability="LOCAL",
            authorized=True,
            identity_status=identity_status,
            preference="NEUTRAL",
            excluded=False,
            play_count=0,
            organic_play_count=0,
            recommended_play_count=0,
            last_played_at_ms=None,
            added_at_ms=1,
            release_date_ordinal=None,
            owner_identity_decision_id=UUID(int=111) if policy_version == "2" else None,
            verified_audio_variant_id=UUID(int=112) if policy_version == "2" else None,
        )
        for recording in (RECORDING, UUID(int=99))
    )
    watermark = max((f.get("server_sequence", 0) for f in facts), default=0)
    snapshot = recommendation_input_snapshot_document(
        OWNER, interaction_watermark=watermark, tracks=tracks, policy_version=policy_version
    )
    reference = RecommendationSnapshotRef(
        snapshot_id=UUID(int=number + 100),
        input_snapshot_sha256=canonical_sha256(snapshot),
        interaction_watermark=watermark,
        catalog_snapshot=1,
        availability_snapshot=canonical_sha256(
            recommendation_availability_snapshot_document(tracks)
        ),
        policy_snapshot_sha256=canonical_sha256(recommendation_policy_snapshot_document(tracks)),
    )
    baseline = RecommendationInputSnapshot(
        reference, tracks, created + timedelta(days=180), policy_version
    )
    query = RecommendationQuery(OWNER, RecommendationSurface.RECOMMENDATIONS, limit=2)
    pipeline = baseline_pipeline_definition(policy_version)
    request = request_document(query, pipeline, reference)
    ranking = RecommendationPipelineRunner().run(query, baseline, pipeline)
    doc: dict[str, Any] = {
        "schema": "AUTPLAY_P11_NATIVE_COLLECTION_V1",
        "producer": "ORIGINAL_REQUEST_TRANSACTION",
        "quality_eligible": False,
        "admission_blocker": "TEMPORAL_SCHEMA2_DATASET_ADAPTER_REQUIRED",
        "collection_status": status,
        "request_id": str(UUID(int=number)),
        "owner_user_id": str(OWNER),
        "consent_revision": 1,
        "cutoff_at": created.isoformat(),
        "surface": "recommendations",
        "request": request,
        "request_sha256": canonical_sha256(request),
        "input_snapshot": snapshot,
        "input_snapshot_sha256": reference.input_snapshot_sha256,
        "ranking": recommendation_ranking_document(ranking),
        "source_facts": facts,
        "source_count_lower_bound": len(facts),
        "candidate_count": len(tracks),
        "candidate_policy_cap": recommendation_snapshot_limit(policy_version),
        "source_fact_cap": 10000,
    }
    payload = rfc8785.dumps(doc)
    return NativeCaptureRow(
        UUID(int=number),
        OWNER,
        1,
        created,
        baseline.retained_until,
        payload,
        sha256(payload).hexdigest(),
    )


def decode(row: NativeCaptureRow) -> NativeCapture:
    return decode_native_capture(row, current_revision=1, now=NOW)


def test_v2_native_capture_replays_owner_verified_provisional_universe() -> None:
    row = capture([], identity_status="PROVISIONAL", policy_version="2")
    parsed = decode(row)
    assert parsed.baseline.policy_version == "2"
    assert parsed.baseline.tracks[0].owner_identity_decision_id is not None
    assert parsed.baseline.tracks[0].identity_status == "PROVISIONAL"
    assert json.loads(row.payload)["ranking"]["items"]


def facts_for(first: NativeCaptureRow) -> list[dict[str, Any]]:
    rank = next(
        i["source_rank"]
        for i in json.loads(first.payload)["ranking"]["items"]
        if i["recording_id"] == str(RECORDING)
    )
    result: list[dict[str, Any]] = []
    for sequence, event_id, kind in (
        (1, IMPRESSION, "RECOMMENDATION_IMPRESSION_RECORDED"),
        (2, LISTEN, "LISTENING_EVENT_RECORDED"),
    ):
        timestamp = (first.created_at + timedelta(seconds=sequence * 60)).isoformat()
        causal = {
            "recommendation_request_id": str(first.request_id),
            "recording_id": str(RECORDING),
            "source_rank": rank,
            "presentation_id": str(UUID(int=90)),
            "impression_event_local_id": str(IMPRESSION),
        }
        payload = {
            "interaction_type": kind,
            "recommendation": causal,
            "event_origin": "RECOMMENDED",
            "played_ms": 60000,
            "completion_ratio": 0.95,
            "excluded_from_taste": False,
        }
        projection = {
            "user_id": str(OWNER),
            "device_id": str(DEVICE),
            "recording_id": str(RECORDING),
            "recommendation_request_id": str(first.request_id),
        }
        listen_projection = dict(
            projection,
            listening_event_id=str(event_id),
            started_at=timestamp,
            **{
                k: payload[k]
                for k in ("event_origin", "played_ms", "completion_ratio", "excluded_from_taste")
            },
        )
        interaction = dict(
            projection,
            interaction_id=str(event_id),
            payload=deepcopy(payload),
            source_rank=rank,
            presentation_id=str(UUID(int=90)),
            event_type=kind,
        )
        result.append(
            {
                "kind": "SYNC",
                "event_id": str(event_id),
                "event_type": kind,
                "server_sequence": sequence,
                "schema_version": 1,
                "aggregate_id": str(event_id),
                "aggregate_type": "LISTENING_EVENT" if sequence == 2 else "USER_INTERACTION_EVENT",
                "operation": "UPSERT",
                "created_at": timestamp,
                "payload": payload,
                "device_id": str(DEVICE),
                "device_sequence": sequence,
                "occurred_at": timestamp,
                "received_at": timestamp,
                "source_request_sha256": str(sequence) * 64,
                "listening_projection": listen_projection if sequence == 2 else None,
                "interaction_projection": interaction if sequence == 1 else None,
                "preference_binding": None,
                "recording_context": {
                    "recording_id": str(RECORDING),
                    "artist_key": "artist",
                    "release_key": None,
                },
                "provenance_matches": True,
            }
        )
    return result


def teacher(request: SonaInferenceRequest) -> SonaTeacherSnapshot:
    return build_sona_teacher_snapshot(
        source_request_sha256=request.request_sha256,
        teacher_key="fixture",
        teacher_version="1",
        teacher_manifest_sha256="c" * 64,
        targets=tuple(
            SonaTeacherTarget(c.recording_id, (0.5, 0.5, 0.5, 0.5)) for c in request.candidates
        ),
    )


def test_native_capture_to_real_training_contract_without_future_history() -> None:
    first = capture([])
    later = capture(facts_for(first), number=10, days=1)
    cases, report = build_native_training_cases(
        [decode(later), decode(first)],
        train_end_ms=TRAIN_END,
        validation_end_ms=VALIDATION_END,
    )
    assert report["structurally_valid_cases"] == 1
    case = cases[0]
    assert case.evidence == ()  # the later outcome never enters original request history
    assert case.temporal_document["schema_version"] == 2
    assert case.provenance_document["persisted_original_temporal_snapshot"] is False
    assert case.split == "train"
    example = materialize_native_training_example(
        case,
        semantic_ids={RECORDING: SonaSemanticId(1, 2, 3), UUID(int=99): SonaSemanticId(4, 5, 6)},
        tokenizer_sha256="a" * 64,
        model_manifest_sha256="b" * 64,
        teacher=teacher,
    )
    assert example.target_recording_id == RECORDING
    assert example.request.history == ()
    assert len(example.ranking_targets) == 2
    assert example.ranking_targets[0].labels[0] == 1
    replay, _ = build_native_training_cases(
        [decode(first), decode(later)], train_end_ms=TRAIN_END, validation_end_ms=VALIDATION_END
    )
    assert replay == cases


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("hash", "CAPTURE_HASH_MISMATCH"),
        ("owner", "CAPTURE_ROW_BINDING_MISMATCH"),
        ("consent", "CONSENT_REVISION_MISMATCH"),
        ("expired", "CAPTURE_EXPIRED"),
    ],
)
def test_envelope_rejections(mutation: str, reason: str) -> None:
    row = capture([])
    if mutation == "hash":
        row = replace(row, payload=row.payload + b" ")
    elif mutation == "owner":
        row = replace(row, owner_user_id=UUID(int=777))
    elif mutation == "consent":
        row = replace(row, consent_revision=2)
    elif mutation == "expired":
        row = replace(row, retained_until=START + timedelta(days=2))
    with pytest.raises(NativeDatasetRejected, match=reason):
        decode(row)


@pytest.mark.parametrize(
    "mutation", ["missing_impression", "foreign_device", "unserved", "future", "search", "excluded"]
)
def test_noncausal_or_operator_outcomes_never_become_labels(mutation: str) -> None:
    first = capture([])
    facts = facts_for(first)
    excluded = frozenset({LISTEN}) if mutation == "excluded" else frozenset()
    if mutation == "missing_impression":
        facts = facts[1:]
    elif mutation == "foreign_device":
        facts[0]["interaction_projection"]["device_id"] = str(UUID(int=444))
    elif mutation == "unserved":
        facts[1]["payload"]["recommendation"]["source_rank"] = 99
    elif mutation == "future":
        facts[1]["received_at"] = (START + timedelta(days=4)).isoformat()
    elif mutation == "search":
        facts[1]["payload"]["recommendation"] = None
        facts[1]["payload"]["event_origin"] = "SEARCH"
    later = capture(facts, number=10, days=1)
    cases, _ = build_native_training_cases(
        [decode(first), decode(later)],
        train_end_ms=TRAIN_END,
        validation_end_ms=VALIDATION_END,
        excluded_event_ids=excluded,
    )
    assert not cases


def test_provenance_gaps_count_once_and_quarantine_entire_history() -> None:
    first = capture([])
    facts = facts_for(first)
    facts[1]["provenance_matches"] = False
    rows = [
        capture(facts, number=10, days=1, status="SOURCE_PROVENANCE_GAP"),
        capture(facts, number=11, days=2, status="SOURCE_PROVENANCE_GAP"),
    ]
    cases, report = build_native_training_cases(
        [decode(r) for r in rows], train_end_ms=TRAIN_END, validation_end_ms=VALIDATION_END
    )
    assert not cases
    assert report["unique_provenance_gaps"] == 1
    assert report["rejection_counts"] == {"SOURCE_PROVENANCE_GAP": 2}


def test_purge_entire_label_window_and_require_complete_tokenizer() -> None:
    first = capture([])
    captures = [decode(first), decode(capture(facts_for(first), number=10, days=1))]
    cases, report = build_native_training_cases(
        captures, train_end_ms=TRAIN_END - 5 * 86400000, validation_end_ms=VALIDATION_END
    )
    assert not cases
    rejections = report["rejection_counts"]
    assert isinstance(rejections, dict) and rejections["PURGED_LABEL_WINDOW"] == 1
    cases, _ = build_native_training_cases(
        captures, train_end_ms=TRAIN_END, validation_end_ms=VALIDATION_END
    )
    with pytest.raises(NativeDatasetRejected, match="INCOMPLETE_TOKENIZER_COVERAGE"):
        materialize_native_training_example(
            cases[0],
            semantic_ids={RECORDING: SonaSemanticId(1, 2, 3)},
            tokenizer_sha256="a" * 64,
            model_manifest_sha256="b" * 64,
            teacher=teacher,
        )


def test_provisional_catalog_and_portable_facts_are_explicit_blockers() -> None:
    row = capture([{"kind": "PORTABLE"}], identity_status="PROVISIONAL")
    result = decode(row)
    assert set(result.blockers) == {
        "PORTABLE_DEDUPLICATION_REQUIRED",
        "NO_ELIGIBLE_CANDIDATES",
        "EMPTY_RANKING",
    }


def test_mutated_temporal_document_cannot_enter_tensor_writer() -> None:
    first = capture([])
    cases, _ = build_native_training_cases(
        [decode(first), decode(capture(facts_for(first), number=10, days=1))],
        train_end_ms=TRAIN_END,
        validation_end_ms=VALIDATION_END,
    )
    cases[0].temporal_document["cutoff_at_ms"] = 1
    with pytest.raises(NativeDatasetRejected, match="NATIVE_CASE_INTEGRITY_MISMATCH"):
        materialize_native_training_example(
            cases[0],
            semantic_ids={},
            tokenizer_sha256="a" * 64,
            model_manifest_sha256="b" * 64,
            teacher=teacher,
        )


def test_conflicting_repeated_fact_quarantines_owner_and_report_has_no_ids() -> None:
    first = capture([])
    facts = facts_for(first)
    second = capture(facts, number=10, days=1)
    facts[1]["source_request_sha256"] = "f" * 64
    third = capture(facts, number=11, days=2)
    cases, report = build_native_training_cases(
        [decode(first), decode(second), decode(third)],
        train_end_ms=TRAIN_END,
        validation_end_ms=VALIDATION_END,
    )
    assert not cases
    assert report["rejection_counts"] == {"CONFLICTING_REPEATED_FACT": 3}
    assert all(str(identifier) not in json.dumps(report) for identifier in (OWNER, DEVICE, LISTEN))
