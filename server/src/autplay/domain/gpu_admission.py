"""Generation-fenced process contract for shared Face/Sona GPU use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

GPU_LEASE_TTL = timedelta(seconds=20)
GPU_RELEASE_SAMPLE_MAX_AGE = timedelta(seconds=5)


class GpuAdmissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GpuReservationKind(StrEnum):
    FACE = "FACE"
    SONA = "SONA"


@dataclass(frozen=True, slots=True)
class GpuLease:
    device_uuid: UUID
    kind: GpuReservationKind
    holder_id: UUID
    model_identity_sha256: bytes
    reservation_generation: int
    authority_generation: int
    cancellation_generation: int
    requested_vram_bytes: int
    lease_until: datetime

    def __post_init__(self) -> None:
        if (
            len(self.model_identity_sha256) != 32
            or self.reservation_generation < 1
            or self.authority_generation < 1
            or self.cancellation_generation < 0
            or self.requested_vram_bytes < 1
            or self.lease_until.tzinfo is None
        ):
            raise GpuAdmissionError("gpu_lease_invalid")


@dataclass(frozen=True, slots=True)
class GpuReleaseProof:
    nvml_process_used_bytes: int
    observed_at: datetime
    process_exit_confirmed: bool
    session_unloaded: bool

    def require_fresh_release(self, now: datetime) -> None:
        if (
            self.nvml_process_used_bytes != 0
            or not (self.process_exit_confirmed or self.session_unloaded)
            or self.observed_at.tzinfo is None
            or self.observed_at > now
            or now - self.observed_at > GPU_RELEASE_SAMPLE_MAX_AGE
        ):
            raise GpuAdmissionError("gpu_release_proof_invalid")
