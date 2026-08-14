"""Append-only match-policy lifecycle and F-016/T4 gate tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import Connection

from .identity_factory import (
    append_evaluation,
    append_policy_event,
    force_deferred_constraints,
    insert_release_set,
    insert_world,
    make_auto_candidates,
    make_candidate,
    make_candidates,
)


def test_initial_policy_history_is_empty(database_connection: Connection[Any]) -> None:
    assert database_connection.execute(
        "SELECT count(*) FROM identity.match_policy_activation"
    ).fetchone() == (0,)


@pytest.mark.parametrize("missing", ("calibrator", "benchmark"))
def test_activation_requires_calibrator_and_benchmark(
    database_connection: Connection[Any], missing: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(
        database_connection,
        include_calibrator=missing != "calibrator",
        benchmark=missing != "benchmark",
    )
    with pytest.raises(psycopg.errors.RaiseException, match="requires calibrator and benchmark"):
        append_policy_event(database_connection, releases, world.admin_user_id)


@pytest.mark.parametrize("actor_case", ("user", "disabled_admin"))
def test_activation_requires_active_owner_or_admin(
    database_connection: Connection[Any], actor_case: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    actor = world.other_user_id if actor_case == "user" else world.admin_user_id
    if actor_case == "disabled_admin":
        database_connection.execute(
            "UPDATE account.user_account SET status = 'DISABLED' WHERE user_id = %s", (actor,)
        )
    with pytest.raises(psycopg.errors.RaiseException, match="active owner or admin"):
        append_policy_event(database_connection, releases, actor)


def test_activate_deactivate_rollback_lifecycle(database_connection: Connection[Any]) -> None:
    world = insert_world(database_connection)
    first_release = insert_release_set(database_connection)
    second_release = insert_release_set(database_connection)
    first = append_policy_event(database_connection, first_release, world.admin_user_id)
    closed = append_policy_event(
        database_connection,
        first_release,
        world.admin_user_id,
        sequence_no=2,
        action="DEACTIVATE",
        supersedes_activation_id=first,
    )
    second = append_policy_event(
        database_connection,
        second_release,
        world.admin_user_id,
        sequence_no=3,
        action="ACTIVATE",
        supersedes_activation_id=closed,
    )
    rollback = append_policy_event(
        database_connection,
        first_release,
        world.admin_user_id,
        sequence_no=4,
        action="ROLLBACK",
        supersedes_activation_id=second,
    )
    assert database_connection.execute(
        """
        SELECT sequence_no, action, threshold_set_version
        FROM identity.match_policy_activation
        WHERE evidence_mode = 'METADATA_ONLY' AND evidence_tier = 'T0'
        ORDER BY sequence_no
        """
    ).fetchall() == [
        (1, "ACTIVATE", first_release.threshold_set_version),
        (2, "DEACTIVATE", first_release.threshold_set_version),
        (3, "ACTIVATE", second_release.threshold_set_version),
        (4, "ROLLBACK", first_release.threshold_set_version),
    ]
    assert rollback is not None


@pytest.mark.parametrize("invalid", ("gap", "branch", "wrong_deactivate", "never_active_rollback"))
def test_invalid_policy_lineage_is_rejected(
    database_connection: Connection[Any], invalid: str
) -> None:
    world = insert_world(database_connection)
    active_release = insert_release_set(database_connection)
    other_release = insert_release_set(database_connection)
    first = append_policy_event(database_connection, active_release, world.admin_user_id)
    sequence = 3 if invalid == "gap" else 2
    action = {
        "gap": "ACTIVATE",
        "branch": "ACTIVATE",
        "wrong_deactivate": "DEACTIVATE",
        "never_active_rollback": "ROLLBACK",
    }[invalid]
    releases = (
        other_release
        if invalid in {"wrong_deactivate", "never_active_rollback"}
        else active_release
    )
    predecessor = first
    if invalid == "branch":
        append_policy_event(
            database_connection,
            active_release,
            world.admin_user_id,
            sequence_no=2,
            action="DEACTIVATE",
            supersedes_activation_id=first,
        )
        sequence = 2
    with pytest.raises(psycopg.Error):
        append_policy_event(
            database_connection,
            releases,
            world.admin_user_id,
            sequence_no=sequence,
            action=action,
            supersedes_activation_id=predecessor,
        )


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_policy_registry_and_events_are_append_only(
    database_connection: Connection[Any], operation: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    activation = append_policy_event(database_connection, releases, world.admin_user_id)
    statements = (
        (
            "UPDATE identity.matcher_release SET created_at = created_at "
            "WHERE matcher_version = %s",
            releases.matcher_version,
        ),
        (
            "UPDATE identity.calibrator_release SET created_at = created_at "
            "WHERE calibrator_version = %s",
            releases.calibrator_version,
        ),
        (
            "UPDATE identity.threshold_set SET created_at = created_at "
            "WHERE threshold_set_version = %s",
            releases.threshold_set_version,
        ),
        (
            (
                "UPDATE identity.match_policy_activation SET created_at = created_at "
                "WHERE activation_id = %s"
            ),
            activation,
        ),
    )
    for update_statement, key in statements:
        statement = (
            update_statement
            if operation == "UPDATE"
            else update_statement.replace("UPDATE ", "DELETE FROM ", 1).split(" SET ", 1)[0]
            + " WHERE "
            + update_statement.split(" WHERE ", 1)[1]
        )
        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
            database_connection.transaction(),
        ):
            database_connection.execute(statement, (key,))


@pytest.mark.parametrize(
    ("failure", "auto_threshold", "margin_threshold"),
    (
        ("inactive", Decimal("0.800000"), Decimal("0.100000")),
        ("confidence", Decimal("0.990000"), Decimal("0.100000")),
        ("margin", Decimal("0.800000"), Decimal("0.300000")),
    ),
)
def test_auto_match_requires_current_policy_and_thresholds(
    database_connection: Connection[Any],
    failure: str,
    auto_threshold: Decimal,
    margin_threshold: Decimal,
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(
        database_connection,
        auto_threshold=auto_threshold,
        margin_threshold=margin_threshold,
    )
    if failure != "inactive":
        append_policy_event(database_connection, releases, world.admin_user_id)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="policy gate failed"),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            make_auto_candidates(database_connection, releases),
            state="AUTO_MATCH",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


def test_deactivated_policy_cannot_authorize_auto_match(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    active = append_policy_event(database_connection, releases, world.admin_user_id)
    append_policy_event(
        database_connection,
        releases,
        world.admin_user_id,
        sequence_no=2,
        action="DEACTIVATE",
        supersedes_activation_id=active,
    )
    with (
        pytest.raises(psycopg.errors.RaiseException, match="policy gate failed"),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            make_auto_candidates(database_connection, releases),
            state="AUTO_MATCH",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


def test_f016_keeps_deterministic_bytes_t4_shadow_only(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(
        database_connection,
        evidence_mode="DETERMINISTIC_BYTES",
        evidence_tier="T4",
        include_calibrator=False,
        include_threshold=False,
    )
    candidates = make_candidates(database_connection, releases, 1)
    shadow = append_evaluation(
        database_connection,
        world.query("VAULT_OBJECT"),
        releases,
        candidates,
        state="REVIEW_REQUIRED",
        execution_mode="SHADOW",
    )
    force_deferred_constraints(database_connection)
    assert shadow.execution_mode == "SHADOW"

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match="pre-P00-D004 deterministic-byte evaluation must remain shadow",
        ),
        database_connection.transaction(),
    ):
        append_evaluation(
            database_connection,
            world.query("AUDIO_VARIANT"),
            releases,
            candidates,
            state="REVIEW_REQUIRED",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("length", (31, 33))
def test_benchmark_hash_must_be_sha256_length(
    database_connection: Connection[Any], length: int
) -> None:
    releases = insert_release_set(database_connection)
    assert releases.threshold_set_version is not None
    with (
        pytest.raises(psycopg.errors.CheckViolation) as exc_info,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO identity.threshold_set (
                threshold_set_version, matcher_version, calibrator_version,
                evidence_mode, minimum_evidence_tier, auto_threshold,
                review_threshold, margin_threshold, benchmark_report_sha256,
                gate_metadata_schema_version
            ) VALUES (%s, %s, %s, 'METADATA_ONLY', 'T0', 0.8, 0.5, 0.1, %s, '1')
            """,
            (
                f"bad-hash-{length}",
                releases.matcher_version,
                releases.calibrator_version,
                b"x" * length,
            ),
        )
    assert exc_info.value.diag.constraint_name == "ck_threshold_set_benchmark_hash_len"


