"""Legal state and typed-query round trips for the P02 identity ledger."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import psycopg
import pytest
from autplay.application.identity_evidence import (
    JsonValue,
    candidate_aggregate_sha256,
    canonical_query_snapshot,
)
from psycopg import Connection
from psycopg.types.json import Jsonb

from .identity_factory import (
    IdentityWorld,
    append_evaluation,
    append_policy_event,
    force_deferred_constraints,
    insert_release_set,
    insert_world,
    make_auto_candidates,
    make_candidate,
    make_candidates,
)

QUERY_TYPES = (
    "IMPORT_ENTRY",
    "USER_TRACK_REF",
    "LOCAL_AUDIO",
    "EXTERNAL_REFERENCE",
    "VAULT_OBJECT",
    "AUDIO_VARIANT",
)
TYPED_COLUMNS = (
    "import_entry_id",
    "user_track_ref_id",
    "local_audio_id",
    "external_reference_id",
    "vault_object_id",
    "audio_variant_id",
)


@pytest.mark.parametrize("query_type", QUERY_TYPES)
def test_all_six_typed_query_keys_round_trip(
    database_connection: Connection[Any], query_type: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, 1)
    decision = append_evaluation(
        database_connection,
        world.query(query_type),
        releases,
        candidates,
        state="REVIEW_REQUIRED",
        execution_mode="SHADOW",
    )
    force_deferred_constraints(database_connection)

    columns = ", ".join(TYPED_COLUMNS)
    row = database_connection.execute(
        f"""SELECT query_type, owner_user_id, device_id, {columns}
            FROM identity.match_decision WHERE decision_id = %s""",
        (decision.decision_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == query_type
    assert sum(value is not None for value in row[3:]) == 1
    assert row[3 + TYPED_COLUMNS.index(decision.query.typed_column)] == decision.query.typed_id
    assert row[1] == decision.query.owner_user_id
    assert row[2] == decision.query.device_id


@pytest.mark.parametrize(
    ("state", "mode", "candidate_count"),
    (
        ("AUTO_MATCH", "APPLIED", 2),
        ("REVIEW_REQUIRED", "SHADOW", 1),
        ("NO_MATCH", "APPLIED", 0),
        ("INTEGRITY_CONFLICT", "SHADOW", 0),
        ("DEFERRED_EVIDENCE", "SHADOW", 0),
    ),
)
def test_all_five_decision_states_round_trip(
    database_connection: Connection[Any], state: str, mode: str, candidate_count: int
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    if state == "AUTO_MATCH":
        append_policy_event(database_connection, releases, world.admin_user_id)
    candidates = (
        make_auto_candidates(database_connection, releases)
        if state == "AUTO_MATCH"
        else make_candidates(database_connection, releases, candidate_count)
    )
    conflicts: list[JsonValue] = (
        [{"code": "EXTERNAL_ID_CONFLICT", "severity": "HARD"}]
        if state == "INTEGRITY_CONFLICT"
        else []
    )
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidates,
        state=state,
        execution_mode=mode,
        hard_conflicts=conflicts,
    )
    force_deferred_constraints(database_connection)

    row = database_connection.execute(
        """
        SELECT decision_kind, execution_mode, decision_state, candidate_count,
               evidence_mode, candidate_generation_version, normalization_version,
               feature_extractor_versions, matcher_version, calibrator_version,
               threshold_set_version, evidence_tier, feature_scores, hard_conflicts,
               candidate_origins, query_snapshot_schema_version,
               snapshot_canonicalization_version, explanation_schema_version,
               actor_type, octet_length(query_snapshot_sha256),
               octet_length(candidate_evidence_sha256), octet_length(request_sha256)
        FROM identity.match_decision WHERE decision_id = %s
        """,
        (decision.decision_id,),
    ).fetchone()
    assert row is not None
    assert row[:4] == ("EVALUATION", mode, state, candidate_count)
    assert row[4:11] == (
        releases.evidence_mode,
        releases.candidate_generation_version,
        releases.normalization_version,
        releases.feature_extractor_versions,
        releases.matcher_version,
        releases.calibrator_version,
        releases.threshold_set_version,
    )
    assert row[11] == releases.evidence_tier
    assert row[13] == conflicts
    assert row[15:19] == ("1", "RFC8785", "1", "SYSTEM")
    assert row[19:] == (32, 32, 32)


@pytest.mark.parametrize("query_type", ("IMPORT_ENTRY", "USER_TRACK_REF", "LOCAL_AUDIO"))
def test_owned_query_rejects_wrong_owner_or_device(
    database_connection: Connection[Any], query_type: str
) -> None:
    world: IdentityWorld = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    query = world.query(query_type)
    if query_type == "LOCAL_AUDIO":
        bad_query = type(query)(
            query.query_type,
            query.typed_column,
            query.typed_id,
            world.other_user_id,
            query.device_id,
        )
        expected = "local audio query device owner mismatch"
    else:
        bad_query = type(query)(
            query.query_type, query.typed_column, query.typed_id, world.other_user_id
        )
        expected = "query owner mismatch"

    with (
        pytest.raises(psycopg.errors.RaiseException, match=expected),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            bad_query,
            releases,
            make_candidates(database_connection, releases, 1),
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("invalid", ("two_keys", "missing_owner", "wrong_device", "unknown_type"))
def test_typed_query_structure_rejects_invalid_combinations(
    database_connection: Connection[Any], invalid: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidate = make_candidate(world.seed_recording_id, 1, releases)
    query = (
        world.query("LOCAL_AUDIO")
        if invalid in {"missing_owner", "wrong_device"}
        else world.query("EXTERNAL_REFERENCE")
    )
    override_matrix: dict[str, dict[str, object]] = {
        "two_keys": {"vault_object_id": world.vault_object_id},
        "missing_owner": {"owner_user_id": None},
        "wrong_device": {"device_id": None},
        "unknown_type": {"query_type": "UNKNOWN"},
    }
    overrides = override_matrix[invalid]
    with pytest.raises(psycopg.errors.CheckViolation), database_connection.transaction():
        append_evaluation(
            database_connection,
            query,
            releases,
            [candidate],
            value_overrides=overrides,
        )


def test_lossless_decision_and_candidate_round_trip_with_multiple_origins_and_features(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = [
        make_candidate(
            world.seed_recording_id,
            1,
            releases,
            feature_scores=[
                {"feature": "known", "score": 0.9},
                {"feature": "future.experimental", "score": 0.42, "metadata": {"v": 7}},
            ],
            candidate_origins=[
                {"kind": "external", "opaque_evidence_id": "provider:abc"},
                {"kind": "local", "opaque_evidence_id": "vault:def"},
            ],
        ),
        make_candidate(
            insert_world(database_connection).seed_recording_id,
            2,
            releases,
            candidate_origins=[{"kind": "secondary", "opaque_evidence_id": "x:2"}],
        ),
    ]
    request_sha256 = hashlib.sha256(b"p02-lossless-request").digest()
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidates,
        actor_type="ADMIN",
        actor_user_id=world.admin_user_id,
        value_overrides={
            "idempotency_key": "p02-lossless-roundtrip",
            "request_sha256": request_sha256,
        },
    )
    force_deferred_constraints(database_connection)
    decision_cursor = database_connection.execute(
        "SELECT * FROM identity.match_decision WHERE decision_id = %s",
        (decision.decision_id,),
    )
    decision_row = decision_cursor.fetchone()
    assert decision_cursor.description is not None
    assert decision_row is not None
    stored_decision = dict(
        zip((column.name for column in decision_cursor.description), decision_row, strict=True)
    )
    query_document = canonical_query_snapshot(
        {
            "normalized_title": "p02 title",
            "normalized_artists": ["p02 artist"],
            "duration_ms": 180000,
            "version_markers": ["normalization:1"],
            "market_scope": "GLOBAL",
            "evidence_ids": [f"{decision.query.query_type.lower()}:{decision.query.typed_id}"],
        }
    )
    aggregate_sha256, _ = candidate_aggregate_sha256(
        [(candidate.rank, candidate.document.sha256) for candidate in candidates]
    )
    top1_confidence = candidates[0].confidence
    top2_confidence = candidates[1].confidence
    assert top1_confidence is not None and top2_confidence is not None
    expected_decision: dict[str, object] = {
        "decision_id": decision.decision_id,
        **decision.query.persistence_values(),
        "query_snapshot": query_document.value,
        "query_snapshot_schema_version": "1",
        "snapshot_canonicalization_version": "RFC8785",
        "query_snapshot_sha256": query_document.sha256,
        "decision_kind": "EVALUATION",
        "execution_mode": "SHADOW",
        "review_action": None,
        "reviewed_candidate_evidence_id": None,
        "candidate_recording_id": candidates[0].recording_id,
        "decision_state": "REVIEW_REQUIRED",
        "candidate_count": 2,
        "candidate_evidence_sha256": aggregate_sha256,
        "candidate_evidence_size_bytes": sum(
            candidate.document.byte_size for candidate in candidates
        ),
        "evidence_mode": releases.evidence_mode,
        "candidate_generation_version": releases.candidate_generation_version,
        "normalization_version": releases.normalization_version,
        "feature_extractor_versions": releases.feature_extractor_versions,
        "matcher_version": releases.matcher_version,
        "calibrator_version": releases.calibrator_version,
        "threshold_set_version": releases.threshold_set_version,
        "raw_score": candidates[0].raw_score,
        "confidence": top1_confidence,
        "top2_confidence": top2_confidence,
        "margin": top1_confidence - top2_confidence,
        "evidence_tier": candidates[0].evidence_tier,
        "feature_scores": candidates[0].feature_scores,
        "hard_conflicts": candidates[0].hard_conflicts,
        "candidate_origins": candidates[0].candidate_origins,
        "explanation_schema_version": "1",
        "actor_type": "ADMIN",
        "actor_user_id": world.admin_user_id,
        "idempotency_scope": "p02-identity",
        "idempotency_key": "p02-lossless-roundtrip",
        "request_sha256": request_sha256,
        "supersedes_decision_id": None,
        "supersession_reason": None,
        "decided_at": decision.decided_at,
    }
    assert stored_decision == expected_decision

    evidence_cursor = database_connection.execute(
        """SELECT * FROM identity.match_candidate_evidence
           WHERE decision_id = %s ORDER BY rank""",
        (decision.decision_id,),
    )
    evidence_rows = evidence_cursor.fetchall()
    assert evidence_cursor.description is not None
    evidence_columns = tuple(column.name for column in evidence_cursor.description)
    stored_evidence = [dict(zip(evidence_columns, row, strict=True)) for row in evidence_rows]
    assert len(stored_evidence) == 2
    for stored, expected in zip(stored_evidence, candidates, strict=True):
        evidence_id = stored.pop("match_candidate_evidence_id")
        created_at = stored.pop("created_at")
        assert isinstance(evidence_id, UUID)
        assert created_at is not None
        assert stored == {
            "decision_id": decision.decision_id,
            "recording_id": expected.recording_id,
            "rank": expected.rank,
            "raw_score": expected.raw_score,
            "confidence": expected.confidence,
            "evidence_tier": expected.evidence_tier,
            "feature_scores": expected.feature_scores,
            "hard_conflicts": expected.hard_conflicts,
            "candidate_origins": expected.candidate_origins,
            "extractor_versions": expected.extractor_versions,
            "evidence_schema_version": "1",
            "evidence_sha256": expected.document.sha256,
            "evidence_document_size_bytes": expected.document.byte_size,
        }


@pytest.mark.parametrize("mismatch", ("generator", "normalizer", "extractors", "unknown_matcher"))
def test_matcher_release_snapshot_mismatch_is_rejected(
    database_connection: Connection[Any], mismatch: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    override_matrix: dict[str, dict[str, object]] = {
        "generator": {"candidate_generation_version": "wrong"},
        "normalizer": {"normalization_version": "wrong"},
        "extractors": {"feature_extractor_versions": Jsonb({"wrong": "1"})},
        "unknown_matcher": {"matcher_version": "unknown"},
    }
    overrides = override_matrix[mismatch]
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            make_candidates(database_connection, releases, 1),
            value_overrides=overrides,
        )
        force_deferred_constraints(database_connection)


def test_raw_duplicate_decision_idempotency_key_hits_named_unique_constraint(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    first = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        make_candidates(database_connection, releases, 1),
        value_overrides={"idempotency_key": "same-key"},
    )
    force_deferred_constraints(database_connection)
    with (
        pytest.raises(psycopg.errors.UniqueViolation) as exc_info,
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("VAULT_OBJECT"),
            releases,
            make_candidates(database_connection, releases, 1),
            value_overrides={"idempotency_key": "same-key"},
        )
    assert exc_info.value.diag.constraint_name == "uq_match_decision_idempotency"
    assert first.decision_id is not None
