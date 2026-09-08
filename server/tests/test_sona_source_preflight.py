"""Aggregate-only R1B source admission report tests."""

from __future__ import annotations

from hashlib import sha256

import pytest
import rfc8785

from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    build_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_preflight import (
    SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    SONA_SOURCE_PREFLIGHT_PRIVACY_BOUNDARY,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    SonaSourceAggregateCounts,
    SonaSourceAggregateSnapshot,
    build_sona_source_preflight_result,
)

ARCHIVE_SHA256 = "84f69171b92f7e2d8a5381b1965828ba8c788f197c3a8ffd730f5add9fcbc526"


def _counts(**overrides: int) -> SonaSourceAggregateCounts:
    values = {
        "replay_request_count": 12,
        "candidate_bounded_request_count": 12,
        "mature_attributed_request_count": 9,
        "history_bound_request_count": 9,
        "fully_bound_request_count": 6,
        "eligible_owner_count": 2,
        "eligible_recording_count": 10,
        "approved_embedding_model_count": 1,
        "embedded_recording_count": 12,
        "train_request_count": 3,
        "validation_request_count": 1,
        "test_request_count": 2,
        "eligible_span_ms": 30 * 24 * 60 * 60 * 1_000,
        "owner_isolation_violation_count": 0,
        "attribution_violation_count": 0,
        "source_chain_violation_count": 0,
        "hash_or_snapshot_integrity_gap_count": 0,
    }
    values.update(overrides)
    return SonaSourceAggregateCounts(**values)


def _snapshot(counts: SonaSourceAggregateCounts) -> SonaSourceAggregateSnapshot:
    return SonaSourceAggregateSnapshot(
        SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
        True,
        "repeatable read",
        counts,
    )


def test_sufficient_counts_report_is_deterministic_but_reconstruction_remains_blocked() -> None:
    first = build_sona_source_preflight_result(
        _snapshot(_counts()),
        generation_id="20260901T110359Z-ebb958b5e91e",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        source_captured_at_ms=1_788_260_694_001,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    )
    second = build_sona_source_preflight_result(
        _snapshot(_counts()),
        generation_id="20260901T110359Z-ebb958b5e91e",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        source_captured_at_ms=1_788_260_694_001,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    )

    assert first == second
    assert not first.ready_for_authorized_extraction
    assert first.document["privacy_boundary"] == SONA_SOURCE_PREFLIGHT_PRIVACY_BOUNDARY
    assert first.document["quality_eligible"] is False
    assert first.document["temporal_provenance"] == {
        "kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_binding_available": False,
        "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
        "review_acceptance_recorded": False,
        "review_acceptance_sha256": None,
    }
    assert first.document["blockers"] == [
        "ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE",
        "SERVER_PROFILE_BINDING_UNAVAILABLE",
    ]
    assert first.report_sha256 == sha256(rfc8785.dumps(first.document)).hexdigest()
    serialized = rfc8785.dumps(first.document)
    for prohibited in (b"owner_user_id", b"recording_id", b"database_url", b"source_path"):
        assert prohibited not in serialized


def test_matching_explicit_reconstruction_acceptance_resolves_only_provenance_blockers() -> None:
    acceptance = build_sona_source_provenance_acceptance(
        generation_id="20260901T110359Z-ebb958b5e91e",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        recorded_at_ms=1_788_800_000_000,
    )

    result = build_sona_source_preflight_result(
        _snapshot(_counts()),
        generation_id=acceptance.generation_id,
        encrypted_archive_sha256=acceptance.encrypted_archive_sha256,
        source_captured_at_ms=1_788_260_694_001,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
        provenance_acceptance=acceptance,
    )

    assert result.ready_for_authorized_extraction
    assert result.document["blockers"] == []
    assert result.document["quality_eligible"] is False
    assert result.document["temporal_provenance"] == {
        "kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_binding_available": False,
        "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
        "review_acceptance_recorded": True,
        "review_acceptance_sha256": acceptance.acceptance_sha256,
    }


def test_reconstruction_acceptance_must_match_exact_archive() -> None:
    acceptance = build_sona_source_provenance_acceptance(
        generation_id="different-generation",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        recorded_at_ms=1_788_800_000_000,
    )

    with pytest.raises(ValueError, match="does not match"):
        build_sona_source_preflight_result(
            _snapshot(_counts()),
            generation_id="20260901T110359Z-ebb958b5e91e",
            encrypted_archive_sha256=ARCHIVE_SHA256,
            source_captured_at_ms=1_788_260_694_001,
            declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
            provenance_acceptance=acceptance,
        )