def test_release_registry_rejects_cross_version_calibrator_scope(
    database_connection: Connection[Any],
) -> None:
    first = insert_release_set(database_connection)
    second = insert_release_set(database_connection)
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO identity.threshold_set (
                threshold_set_version, matcher_version, calibrator_version,
                evidence_mode, minimum_evidence_tier, auto_threshold,
                review_threshold, margin_threshold, benchmark_report_sha256,
                gate_metadata_schema_version
            ) VALUES ('cross-version', %s, %s, 'METADATA_ONLY', 'T0',
                      0.8, 0.5, 0.1, %s, '1')
            """,
            (first.matcher_version, second.calibrator_version, b"b" * 32),
        )
    assert exc_info.value.diag.constraint_name == "fk_threshold_set_calibrator_scope"


def test_nullable_calibrator_threshold_cannot_activate_or_auto_match(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection, include_calibrator=False)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="requires calibrator and benchmark"),
        database_connection.transaction(),
    ):
        append_policy_event(database_connection, releases, world.admin_user_id)
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            make_auto_candidates(database_connection, releases),
            state="AUTO_MATCH",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("state", ("AUTO_MATCH", "NO_MATCH"))
def test_shadow_cannot_resolve_or_auto_match(
    database_connection: Connection[Any], state: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = (
        make_auto_candidates(database_connection, releases) if state == "AUTO_MATCH" else []
    )
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            state=state,
            execution_mode="SHADOW",
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("failure", ("hard_conflict", "insufficient_tier", "wrong_threshold"))
def test_auto_match_rejects_conflict_tier_or_release_mismatch(
    database_connection: Connection[Any], failure: str
) -> None:
    world = insert_world(database_connection)
    active = insert_release_set(
        database_connection,
        evidence_tier="T1" if failure == "insufficient_tier" else "T0",
    )
    append_policy_event(database_connection, active, world.admin_user_id)
    decision_release = active
    if failure == "insufficient_tier":
        # Same matcher/calibrator/mode, but a lower-tier threshold snapshot.
        assert active.calibrator_version is not None
        lower_version = f"{active.threshold_set_version}-lower"
        database_connection.execute(
            """
            INSERT INTO identity.threshold_set (
                threshold_set_version, matcher_version, calibrator_version,
                evidence_mode, minimum_evidence_tier, auto_threshold,
                review_threshold, margin_threshold, benchmark_report_sha256,
                gate_metadata_schema_version
            ) VALUES (%s, %s, %s, 'METADATA_ONLY', 'T0', 0.8, 0.5, 0.1, %s, '1')
            """,
            (lower_version, active.matcher_version, active.calibrator_version, b"l" * 32),
        )
        decision_release = type(active)(
            active.matcher_version,
            active.candidate_generation_version,
            active.normalization_version,
            active.feature_extractor_versions,
            active.calibrator_version,
            lower_version,
            active.evidence_mode,
            "T0",
        )
    elif failure == "wrong_threshold":
        decision_release = insert_release_set(database_connection)
    candidates = make_auto_candidates(database_connection, decision_release)
    if failure == "hard_conflict":
        candidates[0] = make_candidate(
            candidates[0].recording_id,
            1,
            decision_release,
            raw_score=Decimal("0.950000"),
            confidence=Decimal("0.950000"),
            hard_conflicts=[{"code": "HARD"}],
        )
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            decision_release,
            candidates,
            state="AUTO_MATCH",
            execution_mode="APPLIED",
        )
        force_deferred_constraints(database_connection)


@pytest.mark.parametrize("invalid", ("state", "score", "margin"))
def test_invalid_state_score_or_margin_is_rejected(
    database_connection: Connection[Any], invalid: str
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidates = make_candidates(database_connection, releases, 2)
    if invalid == "state":
        with pytest.raises(psycopg.errors.CheckViolation):
            append_evaluation(
                database_connection,
                world.query("EXTERNAL_REFERENCE"),
                releases,
                candidates,
                state="UNKNOWN",
            )
        return
    if invalid == "score":
        # The production canonical builder rejects this first.  Replace only
        # the relational score so this negative still proves the independent
        # database CHECK for raw/direct DML.
        candidates[0] = replace(
            candidates[0],
            raw_score=Decimal("1.1"),
        )
    with pytest.raises(psycopg.Error), database_connection.transaction():
        append_evaluation(
            database_connection,
            world.query("EXTERNAL_REFERENCE"),
            releases,
            candidates,
            top2_confidence=(Decimal("0.5") if invalid == "margin" else None),
            use_default_top2=invalid != "margin",
        )
        force_deferred_constraints(database_connection)
