"""Candidate-set sealing and deferred-validation tests."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import replace
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from autplay.application.identity_evidence import JsonValue
from psycopg import Connection

from .identity_factory import (
    append_evaluation,
    append_policy_event,
    force_deferred_constraints,
    insert_candidate_rows,
    insert_recording,
    insert_release_set,
    insert_world,
    make_auto_candidates,
    make_candidate,
    make_candidates,
)


def _aggregate_for_rows(candidates: list[Any]) -> bytes:
    return hashlib.sha256(
        b"".join(
            struct.pack("!i", candidate.rank) + candidate.document.sha256
            for candidate in candidates
        )
    ).digest()


@pytest.mark.parametrize("count", (0, 1, 2, 100))
def test_candidate_sets_seal_at_all_legal_cardinalities(
    database_connection: Connection[Any], count: int
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, count)
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidates,
        state="NO_MATCH" if count == 0 else "REVIEW_REQUIRED",
        execution_mode="APPLIED" if count == 0 else "SHADOW",
    )
    force_deferred_constraints(database_connection)

    row = database_connection.execute(
        """
        SELECT d.candidate_count, d.candidate_evidence_sha256,
               d.candidate_evidence_size_bytes, count(e.*),
               COALESCE(sum(e.evidence_document_size_bytes), 0)
        FROM identity.match_decision d
        LEFT JOIN identity.match_candidate_evidence e USING (decision_id)
        WHERE d.decision_id = %s
        GROUP BY d.decision_id
        """,
        (decision.decision_id,),
    ).fetchone()
    assert row == (
        count,
        _aggregate_for_rows(candidates),
        sum(candidate.document.byte_size for candidate in candidates),
        count,
        sum(candidate.document.byte_size for candidate in candidates),
    )


@pytest.mark.parametrize("override", ("count", "hash", "size"))
def test_wrong_candidate_seal_is_rejected(
    database_connection: Connection[Any], override: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, 2)
    candidate_count = 1 if override == "count" else None
    aggregate_hash = b"x" * 32 if override == "hash" else None
    aggregate_size = (
        sum(candidate.document.byte_size for candidate in candidates) + 1
        if override == "size"
        else None
    )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="candidate evidence seal mismatch"),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            candidate_count=candidate_count,
            candidate_evidence_sha256=aggregate_hash,
            candidate_evidence_size_bytes=aggregate_size,
        )
        force_deferred_constraints(database_connection)


def test_candidate_count_above_one_hundred_is_rejected(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    with (
        pytest.raises(psycopg.errors.CheckViolation) as exc_info,
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            [],
            state="NO_MATCH",
            execution_mode="APPLIED",
            candidate_count=101,
        )
    assert exc_info.value.diag.constraint_name == "match_decision_candidate_count_check"


@pytest.mark.parametrize(
    "invalid_kind", ("gap", "rank_duplicate", "recording_duplicate", "rank_101")
)
def test_invalid_candidate_rank_or_identity_is_rejected(
    database_connection: Connection[Any], invalid_kind: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, 2)
    if invalid_kind == "gap":
        candidates[1] = replace(candidates[1], rank=3)
        expected_error: type[psycopg.Error] = psycopg.errors.RaiseException
        expected_constraint = None
    elif invalid_kind == "rank_duplicate":
        candidates[1] = replace(candidates[1], rank=1)
        expected_error = psycopg.errors.UniqueViolation
        expected_constraint = "uq_match_candidate_evidence_rank"
    elif invalid_kind == "recording_duplicate":
        candidates[1] = replace(candidates[1], recording_id=candidates[0].recording_id)
        expected_error = psycopg.errors.UniqueViolation
        expected_constraint = "uq_match_candidate_evidence_recording"
    else:
        candidates = [replace(candidates[0], rank=101)]
        expected_error = psycopg.errors.CheckViolation
        expected_constraint = "match_candidate_evidence_rank_check"

    with pytest.raises(expected_error) as exc_info, database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            candidate_evidence_sha256=_aggregate_for_rows(candidates),
        )
        force_deferred_constraints(database_connection)
    if expected_constraint is None:
        assert "candidate evidence seal mismatch" in str(exc_info.value)
    else:
        assert exc_info.value.diag.constraint_name == expected_constraint


@pytest.mark.parametrize("mismatch", ("selected_recording", "top_one_json", "top_two"))
def test_decision_summary_must_equal_ranked_evidence(
    database_connection: Connection[Any], mismatch: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, 2)
    target_recording_id = None
    use_default_target = True
    decision_conflicts: list[JsonValue] | None = None
    top2_confidence = None
    use_default_top2 = True
    if mismatch == "selected_recording":
        target_recording_id = insert_recording(database_connection, "wrong-top-one")
        use_default_target = False
    elif mismatch == "top_one_json":
        decision_conflicts = [{"code": "DECISION_ONLY"}]
    else:
        top2_confidence = Decimal("0.500000")
        use_default_top2 = False

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match=r"decision top-one summary mismatch|decision top-two confidence mismatch",
        ),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            target_recording_id=target_recording_id,
            use_default_target=use_default_target,
            hard_conflicts=decision_conflicts,
            top2_confidence=top2_confidence,
            use_default_top2=use_default_top2,
        )
        force_deferred_constraints(database_connection)


def test_only_deferred_evidence_may_contain_null_candidate_scores(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    deferred_candidate = make_candidate(
        insert_recording(database_connection, "deferred-null-score"),
        1,
        releases,
        scores_are_null=True,
    )
    deferred = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        [deferred_candidate],
        state="DEFERRED_EVIDENCE",
        execution_mode="SHADOW",
    )
    force_deferred_constraints(database_connection)
    assert database_connection.execute(
        """
        SELECT raw_score, confidence FROM identity.match_candidate_evidence
        WHERE decision_id = %s
        """,
        (deferred.decision_id,),
    ).fetchone() == (None, None)

    with (
        pytest.raises(psycopg.errors.RaiseException, match="cannot have null scores"),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("VAULT_OBJECT"),
            releases,
            [
                make_candidate(
                    insert_recording(database_connection, "review-null-score"),
                    1,
                    releases,
                    scores_are_null=True,
                )
            ],
            state="REVIEW_REQUIRED",
            execution_mode="SHADOW",
        )
        force_deferred_constraints(database_connection)


def test_partial_candidate_set_is_rejected_at_real_commit(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidate = make_candidates(database_connection, releases, 1)
    append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidate,
        candidate_count=2,
    )
    with pytest.raises(psycopg.errors.RaiseException, match="candidate evidence seal mismatch"):
        database_connection.commit()
    database_connection.rollback()


def test_late_candidate_insert_breaks_an_already_sealed_decision(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    first = make_candidates(database_connection, releases, 1)
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        first,
    )
    force_deferred_constraints(database_connection)

    with (
        pytest.raises(psycopg.errors.RaiseException, match="candidate evidence seal mismatch"),
        database_connection.transaction(),
    ):
        insert_candidate_rows(
            database_connection,
            decision.decision_id,
            [make_candidate(insert_recording(database_connection, "late-candidate"), 2, releases)],
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize(
    ("table", "operation"),
    (
        ("match_decision", "UPDATE"),
        ("match_decision", "DELETE"),
        ("match_candidate_evidence", "UPDATE"),
        ("match_candidate_evidence", "DELETE"),
    ),
)
def test_decisions_and_candidate_evidence_are_append_only(
    database_connection: Connection[Any], table: str, operation: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = (
        []
        if table == "match_decision" and operation == "DELETE"
        else make_candidates(database_connection, releases, 1)
    )
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidates,
        state="NO_MATCH" if not candidates else "REVIEW_REQUIRED",
        execution_mode="APPLIED" if not candidates else "SHADOW",
    )
    force_deferred_constraints(database_connection)
    timestamp_column = "decided_at" if table == "match_decision" else "created_at"
    statement = (
        f"UPDATE identity.{table} "
        f"SET {timestamp_column} = {timestamp_column} WHERE decision_id = %s"
        if operation == "UPDATE"
        else f"DELETE FROM identity.{table} WHERE decision_id = %s"
    )
    message = (
        "match decisions are append-only"
        if table == "match_decision"
        else "candidate evidence is append-only"
    )
    with (
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match=message),
        database_connection.transaction(),
    ):
        database_connection.execute(statement, (decision.decision_id,))
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("candidate_count", (0, 1))
def test_auto_match_requires_a_sealed_top_two_candidate_pair(
    database_connection: Connection[Any], candidate_count: int
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    append_policy_event(database_connection, releases, world.admin_user_id)
    candidates = make_auto_candidates(database_connection, releases)[:candidate_count]
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            state="AUTO_MATCH",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)
