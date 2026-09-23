"""Revision-15 terminal/retry guards prevent stale work or serving truth reuse."""

import pytest

from autplay.domain.ml_lifecycle import (
    MlLifecycleError,
    MlLifecycleKind,
    lineage_failure_target,
    require_ml_transition,
)


def test_lineage_only_retry_wait_reopens_and_terminal_states_do_not() -> None:
    kind = MlLifecycleKind.LINEAGE_WORK
    require_ml_transition(kind, "PENDING", "CLAIMED")
    require_ml_transition(kind, "CLAIMED", "RETRY_WAIT")
    require_ml_transition(kind, "RETRY_WAIT", "PENDING")
    for terminal in (
        "SUCCEEDED",
        "TERMINAL_INELIGIBLE",
        "TERMINAL_FAILED",
        "RETRY_EXHAUSTED",
        "CANCELLED",
        "SUPERSEDED",
    ):
        with pytest.raises(MlLifecycleError):
            require_ml_transition(kind, terminal, "PENDING")
    with pytest.raises(MlLifecycleError):
        require_ml_transition(kind, "CLAIMED", "PENDING")


def test_discovery_success_does_not_close_cursor_or_reopen_consumed_target() -> None:
    require_ml_transition(MlLifecycleKind.SONA_TARGET_DISPATCH, "WAITING", "READY")
    require_ml_transition(MlLifecycleKind.SONA_TARGET_DISPATCH, "READY", "CONSUMED")
    with pytest.raises(MlLifecycleError):
        require_ml_transition(MlLifecycleKind.SONA_TARGET_DISPATCH, "CONSUMED", "READY")
    with pytest.raises(MlLifecycleError):
        require_ml_transition(MlLifecycleKind.SONA_CAPTURE_CURSOR, "ACTIVE", "CONSUMED")
    require_ml_transition(MlLifecycleKind.SONA_CAPTURE_CURSOR, "ACTIVE", "EXPIRED")


def test_serving_decision_is_final_after_commit_and_backfill_stop_is_ordered() -> None:
    require_ml_transition(MlLifecycleKind.SERVING_DECISION, "PREPARING", "COMMITTED_P11")
    require_ml_transition(MlLifecycleKind.SERVING_DECISION, "PREPARING", "COMMITTED_SONA")
    with pytest.raises(MlLifecycleError):
        require_ml_transition(MlLifecycleKind.SERVING_DECISION, "COMMITTED_SONA", "COMMITTED_P11")
    with pytest.raises(MlLifecycleError):
        require_ml_transition(MlLifecycleKind.FACE_BACKFILL, "RUNNING", "COMPLETED")
    require_ml_transition(MlLifecycleKind.FACE_BACKFILL, "RUNNING", "STOPPING")
    require_ml_transition(MlLifecycleKind.FACE_BACKFILL, "STOPPING", "COMPLETED")


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("SOURCE_UNAVAILABLE", "TERMINAL_INELIGIBLE"),
        ("CONSENT_WITHDRAWN", "TERMINAL_INELIGIBLE"),
        ("RETENTION_EXPIRED", "TERMINAL_INELIGIBLE"),
        ("CORRUPT_OUTPUT", "TERMINAL_FAILED"),
        ("INTEGRITY_VIOLATION", "TERMINAL_FAILED"),
        ("BATCH_ONE_OOM", "TERMINAL_FAILED"),
        ("GPU_BUSY", "RETRY_WAIT"),
        ("PROCESS_DIED", "RETRY_WAIT"),
        ("LEASE_EXPIRED", "RETRY_WAIT"),
    ],
)
def test_failure_classification(reason: str, expected: str) -> None:
    assert lineage_failure_target(reason) == expected
    if expected == "RETRY_WAIT":
        assert lineage_failure_target(reason, retry_budget_exhausted=True) == "RETRY_EXHAUSTED"


def test_unknown_failure_reason_cannot_be_silently_retried() -> None:
    with pytest.raises(MlLifecycleError):
        lineage_failure_target("UNKNOWN")
