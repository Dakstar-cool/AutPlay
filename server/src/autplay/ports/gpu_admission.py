"""GPU process boundary; implementations must fail closed on authority loss."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from autplay.domain.gpu_admission import (
    GpuLease,
    GpuReleaseProof,
    GpuReservationKind,
)
from autplay.domain.gpu_memory import GpuMemorySample


class GpuAdmissionAuthority(Protocol):
    def acquire(
        self,
        *,
        device_uuid: UUID,
        kind: GpuReservationKind,
        holder_id: UUID,
        model_identity_sha256: bytes,
        requested_vram_bytes: int,
    ) -> GpuLease: ...

    def require_live(self, lease: GpuLease) -> GpuLease: ...

    def heartbeat(self, lease: GpuLease, *, measured_vram_bytes: int) -> GpuLease: ...

    def cancel_face(self, device_uuid: UUID) -> GpuLease: ...

    def release(self, lease: GpuLease, proof: GpuReleaseProof) -> None: ...


class GpuProcessControl(Protocol):
    def stop_new_work(self) -> None: ...

    def unload_or_exit_after_batch(self) -> None: ...


class GpuMemoryProbe(Protocol):
    def sample(self, *, device_uuid: UUID, process_id: int) -> GpuMemorySample: ...
