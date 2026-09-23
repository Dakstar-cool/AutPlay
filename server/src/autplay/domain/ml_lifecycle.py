"""Frozen revision-15 transition authority for work, discovery and serving truth."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class MlLifecycleKind(StrEnum):
    LINEAGE_WORK = "LINEAGE_WORK"
    SONA_CAPTURE_CURSOR = "SONA_CAPTURE_CURSOR"
    SONA_TARGET_DISPATCH = "SONA_TARGET_DISPATCH"
    FACE_DOWNLOAD = "FACE_DOWNLOAD"
    FACE_BACKFILL = "FACE_BACKFILL"
    SERVING_DECISION = "SERVING_DECISION"


_TRANSITIONS: Final[dict[MlLifecycleKind, dict[str, frozenset[str]]]] = {
    MlLifecycleKind.LINEAGE_WORK: {
        "PENDING": frozenset({"CLAIMED", "CANCELLED", "SUPERSEDED"}),
        "CLAIMED": frozenset(
            {
                "SUCCEEDED",
                "RETRY_WAIT",
                "TERMINAL_INELIGIBLE",
                "TERMINAL_FAILED",
                "RETRY_EXHAUSTED",
                "CANCELLED",
                "SUPERSEDED",
            }
        ),
        "RETRY_WAIT": frozenset({"PENDING", "CANCELLED", "SUPERSEDED"}),
    },
    MlLifecycleKind.SONA_CAPTURE_CURSOR: {
        "ACTIVE": frozenset({"EXPIRED", "CANCELLED"}),
    },
    MlLifecycleKind.SONA_TARGET_DISPATCH: {
        "WAITING": frozenset({"READY", "TERMINAL_INELIGIBLE", "EXPIRED", "CANCELLED"}),
        "READY": frozenset({"CONSUMED", "TERMINAL_INELIGIBLE", "EXPIRED", "CANCELLED"}),
    },
    MlLifecycleKind.FACE_DOWNLOAD: {
        "PENDING": frozenset({"RUNNING", "CANCELLED", "SUPERSEDED"}),
        "RUNNING": frozenset({"READY", "RETRY_WAIT", "TERMINAL_FAILED", "CANCELLED", "SUPERSEDED"}),
        "RETRY_WAIT": frozenset({"PENDING", "CANCELLED", "SUPERSEDED"}),
    },
    MlLifecycleKind.FACE_BACKFILL: {
        "PREPARED": frozenset({"RUNNING", "CANCELLED"}),
        "RUNNING": frozenset({"PAUSED", "STOPPING"}),
        "PAUSED": frozenset({"RUNNING", "STOPPING"}),
        "STOPPING": frozenset({"COMPLETED", "CANCELLED", "FAILED"}),
    },
    MlLifecycleKind.SERVING_DECISION: {
        "PREPARING": frozenset({"COMMITTED_P11", "COMMITTED_SONA"}),
    },
}

_STABLE_INELIGIBLE: Final = frozenset(
    {"SOURCE_UNAVAILABLE", "CONSENT_WITHDRAWN", "RETENTION_EXPIRED", "LINEAGE_REVOKED"}
)
_TERMINAL_FAILED: Final = frozenset(
    {
        "CORRUPT_OUTPUT",
        "INTEGRITY_VIOLATION",
        "CONTRACT_VIOLATION",
        "UNSUPPORTED_RUNTIME",
        "BATCH_ONE_OOM",
    }
)
_TRANSIENT: Final = frozenset(
    {"GPU_BUSY", "GPU_TIMEOUT", "PROCESS_DIED", "DATABASE_UNAVAILABLE", "LEASE_EXPIRED"}
)


class MlLifecycleError(ValueError):
    """A state change would change durable authority outside the frozen graph."""


def require_ml_transition(kind: MlLifecycleKind, current: str, target: str) -> None:
    if type(kind) is not MlLifecycleKind or target not in _TRANSITIONS[kind].get(current, ()):
        raise MlLifecycleError(f"invalid {kind} transition: {current} -> {target}")


def lineage_failure_target(reason: str, *, retry_budget_exhausted: bool = False) -> str:
    """Classify a claimed work failure without treating GPU loss as source loss."""

    if reason in _STABLE_INELIGIBLE:
        return "TERMINAL_INELIGIBLE"
    if reason in _TERMINAL_FAILED:
        return "TERMINAL_FAILED"
    if reason in _TRANSIENT:
        return "RETRY_EXHAUSTED" if retry_budget_exhausted else "RETRY_WAIT"
    raise MlLifecycleError("unknown lineage failure reason")
