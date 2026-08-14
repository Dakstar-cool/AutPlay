"""Framework-independent contracts for durable PostgreSQL jobs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

MAX_JOB_DOCUMENT_BYTES: Final = 256 * 1024
MAX_ERROR_DETAIL_BYTES: Final = 64 * 1024
MAX_JSON_DEPTH: Final = 32
MAX_JOB_TYPE_LENGTH: Final = 200
MAX_WORKER_ID_LENGTH: Final = 300
MAX_ERROR_CODE_LENGTH: Final = 200

_ERROR_CODE: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SENSITIVE_KEY: Final = re.compile(
    r"(?:^|_)(?:access|auth|refresh)?_?token(?:$|_)|"
    r"(?:^|_)(?:api_key|authorization|cookie|password|passwd|secret|credential)(?:$|_)",
    re.IGNORECASE,
)


class JobState(StrEnum):
    """Closed v1 states stored by ``jobs.job``."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """Return whether no later repository transition is allowed."""

        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class JobAttemptOutcome(StrEnum):
    """Closed v1 outcomes stored by ``jobs.job_attempt``."""

    SUCCESS = "SUCCESS"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    TERMINAL_ERROR = "TERMINAL_ERROR"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    CANCELLED = "CANCELLED"


class LeaseTransition(StrEnum):
    """Result of a mutation guarded by an active lease fence."""

    APPLIED = "APPLIED"
    CANCELLED = "CANCELLED"
    LOST_LEASE = "LOST_LEASE"


class CancelRequestResult(StrEnum):
    """Owner-safe result of requesting job cancellation."""

    REQUESTED = "REQUESTED"
    CANCELLED = "CANCELLED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class JobKey:
    """A versioned handler key."""

    job_type: str
    schema_version: int

    def __post_init__(self) -> None:
        _require_bounded_text(self.job_type, "job_type", MAX_JOB_TYPE_LENGTH)
        if self.schema_version < 1:
            raise ValueError("schema_version must be at least one")


@dataclass(frozen=True, slots=True)
class LeaseFence:
    """Fencing identity for one claim epoch.

    ``attempt_no`` prevents a stale worker from mutating a later lease even if a
    process-level worker identifier is accidentally reused.
    """

    job_id: UUID
    worker_id: str
    attempt_no: int

    def __post_init__(self) -> None:
        _require_bounded_text(self.worker_id, "worker_id", MAX_WORKER_ID_LENGTH)
        if self.attempt_no < 1:
            raise ValueError("attempt_no must be at least one")


@dataclass(frozen=True, slots=True)
class JobLease:
    """One active, fenced delivery of a durable job."""

    fence: LeaseFence
    key: JobKey
    user_id: UUID | None
    priority: int
    payload: dict[str, JsonValue]
    checkpoint: dict[str, JsonValue] | None
    lease_deadline: datetime
    cancel_requested_at: datetime | None

    def __post_init__(self) -> None:
        if not 0 <= self.priority <= 4:
            raise ValueError("priority must be between zero and four")
        _require_aware(self.lease_deadline, "lease_deadline")
        if self.cancel_requested_at is not None:
            _require_aware(self.cancel_requested_at, "cancel_requested_at")


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Current lease state returned after a successful renewal."""

    lease_deadline: datetime
    cancel_requested_at: datetime | None

    def __post_init__(self) -> None:
        _require_aware(self.lease_deadline, "lease_deadline")
        if self.cancel_requested_at is not None:
            _require_aware(self.cancel_requested_at, "cancel_requested_at")

    @property
    def cancel_requested(self) -> bool:
        """Return whether the worker must stop at its next safe point."""

        return self.cancel_requested_at is not None


