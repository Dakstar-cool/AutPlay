"""Identity decision lineage, manual review, and owner projection tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import Connection

from .identity_factory import (
    StoredDecision,
    append_evaluation,
    append_review,
    force_deferred_constraints,
    insert_recording,
    insert_release_set,
    insert_world,
    make_candidates,
    new_id,
)


def _review_predecessor(
    connection: Connection[Any], *, query_type: str = "EXTERNAL_REFERENCE"
) -> tuple[Any, StoredDecision]:
    world = insert_world(connection)
    releases = insert_release_set(connection)
    predecessor = append_evaluation(
        connection,
        world.query(query_type),
        releases,
        make_candidates(connection, releases, 2),
        state="REVIEW_REQUIRED",
        execution_mode="SHADOW",
    )
    force_deferred_constraints(connection)
    return world, predecessor


@pytest.mark.parametrize("action", ("ACCEPT", "REJECT"))
def test_accept_and_reject_reference_exact_predecessor_candidate(
    database_connection: Connection[Any], action: str
) -> None:
    world, predecessor = _review_predecessor(database_connection)
    review = append_review(
        database_connection,
        predecessor,
        action=action,
        actor_user_id=world.owner_user_id,
        selected_rank=2,
    )
    force_deferred_constraints(database_connection)

    row = database_connection.execute(
        """
        SELECT child.review_action, child.reviewed_candidate_evidence_id,
               child.candidate_recording_id, child.supersedes_decision_id,
               evidence.decision_id, evidence.recording_id
        FROM identity.match_decision child
        JOIN identity.match_candidate_evidence evidence
          ON evidence.match_candidate_evidence_id = child.reviewed_candidate_evidence_id
        WHERE child.decision_id = %s
        """,
        (review.decision_id,),
    ).fetchone()
    assert row is not None
    assert row == (
        action,
        row[1],
        predecessor.candidates[1].recording_id,
        predecessor.decision_id,
        predecessor.decision_id,
        predecessor.candidates[1].recording_id,
    )


@pytest.mark.parametrize(
    ("predecessor_state", "action"),
    (
        ("REVIEW_REQUIRED", "KEEP_UNRESOLVED"),
        ("NO_MATCH", "KEEP_UNRESOLVED"),
        ("DEFERRED_EVIDENCE", "KEEP_UNRESOLVED"),
        ("REVIEW_REQUIRED", "CREATE_RECORDING"),
        ("NO_MATCH", "CREATE_RECORDING"),
        ("DEFERRED_EVIDENCE", "CREATE_RECORDING"),
    ),
)
def test_keep_and_create_actions_follow_legal_predecessors(
    database_connection: Connection[Any], predecessor_state: str, action: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = (
        make_candidates(database_connection, releases, 1)
        if predecessor_state == "REVIEW_REQUIRED"
        else []
    )
    predecessor = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        candidates,
        state=predecessor_state,
        execution_mode="SHADOW" if predecessor_state != "NO_MATCH" else "APPLIED",
    )
    force_deferred_constraints(database_connection)
    new_recording = (
        insert_recording(database_connection, "manual-created-recording")
        if action == "CREATE_RECORDING"
        else None
    )
    review = append_review(
        database_connection,
        predecessor,
        action=action,
        actor_user_id=world.owner_user_id,
        created_recording_id=new_recording,
    )
    force_deferred_constraints(database_connection)

    assert database_connection.execute(
        """
        SELECT review_action, reviewed_candidate_evidence_id,
               candidate_recording_id, decision_state
        FROM identity.match_decision WHERE decision_id = %s
        """,
        (review.decision_id,),
    ).fetchone() == (action, None, new_recording, predecessor_state)


def test_integrity_conflict_permits_only_keep_unresolved(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    predecessor = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        [],
        state="INTEGRITY_CONFLICT",
        hard_conflicts=[{"code": "CONFLICT"}],
    )
    force_deferred_constraints(database_connection)
    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match=r"invalid create-recording review action|integrity conflict permits only",
        ),
        database_connection.transaction(),
    ):
        append_review(
            database_connection,
            predecessor,
            action="CREATE_RECORDING",
            actor_user_id=world.owner_user_id,
            created_recording_id=insert_recording(database_connection, "forbidden-create"),
        )
        force_deferred_constraints(database_connection)

    append_review(
        database_connection,
        predecessor,
        action="KEEP_UNRESOLVED",
        actor_user_id=world.owner_user_id,
    )
    force_deferred_constraints(database_connection)


@pytest.mark.parametrize("mutation", ("missing_row", "changed_row"))
def test_manual_review_must_copy_complete_candidate_snapshot(
    database_connection: Connection[Any], mutation: str
) -> None:
    world, predecessor = _review_predecessor(database_connection)
    replacement = (
        insert_recording(database_connection, "changed-review-evidence")
        if mutation == "changed_row"
        else None
    )
    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match=r"candidate evidence seal mismatch|must copy the predecessor candidate snapshot",
        ),
        database_connection.transaction(),
    ):
        review = append_review(
            database_connection,
            predecessor,
            action="ACCEPT",
            actor_user_id=world.owner_user_id,
            replacement_rank_one_recording_id=replacement,
        )
        if mutation == "missing_row":
            database_connection.execute(
                """
                DELETE FROM identity.match_candidate_evidence
                WHERE decision_id = %s AND rank = 2
                """,
                (review.decision_id,),
            )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("actor_case", ("wrong_user", "inactive_admin", "system"))
def test_manual_review_rejects_invalid_actor(
    database_connection: Connection[Any], actor_case: str
) -> None:
    world, predecessor = _review_predecessor(database_connection, query_type="USER_TRACK_REF")
    actor = world.other_user_id if actor_case == "wrong_user" else world.admin_user_id
    actor_type = (
        "SYSTEM"
        if actor_case == "system"
        else ("ADMIN" if actor_case == "inactive_admin" else "USER")
    )
    if actor_case == "inactive_admin":
        database_connection.execute(
            "UPDATE account.user_account SET status = 'DISABLED' WHERE user_id = %s",
            (actor,),
        )
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_review(
            database_connection,
            predecessor,
            action="ACCEPT",
            actor_user_id=actor,
            actor_type=actor_type,
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize(
    ("query_type", "action", "expected_status"),
    (
        ("USER_TRACK_REF", "ACCEPT", "RESOLVED"),
        ("USER_TRACK_REF", "REJECT", "CANDIDATES"),
        ("USER_TRACK_REF", "KEEP_UNRESOLVED", "UNRESOLVED"),
        ("IMPORT_ENTRY", "ACCEPT", "MANUAL_MATCH"),
        ("IMPORT_ENTRY", "REJECT", "REVIEW_REQUIRED"),
        ("IMPORT_ENTRY", "KEEP_UNRESOLVED", "MANUAL_UNRESOLVED"),
    ),
)
def test_review_and_owner_projection_commit_atomically(
    database_connection: Connection[Any], query_type: str, action: str, expected_status: str
) -> None:
    world, predecessor = _review_predecessor(database_connection, query_type=query_type)
    review = append_review(
        database_connection,
        predecessor,
        action=action,
        actor_user_id=world.owner_user_id,
        project=True,
    )
    force_deferred_constraints(database_connection)

    if query_type == "USER_TRACK_REF":
        row = database_connection.execute(
            """
            SELECT resolution_status, recording_id, current_match_decision_id
            FROM library.user_track_ref WHERE user_track_ref_id = %s
            """,
            (predecessor.query.typed_id,),
        ).fetchone()
    else:
        row = database_connection.execute(
            """
            SELECT match_status, selected_recording_id, current_match_decision_id
            FROM importing.import_entry WHERE import_entry_id = %s
            """,
            (predecessor.query.typed_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == expected_status
    assert row[1] == (predecessor.candidates[0].recording_id if action == "ACCEPT" else None)
    assert row[2] == review.decision_id


@pytest.mark.parametrize("query_type", ("USER_TRACK_REF", "IMPORT_ENTRY"))
def test_create_recording_and_projection_commit_atomically(
    database_connection: Connection[Any], query_type: str
) -> None:
    world, predecessor = _review_predecessor(database_connection, query_type=query_type)
    created_recording = insert_recording(database_connection, "created-in-review-transaction")
    review = append_review(
        database_connection,
        predecessor,
        action="CREATE_RECORDING",
        actor_user_id=world.owner_user_id,
        created_recording_id=created_recording,
        project=True,
    )
    force_deferred_constraints(database_connection)
    target_table = (
        "library.user_track_ref" if query_type == "USER_TRACK_REF" else "importing.import_entry"
    )
    id_column = "user_track_ref_id" if query_type == "USER_TRACK_REF" else "import_entry_id"
    recording_column = "recording_id" if query_type == "USER_TRACK_REF" else "selected_recording_id"
    assert database_connection.execute(
        f"""
        SELECT {recording_column}, current_match_decision_id
        FROM {target_table} WHERE {id_column} = %s
        """,
        (predecessor.query.typed_id,),
    ).fetchone() == (created_recording, review.decision_id)


@pytest.mark.parametrize("query_type", ("USER_TRACK_REF", "IMPORT_ENTRY"))
def test_applied_owner_decision_without_projection_rolls_back(
    database_connection: Connection[Any], query_type: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="must be projected atomically"),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query(query_type),
            releases,
            make_candidates(database_connection, releases, 1),
            state="REVIEW_REQUIRED",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


def test_projection_pointer_cannot_be_cleared_or_point_to_non_leaf(
    database_connection: Connection[Any],
) -> None:
    world, predecessor = _review_predecessor(database_connection, query_type="USER_TRACK_REF")
    review = append_review(
        database_connection,
        predecessor,
        action="ACCEPT",
        actor_user_id=world.owner_user_id,
        project=True,
    )
    force_deferred_constraints(database_connection)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="pointer cannot be cleared"),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            UPDATE library.user_track_ref
            SET current_match_decision_id = NULL, recording_id = NULL,
                resolution_status = 'UNRESOLVED', resolution_confidence = NULL,
                resolved_at = NULL
            WHERE user_track_ref_id = %s
            """,
            (predecessor.query.typed_id,),
        )
        force_deferred_constraints(database_connection)

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match=r"current decision projection mismatch|must be projected atomically",
        ),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            predecessor.query,
            predecessor.releases,
            list(predecessor.candidates),
            state="REVIEW_REQUIRED",
            execution_mode="APPLIED",
            supersedes_decision_id=review.decision_id,
            decided_at=review.decided_at + timedelta(microseconds=1),
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("query_type", ("USER_TRACK_REF", "IMPORT_ENTRY"))
@pytest.mark.parametrize("invalid", ("wrong_type", "wrong_owner", "shadow", "target"))
def test_projection_rejects_wrong_type_owner_shadow_or_target(
    database_connection: Connection[Any],
    query_type: str,
    invalid: str,
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    query = world.query(query_type)
    candidates = make_candidates(database_connection, releases, 1)
    target_query = query
    target_recording_id: UUID | None = candidates[0].recording_id
    confidence = candidates[0].confidence

    if invalid == "wrong_type":
        decision = append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            [],
            state="NO_MATCH",
            execution_mode="APPLIED",
        )
        target_recording_id = None
        confidence = None
        status = "NOT_FOUND" if query_type == "USER_TRACK_REF" else "NO_MATCH"
    elif invalid == "shadow":
        decision = append_evaluation(
            database_connection,
            query,
            releases,
            candidates,
            state="REVIEW_REQUIRED",
            execution_mode="SHADOW",
        )
        target_recording_id = None
        status = "CANDIDATES" if query_type == "USER_TRACK_REF" else "REVIEW_REQUIRED"
    else:
        predecessor = append_evaluation(
            database_connection,
            query,
            releases,
            candidates,
            state="REVIEW_REQUIRED",
            execution_mode="SHADOW",
        )
        decision = append_review(
            database_connection,
            predecessor,
            action="ACCEPT",
            actor_user_id=world.owner_user_id,
            project=True,
        )
        status = "RESOLVED" if query_type == "USER_TRACK_REF" else "MANUAL_MATCH"
        if invalid == "wrong_owner":
            target_query = insert_world(database_connection).query(query_type)
        else:
            target_recording_id = insert_recording(
                database_connection,
                f"divergent-{query_type.lower()}",
            )
    force_deferred_constraints(database_connection)
    decided_at = decision.decided_at

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match=r"projection mismatch|cannot point to shadow",
        ),
        database_connection.transaction(),
    ):
        if query_type == "USER_TRACK_REF":
            database_connection.execute(
                """
                UPDATE library.user_track_ref
                SET resolution_status = %s, recording_id = %s,
                    resolution_confidence = %s, current_match_decision_id = %s,
                    resolved_at = CASE WHEN %s = 'RESOLVED' THEN %s ELSE NULL END
                WHERE user_track_ref_id = %s
                """,
                (
                    status,
                    target_recording_id,
                    confidence,
                    decision.decision_id,
                    status,
                    decided_at,
                    target_query.typed_id,
                ),
            )
        else:
            database_connection.execute(
                """
                UPDATE importing.import_entry
                SET match_status = %s, selected_recording_id = %s,
                    current_match_decision_id = %s
                WHERE import_entry_id = %s
                """,
                (
                    status,
                    target_recording_id,
                    decision.decision_id,
                    target_query.typed_id,
                ),
            )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("invalid", ("self", "cycle", "cross_query", "earlier", "branch"))
