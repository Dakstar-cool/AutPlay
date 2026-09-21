"""Bounded operator-reviewed evidence for simultaneous server resource capacity."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from autplay.domain.resource_admission import ResourceAdmissionError, positive_limit
from autplay.domain.resource_policy import GlobalResourceLimits

MAX_MEASUREMENT_BYTES = 65536
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_METRICS = frozenset(
    {
        "cpu_peak_percent",
        "disk_busy_peak_percent",
        "network_busy_peak_percent",
        "disk_read_mib_per_second",
        "disk_write_mib_per_second",
        "network_receive_mib_per_second",
        "network_send_mib_per_second",
        "database_p95_ms",
        "queue_wait_p95_ms",
        "queue_depth_max",
        "playback_mib_per_second",
        "transfer_mib_per_second",
        "successful_operations",
        "failed_operations",
        "timed_out_operations",
    }
)
_MINIMA = frozenset(
    {
        "duration_seconds",
        "sample_count",
        "playback_mib_per_second",
        "transfer_mib_per_second",
        "successful_operations",
    }
)
MEASUREMENT_PATHS = frozenset(
    {
        "PLAYBACK_CURRENT_NEXT",
        "RANGE_SEEK",
        "DOWNLOAD",
        "UPLOAD",
        "INTERNET_ACQUISITION",
        "A1_ACQUISITION",
    }
)
_FIELDS = frozenset(
    {
        "version",
        "measurement_id",
        "server_instance_id",
        "identity_epoch",
        "environment_sha256",
        "workload_sha256",
        "measured_at",
        "duration_seconds",
        "sample_count",
        "simultaneous_playbacks",
        "simultaneous_transfers",
        "metrics",
        "acceptance_maxima",
        "acceptance_minima",
        "workload_paths",
    }
)


def _invalid() -> ResourceAdmissionError:
    return ResourceAdmissionError("resource_measurement_invalid")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _integer(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _invalid()
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _invalid()
    return value


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise _invalid()
    try:
        result = UUID(value)
    except ValueError:
        raise _invalid() from None
    if str(result) != value:
        raise _invalid()
    return result


def _metrics(value: object) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, dict) or set(value) != _METRICS:
        raise _invalid()
    result = []
    for key in sorted(_METRICS):
        number = value[key]
        maximum = 100 if key.endswith("_percent") else 1_000_000_000
        if (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or not 0 <= number <= maximum
        ):
            raise _invalid()
        if (key == "queue_depth_max" or key.endswith("_operations")) and type(number) is not int:
            raise _invalid()
        result.append((key, float(number)))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ResourceMeasurement:
    measurement_id: UUID
    server_instance_id: UUID
    identity_epoch: int
    environment_sha256: str
    workload_sha256: str
    report_sha256: str
    measured_at: datetime
    duration_seconds: int
    sample_count: int
    simultaneous: GlobalResourceLimits
    metrics: tuple[tuple[str, float], ...]
    acceptance_maxima: tuple[tuple[str, float], ...]
    acceptance_minima: tuple[tuple[str, float], ...]
    workload_paths: tuple[str, ...]

    @classmethod
    def parse(cls, payload: bytes) -> ResourceMeasurement:
        if not 1 <= len(payload) <= MAX_MEASUREMENT_BYTES:
            raise _invalid()
        try:
            document = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(document, dict) or set(document) != _FIELDS:
                raise _invalid()
            if _integer(document["version"], 1) != 1:
                raise _invalid()
            measured_at = document["measured_at"]
            if not isinstance(measured_at, str) or not measured_at.endswith("Z"):
                raise _invalid()
            timestamp = datetime.fromisoformat(measured_at)
            offset = timestamp.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise _invalid()
            metrics, maxima = _metrics(document["metrics"]), _metrics(document["acceptance_maxima"])
            duration = _integer(document["duration_seconds"], 86400)
            samples = _integer(document["sample_count"], 1_000_000)
            raw_minima = document["acceptance_minima"]
            paths = document["workload_paths"]
            if (
                not isinstance(raw_minima, dict)
                or set(raw_minima) != _MINIMA
                or not isinstance(paths, list)
                or len(paths) != len(MEASUREMENT_PATHS)
                or any(not isinstance(path, str) for path in paths)
                or set(paths) != MEASUREMENT_PATHS
            ):
                raise _invalid()
            minima = []
            observed = {**dict(metrics), "duration_seconds": duration, "sample_count": samples}
            for name in sorted(_MINIMA):
                threshold = raw_minima[name]
                if (
                    type(threshold) not in (int, float)
                    or not math.isfinite(threshold)
                    or not 0 < threshold <= 1_000_000_000
                ):
                    raise _invalid()
                if observed[name] < threshold:
                    raise ResourceAdmissionError("resource_measurement_failed")
                minima.append((name, float(threshold)))
            if any(value > dict(maxima)[name] for name, value in metrics):
                raise ResourceAdmissionError("resource_measurement_failed")
            return cls(
                _uuid(document["measurement_id"]),
                _uuid(document["server_instance_id"]),
                _integer(document["identity_epoch"], 2**53 - 1),
                _digest(document["environment_sha256"]),
                _digest(document["workload_sha256"]),
                hashlib.sha256(payload).hexdigest(),
                timestamp,
                duration,
                samples,
                GlobalResourceLimits(
                    _integer(document["simultaneous_playbacks"], 1_000_000),
                    _integer(document["simultaneous_transfers"], 1_000_000),
                ),
                metrics,
                maxima,
                tuple(minima),
                tuple(sorted(paths)),
            )
        except UnicodeError, ValueError, TypeError, OverflowError, RecursionError:
            raise _invalid() from None


@dataclass(frozen=True, slots=True)
class InitializeResourceBudget:
    operation_id: UUID
    expected_revision: int
    report: ResourceMeasurement
    reviewed_report_sha256: str
    expected_environment_sha256: str
    limits: GlobalResourceLimits
    ceilings: GlobalResourceLimits

    def __post_init__(self) -> None:
        _integer(self.expected_revision, 2**53 - 1)
        if (
            _digest(self.reviewed_report_sha256) != self.report.report_sha256
            or _digest(self.expected_environment_sha256) != self.report.environment_sha256
        ):
            raise ResourceAdmissionError("resource_measurement_mismatch")
        for limit, ceiling, tested in (
            (self.limits.playbacks, self.ceilings.playbacks, self.report.simultaneous.playbacks),
            (self.limits.transfers, self.ceilings.transfers, self.report.simultaneous.transfers),
        ):
            positive_limit(limit)
            positive_limit(ceiling)
            if not limit <= ceiling <= tested:
                raise ResourceAdmissionError("resource_budget_exceeded")

    @property
    def digest(self) -> str:
        payload = {
            "action": "INITIALIZE_RESOURCE_BUDGET_V1",
            "operation_id": str(self.operation_id),
            "expected_revision": self.expected_revision,
            "report_sha256": self.report.report_sha256,
            "server_instance_id": str(self.report.server_instance_id),
            "identity_epoch": self.report.identity_epoch,
            "environment_sha256": self.expected_environment_sha256,
            "limits": [self.limits.playbacks, self.limits.transfers],
            "ceilings": [self.ceilings.playbacks, self.ceilings.transfers],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
