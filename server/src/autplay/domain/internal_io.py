"""Measured capacity for contained internal byte work, separate from audio leases."""

import hashlib
import json
import math
from dataclasses import dataclass
from uuid import UUID

from .resource_admission import ResourceAdmissionError, positive_limit
from .resource_measurements import (
    MAX_MEASUREMENT_BYTES,
    ResourceMeasurement,
    _digest,
    _integer,
    _pairs,
)

INTERNAL_IO_PATHS = frozenset(
    {
        "INGEST_ANALYSIS_PUBLICATION",
        "FINALIZED_STAGING_CLEANUP",
        "PROVIDER_STAGING_CLEANUP",
        "PROVIDER_SCRATCH_RETIREMENT",
        "ORPHAN_RETIREMENT",
        "ORPHAN_MISSING_CHECK",
        "VAULT_INVENTORY",
        "DEVICE_UPLOAD_CLEANUP",
    }
)
METADATA_IO_PATHS = INTERNAL_IO_PATHS | {"METADATA_ENRICHMENT"}
TRAINING_IO_PATHS = METADATA_IO_PATHS | {
    "TRAINING_DATASET_CHECKPOINT",
    "TRAINING_ROOT_CLEANUP",
}
_FIELDS = frozenset(
    {
        "version",
        "resource_measurement",
        "simultaneous_internal_io",
        "workload_paths",
        "internal_mib_per_second",
        "minimum_internal_mib_per_second",
        "successful_internal_operations",
        "minimum_successful_internal_operations",
        "worst_permitted_mix",
    }
)


@dataclass(frozen=True, slots=True)
class InternalIoMeasurement:
    resources: ResourceMeasurement
    simultaneous: int
    report_sha256: str
    mib_per_second: float
    minimum_mib_per_second: float
    successful_operations: int
    minimum_successful_operations: int
    version: int
    workload_paths: tuple[str, ...]

    @classmethod
    def parse(cls, payload: bytes) -> InternalIoMeasurement:
        invalid = ResourceAdmissionError("internal_io_measurement_invalid")
        if not 1 <= len(payload) <= MAX_MEASUREMENT_BYTES:
            raise invalid
        try:
            document = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(document, dict) or set(document) != _FIELDS:
                raise invalid
            version = _integer(document["version"], 3)
            required_paths = {
                1: INTERNAL_IO_PATHS,
                2: METADATA_IO_PATHS,
                3: TRAINING_IO_PATHS,
            }[version]
            if document["worst_permitted_mix"] is not True:
                raise invalid
            paths = document["workload_paths"]
            if (
                not isinstance(paths, list)
                or len(paths) != len(required_paths)
                or any(not isinstance(path, str) for path in paths)
                or set(paths) != required_paths
            ):
                raise invalid
            resources = ResourceMeasurement.parse(
                json.dumps(
                    document["resource_measurement"], sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            simultaneous = _integer(document["simultaneous_internal_io"], 1_000_000)
            rate, minimum = (
                document["internal_mib_per_second"],
                document["minimum_internal_mib_per_second"],
            )
            for value in (rate, minimum):
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 < value <= 1_000_000_000
                ):
                    raise invalid
            successes = _integer(document["successful_internal_operations"], 1_000_000_000)
            minimum_successes = _integer(
                document["minimum_successful_internal_operations"], 1_000_000_000
            )
            if successes > dict(resources.metrics)["successful_operations"]:
                raise invalid
            if rate < minimum or successes < minimum_successes:
                raise ResourceAdmissionError("internal_io_measurement_failed")
            return cls(
                resources,
                simultaneous,
                hashlib.sha256(payload).hexdigest(),
                float(rate),
                float(minimum),
                successes,
                minimum_successes,
                version,
                tuple(sorted(paths)),
            )
        except UnicodeError, ValueError, TypeError, OverflowError, RecursionError:
            raise invalid from None


@dataclass(frozen=True, slots=True)
class InitializeInternalIoBudget:
    operation_id: UUID
    expected_revision: int
    report: InternalIoMeasurement
    reviewed_report_sha256: str
    expected_environment_sha256: str
    limit: int
    ceiling: int

    def __post_init__(self) -> None:
        _integer(self.expected_revision, 2**53 - 1)
        positive_limit(self.limit)
        positive_limit(self.ceiling)
        if not self.limit <= self.ceiling <= self.report.simultaneous:
            raise ResourceAdmissionError("internal_io_budget_exceeded")
        if (
            _digest(self.reviewed_report_sha256) != self.report.report_sha256
            or _digest(self.expected_environment_sha256) != self.report.resources.environment_sha256
        ):
            raise ResourceAdmissionError("internal_io_measurement_mismatch")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "action": "INITIALIZE_INTERNAL_IO_V1",
                    "operation_id": str(self.operation_id),
                    "expected_revision": self.expected_revision,
                    "report_sha256": self.report.report_sha256,
                    "environment_sha256": self.expected_environment_sha256,
                    "limit": self.limit,
                    "ceiling": self.ceiling,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