def test_invalid_decision_lineage_is_rejected(
    database_connection: Connection[Any], invalid: str
) -> None:
    world, predecessor = _review_predecessor(database_connection)
    releases = predecessor.releases
    if invalid == "branch":
        append_evaluation(
            database_connection,
            predecessor.query,
            releases,
            list(predecessor.candidates),
            supersedes_decision_id=predecessor.decision_id,
            decided_at=predecessor.decided_at + timedelta(seconds=1),
        )
        force_deferred_constraints(database_connection)
    proposed_id = new_id() if invalid in {"self", "cycle"} else None
    parent_id = proposed_id if invalid == "self" else predecessor.decision_id
    query = world.query("VAULT_OBJECT") if invalid == "cross_query" else predecessor.query
    decision_time = (
        predecessor.decided_at - timedelta(seconds=1)
        if invalid == "earlier"
        else datetime.now(UTC) + timedelta(seconds=10)
    )
    with pytest.raises(psycopg.Error), database_connection.transaction():
        created = append_evaluation(
            database_connection,
            query,
            releases,
            list(predecessor.candidates),
            supersedes_decision_id=parent_id,
            decided_at=decision_time,
            decision_id=proposed_id,
        )
        if invalid == "cycle":
            database_connection.execute(
                """
                UPDATE identity.match_decision SET supersedes_decision_id = %s,
                    supersession_reason = 'P02 cycle fixture',
                    decided_at = decided_at + interval '1 second'
                WHERE decision_id = %s
                """,
                (created.decision_id, predecessor.decision_id),
            )
        force_deferred_constraints(database_connection)


