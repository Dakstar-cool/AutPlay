"""Two-connection identity-lineage and policy serialization races."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import Connection

from .identity_factory import (
    append_evaluation,
    append_policy_event,
    force_deferred_constraints,
    insert_release_set,
    insert_world,
    make_auto_candidates,
    make_candidates,
)

if TYPE_CHECKING:
    from .conftest import DatabaseHarness


def _prepare_committed_predecessor(
    connection: Connection[Any],
) -> tuple[Any, Any]:
    world = insert_world(connection)
    releases = insert_release_set(connection)
    predecessor = append_evaluation(
        connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        make_candidates(connection, releases, 1),
    )
    force_deferred_constraints(connection)
    connection.commit()
    return world, predecessor


def test_two_connections_cannot_create_successor_branch(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with database_harness.connect(database_name) as setup:
        _, predecessor = _prepare_committed_predecessor(setup)

    first_inserted = threading.Event()
    allow_first_commit = threading.Event()
    second_started = threading.Event()

    def append_successor(first: bool) -> str:
        with database_harness.connect(database_name) as connection:
            candidates = make_candidates(connection, predecessor.releases, 1)
            try:
                if not first:
                    second_started.set()
                append_evaluation(
                    connection,
                    predecessor.query,
                    predecessor.releases,
                    candidates,
                    supersedes_decision_id=predecessor.decision_id,
                    decided_at=predecessor.decided_at + timedelta(seconds=1 if first else 2),
                )
                if first:
                    first_inserted.set()
                    if not allow_first_commit.wait(timeout=10):
                        raise AssertionError("first successor commit was never released")
                force_deferred_constraints(connection)
                connection.commit()
                return "committed"
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                return "unique_violation"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(append_successor, True)
        assert first_inserted.wait(timeout=10)
        second = executor.submit(append_successor, False)
        assert second_started.wait(timeout=10)
        allow_first_commit.set()
        outcomes = {first.result(timeout=15), second.result(timeout=15)}
    assert outcomes == {"committed", "unique_violation"}
    with database_harness.connect(database_name) as observer:
        assert observer.execute(
            "SELECT count(*) FROM identity.match_decision WHERE supersedes_decision_id = %s",
            (predecessor.decision_id,),
        ).fetchone() == (1,)


def test_two_connections_cannot_append_same_policy_sequence(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with database_harness.connect(database_name) as setup:
        world = insert_world(setup)
        releases = insert_release_set(setup)
        active = append_policy_event(setup, releases, world.admin_user_id)
        setup.commit()
    first_inserted = threading.Event()
    allow_commit = threading.Event()
    second_started = threading.Event()

    def deactivate(first: bool) -> str:
        with database_harness.connect(database_name) as connection:
            try:
                if not first:
                    second_started.set()
                append_policy_event(
                    connection,
                    releases,
                    world.admin_user_id,
                    sequence_no=2,
                    action="DEACTIVATE",
                    supersedes_activation_id=active,
                )
                if first:
                    first_inserted.set()
                    if not allow_commit.wait(timeout=10):
                        raise AssertionError("policy race commit was never released")
                connection.commit()
                return "committed"
            except psycopg.Error:
                connection.rollback()
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(deactivate, True)
        assert first_inserted.wait(timeout=10)
        second = executor.submit(deactivate, False)
        assert second_started.wait(timeout=10)
        allow_commit.set()
        outcomes = {first.result(timeout=15), second.result(timeout=15)}
    assert outcomes == {"committed", "rejected"}
    with database_harness.connect(database_name) as observer:
        assert observer.execute(
            """
            SELECT count(*) FROM identity.match_policy_activation
            WHERE evidence_mode = %s AND evidence_tier = %s AND sequence_no = 2
            """,
            (releases.evidence_mode, releases.evidence_tier),
        ).fetchone() == (1,)


def test_deactivation_serializes_before_competing_auto_match(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with database_harness.connect(database_name) as setup:
        world = insert_world(setup)
        releases = insert_release_set(setup)
        active = append_policy_event(setup, releases, world.admin_user_id)
        setup.commit()
    deactivation_inserted = threading.Event()
    allow_deactivation_commit = threading.Event()
    auto_started = threading.Event()

    def deactivate() -> str:
        with database_harness.connect(database_name) as connection:
            append_policy_event(
                connection,
                releases,
                world.admin_user_id,
                sequence_no=2,
                action="DEACTIVATE",
                supersedes_activation_id=active,
            )
            deactivation_inserted.set()
            if not allow_deactivation_commit.wait(timeout=10):
                raise AssertionError("deactivation commit was never released")
            connection.commit()
            return "deactivated"

    def auto_match() -> str:
        assert deactivation_inserted.wait(timeout=10)
        with database_harness.connect(database_name) as connection:
            try:
                auto_started.set()
                append_evaluation(
                    connection,
                    world.query("EXTERNAL_REFERENCE"),
                    releases,
                    make_auto_candidates(connection, releases),
                    state="AUTO_MATCH",
                    execution_mode="APPLIED",
                )
                force_deferred_constraints(connection)
                connection.commit()
                return "committed"
            except psycopg.errors.RaiseException:
                connection.rollback()
                return "policy_rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        deactivation = executor.submit(deactivate)
        assert deactivation_inserted.wait(timeout=10)
        automatic = executor.submit(auto_match)
        assert auto_started.wait(timeout=10)
        allow_deactivation_commit.set()
        assert deactivation.result(timeout=15) == "deactivated"
        assert automatic.result(timeout=15) == "policy_rejected"


def test_auto_match_serializes_before_competing_deactivation(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with database_harness.connect(database_name) as setup:
        world = insert_world(setup)
        releases = insert_release_set(setup)
        active = append_policy_event(setup, releases, world.admin_user_id)
        setup.commit()
    auto_holds_lock = threading.Event()
    allow_auto_commit = threading.Event()
    deactivation_started = threading.Event()

    def auto_match() -> str:
        with database_harness.connect(database_name) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{releases.evidence_mode}:{releases.evidence_tier}",),
            )
            decision = append_evaluation(
                connection,
                world.query("EXTERNAL_REFERENCE"),
                releases,
                make_auto_candidates(connection, releases),
                state="AUTO_MATCH",
                execution_mode="APPLIED",
            )
            force_deferred_constraints(connection)
            auto_holds_lock.set()
            if not allow_auto_commit.wait(timeout=10):
                raise AssertionError("auto-match commit was never released")
            connection.commit()
            return str(decision.decision_id)

    def deactivate() -> str:
        assert auto_holds_lock.wait(timeout=10)
        with database_harness.connect(database_name) as connection:
            deactivation_started.set()
            append_policy_event(
                connection,
                releases,
                world.admin_user_id,
                sequence_no=2,
                action="DEACTIVATE",
                supersedes_activation_id=active,
            )
            connection.commit()
            return "deactivated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        automatic = executor.submit(auto_match)
        assert auto_holds_lock.wait(timeout=10)
        deactivation = executor.submit(deactivate)
        assert deactivation_started.wait(timeout=10)
        allow_auto_commit.set()
        assert automatic.result(timeout=15)
        assert deactivation.result(timeout=15) == "deactivated"
    with database_harness.connect(database_name) as observer:
        assert observer.execute(
            "SELECT count(*) FROM identity.match_decision WHERE decision_state = 'AUTO_MATCH'"
        ).fetchone() == (1,)