@dataclass(frozen=True, slots=True)
class CheckpointSaved:
    """Cancellation state observed atomically with a durable checkpoint."""

    cancel_requested_at: datetime | None

    def __post_init__(self) -> None:
        if self.cancel_requested_at is not None:
            _require_aware(self.cancel_requested_at, "cancel_requested_at")

    @property
    def cancel_requested(self) -> bool:
        """Return whether cancellation was pending when the checkpoint was saved."""

        return self.cancel_requested_at is not None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy shared by normal failure and lease recovery."""

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=2)
    max_delay: timedelta = timedelta(minutes=5)
    jitter_ratio: float = 0.25

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be between one and one hundred")
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must not be shorter than base_delay")
        if self.max_delay > timedelta(days=1):
            raise ValueError("max_delay must not exceed one day")
        if not math.isfinite(self.jitter_ratio) or not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")

    def delay_for(self, job_id: UUID, attempt_no: int) -> timedelta:
        """Return deterministic, downward-jittered exponential backoff."""

        if attempt_no < 1:
            raise ValueError("attempt_no must be at least one")
        exponent = min(attempt_no - 1, 62)
        cap_seconds = min(
            self.max_delay.total_seconds(),
            self.base_delay.total_seconds() * (2**exponent),
        )
        digest = hashlib.sha256(job_id.bytes + attempt_no.to_bytes(8, "big")).digest()
        unit = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
        factor = (1 - self.jitter_ratio) + (self.jitter_ratio * unit)
        return timedelta(seconds=cap_seconds * factor)


@dataclass(frozen=True, slots=True)
class JobError:
    """Safe structured error persisted for an attempt or terminal job."""

    code: str
    detail: dict[str, JsonValue]

    def __post_init__(self) -> None:
        validate_error_code(self.code)
        normalized = validate_job_document(
            self.detail,
            field="error_detail",
            maximum_bytes=MAX_ERROR_DETAIL_BYTES,
        )
        object.__setattr__(self, "detail", normalized)


class JobExecutionError(RuntimeError):
    """Base class for deliberately classified handler failures."""

    def __init__(self, code: str, detail: Mapping[str, JsonValue] | None = None) -> None:
        self.error = JobError(code, dict(detail or {}))
        super().__init__(code)


class RetryableJobError(JobExecutionError):
    """A handler failure that may be retried within its bounded policy."""


class TerminalJobError(JobExecutionError):
    """A handler failure that must not be retried automatically."""


class JobCancellationRequested(RuntimeError):
    """Raised by a handler at a safe cancellation point."""

    def __init__(self) -> None:
        super().__init__("job.cancel_requested")


class JobDocumentError(ValueError):
    """A persisted job document is unsafe or exceeds its v1 bounds."""


class JobPersistenceInvariantError(RuntimeError):
    """Durable job rows do not satisfy repository-level invariants."""


def validate_error_code(code: str) -> None:
    """Validate one stable, non-sensitive machine error code."""

    if not 1 <= len(code) <= MAX_ERROR_CODE_LENGTH or _ERROR_CODE.fullmatch(code) is None:
        raise ValueError("error code must be a lowercase dotted machine code")


def validate_job_document(
    value: Mapping[str, JsonValue],
    *,
    field: str,
    maximum_bytes: int = MAX_JOB_DOCUMENT_BYTES,
) -> dict[str, JsonValue]:
    """Copy and size-check a secret-free JSON object for durable storage."""

    if maximum_bytes < 2:
        raise ValueError("maximum_bytes must allow an empty JSON object")
    normalized = _copy_json_object(value, field, depth=0)
    try:
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise JobDocumentError(f"{field} is not valid JSON") from error
    if len(encoded) > maximum_bytes:
        raise JobDocumentError(f"{field} exceeds {maximum_bytes} bytes")
    return normalized


def _copy_json_object(
    value: Mapping[str, JsonValue], path: str, *, depth: int
) -> dict[str, JsonValue]:
    if depth > MAX_JSON_DEPTH:
        raise JobDocumentError(f"{path} exceeds the maximum nesting depth")
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise JobDocumentError(f"{path} contains a non-string key")
        if _is_sensitive_key(key):
            raise JobDocumentError(f"{path}.{key} is sensitive")
        result[key] = _copy_json_value(item, f"{path}.{key}", depth=depth + 1)
    return result


def _copy_json_value(value: JsonValue, path: str, *, depth: int) -> JsonValue:
    if depth > MAX_JSON_DEPTH:
        raise JobDocumentError(f"{path} exceeds the maximum nesting depth")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JobDocumentError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, list):
        return [
            _copy_json_value(item, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return _copy_json_object(value, path, depth=depth)
    raise JobDocumentError(f"{path} contains an unsupported JSON value")


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return _SENSITIVE_KEY.search(normalized) is not None


def _require_bounded_text(value: str, field: str, maximum: int) -> None:
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"{field} length must be between one and {maximum}")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


__all__ = (
    "MAX_ERROR_DETAIL_BYTES",
    "MAX_JOB_DOCUMENT_BYTES",
    "CancelRequestResult",
    "CheckpointSaved",
    "Heartbeat",
    "JobAttemptOutcome",
    "JobCancellationRequested",
    "JobDocumentError",
    "JobError",
    "JobExecutionError",
    "JobKey",
    "JobLease",
    "JobPersistenceInvariantError",
    "JobState",
    "JsonValue",
    "LeaseFence",
    "LeaseTransition",
    "RetryPolicy",
    "RetryableJobError",
    "TerminalJobError",
    "validate_error_code",
    "validate_job_document",
)