def test_preflight_reports_every_fail_closed_blocker_without_claiming_eligibility() -> None:
    result = build_sona_source_preflight_result(
        _snapshot(
            _counts(
                replay_request_count=0,
                candidate_bounded_request_count=0,
                mature_attributed_request_count=0,
                history_bound_request_count=0,
                fully_bound_request_count=0,
                eligible_owner_count=0,
                eligible_recording_count=0,
                approved_embedding_model_count=0,
                embedded_recording_count=0,
                train_request_count=0,
                validation_request_count=0,
                test_request_count=0,
                eligible_span_ms=0,
                owner_isolation_violation_count=1,
                attribution_violation_count=1,
                source_chain_violation_count=1,
                hash_or_snapshot_integrity_gap_count=1,
            )
        ),
        generation_id="generation",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        source_captured_at_ms=1,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    )

    assert not result.ready_for_authorized_extraction
    assert result.document["quality_eligible"] is False
    assert result.document["blockers"] == [
        "ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE",
        "SERVER_PROFILE_BINDING_UNAVAILABLE",
        "NO_REPLAY_COMPLETE_P11_REQUESTS",
        "NO_BOUNDED_MANDATORY_FILTER_CANDIDATE_SETS",
        "NO_MATURE_ATTRIBUTED_LABELS",
        "NO_REPLAY_BOUND_TEMPORAL_HISTORY",
        "NO_PROVENANCE_APPROVED_ACTIVE_EMBEDDING_MODEL",
        "NO_FULLY_SOURCE_BOUND_REQUESTS",
        "TIME_SPLIT_WITH_7D_EMBARGO_INFEASIBLE",
        "OWNER_ISOLATION_VIOLATION",
        "OUTCOME_ATTRIBUTION_VIOLATION",
        "SOURCE_EVENT_CHAIN_VIOLATION",
        "HASH_OR_SNAPSHOT_INTEGRITY_GAP",
    ]


def test_aggregate_snapshot_rejects_write_capable_or_unstable_transactions() -> None:
    with pytest.raises(ValueError, match="read-only"):
        SonaSourceAggregateSnapshot(
            SONA_SOURCE_EXPECTED_ALEMBIC_HEAD, False, "repeatable read", _counts()
        )
    with pytest.raises(ValueError, match="repeatable-read"):
        SonaSourceAggregateSnapshot(
            SONA_SOURCE_EXPECTED_ALEMBIC_HEAD, True, "read committed", _counts()
        )


def test_preflight_requires_one_exact_approved_embedding_model() -> None:
    result = build_sona_source_preflight_result(
        _snapshot(_counts(approved_embedding_model_count=2)),
        generation_id="generation",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        source_captured_at_ms=1,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    )

    assert not result.ready_for_authorized_extraction
    assert result.document["blockers"] == [
        "ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE",
        "SERVER_PROFILE_BINDING_UNAVAILABLE",
        "AMBIGUOUS_PROVENANCE_APPROVED_ACTIVE_EMBEDDING_MODEL",
    ]


def test_preflight_refuses_more_requests_than_the_immutable_materializer_bound() -> None:
    result = build_sona_source_preflight_result(
        _snapshot(
            _counts(
                replay_request_count=5_000,
                candidate_bounded_request_count=5_000,
                mature_attributed_request_count=5_000,
                history_bound_request_count=5_000,
                fully_bound_request_count=5_000,
                eligible_owner_count=2,
                train_request_count=3_000,
                validation_request_count=1_000,
                test_request_count=1_000,
            )
        ),
        generation_id="generation",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        source_captured_at_ms=1,
        declared_alembic_head=SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    )

    assert not result.ready_for_authorized_extraction
    assert result.document["blockers"] == [
        "ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE",
        "SERVER_PROFILE_BINDING_UNAVAILABLE",
        "SOURCE_REQUEST_COUNT_EXCEEDS_MATERIALIZER_BOUND",
    ]


def test_counts_reject_impossible_subset_relationships() -> None:
    with pytest.raises(ValueError, match="fully-bound"):
        _counts(fully_bound_request_count=10, mature_attributed_request_count=9)
    with pytest.raises(ValueError, match="split probe"):
        _counts(train_request_count=4, validation_request_count=2, test_request_count=2)
