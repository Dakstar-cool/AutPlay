"""Fail-closed NVML memory gates for a leased GPU process.

These gates do not acquire a lease or start CUDA. A process must recheck the
database lease at each batch boundary and use these measurements at load,
readiness, and release boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from autplay.domain.gpu_admission import (
    GPU_RELEASE_SAMPLE_MAX_AGE,
    GpuAdmissionError,
    GpuLease,
    GpuReleaseProof,
)


@dataclass(frozen=True, slots=True)
class GpuMemorySample:
    device_uuid: UUID
    process_id: int
    process_present: bool
    process_used_bytes: int
    device_free_bytes: int
    device_total_bytes: int
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.device_uuid) is not UUID
            or type(self.process_id) is not int
            or self.process_id <= 0
            or type(self.process_present) is not bool
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.process_used_bytes,
                    self.device_free_bytes,
                    self.device_total_bytes,
                )
            )
            or self.device_total_bytes == 0
            or self.device_free_bytes > self.device_total_bytes
            or (not self.process_present and self.process_used_bytes != 0)
            or self.observed_at.tzinfo is None
        ):
            raise GpuAdmissionError("gpu_nvml_measurement_invalid")


def require_gpu_memory_ready(
    lease: GpuLease,
    sample: GpuMemorySample,
    *,
    safety_margin_bytes: int,
    session_loaded: bool,
    now: datetime,
) -> None:
    """Check measured residual before load and process use after load."""

    _require_sample(lease, sample, now)
    if (
        type(safety_margin_bytes) is not int
        or safety_margin_bytes < 0
        or type(session_loaded) is not bool
        or sample.device_total_bytes < safety_margin_bytes
    ):
        raise GpuAdmissionError("gpu_nvml_budget_invalid")
    if session_loaded:
        if (
            not sample.process_present
            or sample.process_used_bytes > lease.requested_vram_bytes
            or sample.device_free_bytes < safety_margin_bytes
        ):
            raise GpuAdmissionError("gpu_nvml_budget_exceeded")
    elif (
        sample.process_present
        or sample.device_free_bytes < lease.requested_vram_bytes + safety_margin_bytes
    ):
        raise GpuAdmissionError("gpu_nvml_budget_exceeded")


def gpu_release_proof_from_sample(
    lease: GpuLease,
    sample: GpuMemorySample,
    *,
    process_exit_confirmed: bool,
    session_unloaded: bool,
    now: datetime,
) -> GpuReleaseProof:
    """Require the compute context to disappear before releasing the lease."""

    _require_sample(lease, sample, now)
    if sample.process_present:
        raise GpuAdmissionError("gpu_nvml_process_still_present")
    if type(process_exit_confirmed) is not bool or type(session_unloaded) is not bool:
        raise GpuAdmissionError("gpu_release_proof_invalid")
    proof = GpuReleaseProof(
        nvml_process_used_bytes=sample.process_used_bytes,
        observed_at=sample.observed_at,
        process_exit_confirmed=process_exit_confirmed,
        session_unloaded=session_unloaded,
    )
    proof.require_fresh_release(now)
    return proof


def _require_sample(lease: GpuLease, sample: GpuMemorySample, now: datetime) -> None:
    if (
        type(lease) is not GpuLease
        or type(sample) is not GpuMemorySample
        or type(now) is not datetime
        or now.tzinfo is None
        or sample.device_uuid != lease.device_uuid
        or sample.observed_at > now
        or now - sample.observed_at > GPU_RELEASE_SAMPLE_MAX_AGE
    ):
        raise GpuAdmissionError("gpu_nvml_sample_stale")