def test_rescore_preserves_old_explanation_and_appends_new_leaf(
    database_connection: Connection[Any],
) -> None:
    _, predecessor = _review_predecessor(database_connection)
    old_snapshot = database_connection.execute(
        """
        SELECT query_snapshot, query_snapshot_sha256, candidate_evidence_sha256,
               feature_scores, candidate_origins
        FROM identity.match_decision WHERE decision_id = %s
        """,
        (predecessor.decision_id,),
    ).fetchone()
    successor_candidates = make_candidates(database_connection, predecessor.releases, 1)
    successor = append_evaluation(
        database_connection,
        predecessor.query,
        predecessor.releases,
        successor_candidates,
        supersedes_decision_id=predecessor.decision_id,
        decided_at=predecessor.decided_at + timedelta(seconds=1),
    )
    force_deferred_constraints(database_connection)
    assert (
        database_connection.execute(
            """
        SELECT query_snapshot, query_snapshot_sha256, candidate_evidence_sha256,
               feature_scores, candidate_origins
        FROM identity.match_decision WHERE decision_id = %s
        """,
            (predecessor.decision_id,),
        ).fetchone()
        == old_snapshot
    )
    assert successor.decision_id != predecessor.decision_id
    assert database_connection.execute(
        "SELECT supersedes_decision_id FROM identity.match_decision WHERE decision_id = %s",
        (successor.decision_id,),
    ).fetchone() == (predecessor.decision_id,)
