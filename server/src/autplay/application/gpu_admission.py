"""Batch-boundary guard shared by future Face and Sona GPU processes."""

from __future__ import annotations

from datetime import UTC, datetime

from autplay.domain.gpu_admission import GpuAdmissionError, GpuLease, GpuReleaseProof
from autplay.domain.gpu_memory import gpu_release_proof_from_sample, require_gpu_memory_ready
from autplay.ports.gpu_admission import (
    GpuAdmissionAuthority,
    GpuMemoryProbe,
    GpuProcessControl,
)


def require_gpu_batch(
    authority: GpuAdmissionAuthority, process: GpuProcessControl, lease: GpuLease
) -> GpuLease:
    """Check PostgreSQL before every bounded batch; stop on any authority failure."""
    try:
        return authority.require_live(lease)
    except GpuAdmissionError:
        _stop(process)
        raise
    except Exception as error:
        _stop(process)
        raise GpuAdmissionError("gpu_authority_unavailable") from error


def require_gpu_memory_gate(
    authority: GpuAdmissionAuthority,
    process: GpuProcessControl,
    probe: GpuMemoryProbe,
    lease: GpuLease,
    *,
    process_id: int,
    safety_margin_bytes: int,
    session_loaded: bool,
) -> GpuLease:
    """Fence the lease and measured VRAM at pre-load and loaded boundaries."""

    current = require_gpu_batch(authority, process, lease)
    try:
        sample = probe.sample(device_uuid=current.device_uuid, process_id=process_id)
        require_gpu_memory_ready(
            current,
            sample,
            safety_margin_bytes=safety_margin_bytes,
            session_loaded=session_loaded,
            now=datetime.now(UTC),
        )
        if session_loaded:
            return authority.heartbeat(current, measured_vram_bytes=sample.process_used_bytes)
        return current
    except GpuAdmissionError:
        _stop(process)
        raise
    except Exception as error:
        _stop(process)
        raise GpuAdmissionError("gpu_nvml_unavailable") from error


def measure_gpu_release(
    probe: GpuMemoryProbe,
    lease: GpuLease,
    *,
    process_id: int,
    process_exit_confirmed: bool,
    session_unloaded: bool,
) -> GpuReleaseProof:
    """Create a DB release proof only after the exact PID leaves the device."""

    try:
        sample = probe.sample(device_uuid=lease.device_uuid, process_id=process_id)
    except GpuAdmissionError:
        raise
    except Exception as error:
        raise GpuAdmissionError("gpu_nvml_unavailable") from error
    return gpu_release_proof_from_sample(
        lease,
        sample,
        process_exit_confirmed=process_exit_confirmed,
        session_unloaded=session_unloaded,
        now=datetime.now(UTC),
    )


def _stop(process: GpuProcessControl) -> None:
    try:
        process.stop_new_work()
    finally:
        process.unload_or_exit_after_batch()
