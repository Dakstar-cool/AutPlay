"""Bounded global cadence for automatic release checks.

The provider budget reserves half of Jamendo's published 35,000 monthly calls for
manual searches, retries, and other API use. One scheduled scan can read two pages.
This admission check complements the runtime provider limiter; it does not grant
permission to call the provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

DEFAULT_INTERVAL_HOURS = 24
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 168
_MONTH_HOURS = 30 * 24
_SCHEDULED_CALL_BUDGET = 17_500
_MAX_CALLS_PER_SCAN = 2
_CHANGE_SPREAD = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ScanSchedule:
    interval_hours: int
    revision: int
    changed_by: UUID | None
    changed_at: datetime | None
    scheduled_policy_count: int
    last_started_at: datetime | None
    next_eligible_at: datetime | None


def validate_interval(hours: int, *, scheduled_policy_count: int) -> None:
    if type(hours) is not int or not MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS:
        raise ValueError("scan interval must be a whole number of hours within [1, 168]")
    if scheduled_policy_count < 0:
        raise ValueError("scheduled policy count is invalid")
    estimated_calls = (
        scheduled_policy_count * _MAX_CALLS_PER_SCAN * _MONTH_HOURS + hours - 1
    ) // hours
    if estimated_calls > _SCHEDULED_CALL_BUDGET:
        raise ValueError("scan interval exceeds reserved provider request capacity")


def next_automatic_check(
    *,
    policy_id: UUID,
    last_started_at: datetime | None,
    previous_eligible_at: datetime | None,
    now: datetime,
    interval_hours: int,
    interval_changed: bool = False,
) -> datetime:
    """Recompute from the last start, spreading overdue work after a change.

    A never-started policy keeps its already assigned initial anchor. New policies
    are scheduled from ``now`` by their caller. An interval change spreads
    overdue policies over one hour.
    """

    validate_interval(interval_hours, scheduled_policy_count=0)
    if now.tzinfo is None or (last_started_at and last_started_at.tzinfo is None):
        raise ValueError("schedule timestamps must be timezone aware")
    if previous_eligible_at is not None and previous_eligible_at.tzinfo is None:
        raise ValueError("schedule timestamps must be timezone aware")
    anchor = last_started_at or previous_eligible_at or now
    if last_started_at is None and previous_eligible_at is not None:
        # Keep the original initial anchor instead of extending it on each edit.
        due = anchor
    else:
        due = anchor + timedelta(hours=interval_hours)
    jitter_seconds = int.from_bytes(hashlib.sha256(policy_id.bytes).digest()[:4], "big")
    if interval_changed and due <= now:
        return now + timedelta(seconds=jitter_seconds % int(_CHANGE_SPREAD.total_seconds()))
    if last_started_at is None and previous_eligible_at is not None:
        return due
    return due


__all__ = (
    "DEFAULT_INTERVAL_HOURS",
    "MAX_INTERVAL_HOURS",
    "MIN_INTERVAL_HOURS",
    "ScanSchedule",
    "next_automatic_check",
    "validate_interval",
)
