"""NVML memory gates, using synthetic measurements without a GPU."""

from __future__ import annotations

import ctypes as C
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from autplay.adapters import nvml_gpu_memory
from autplay.adapters.nvml_gpu_memory import NvmlGpuMemoryProbe, _Memory
from autplay.application.gpu_admission import require_gpu_memory_gate
from autplay.domain.gpu_admission import GpuAdmissionError, GpuLease, GpuReservationKind
from autplay.domain.gpu_memory import (
    GpuMemorySample,
    gpu_release_proof_from_sample,
    require_gpu_memory_ready,
)
from autplay.ports.gpu_admission import GpuAdmissionAuthority, GpuMemoryProbe, GpuProcessControl


def _lease(now: datetime) -> GpuLease:
    return GpuLease(
        device_uuid=uuid4(),
        kind=GpuReservationKind.FACE,
        holder_id=uuid4(),
        model_identity_sha256=b"m" * 32,
        reservation_generation=1,
        authority_generation=1,
        cancellation_generation=0,
        requested_vram_bytes=4_000,
        lease_until=now + timedelta(seconds=20),
    )


def _sample(lease: GpuLease, now: datetime) -> GpuMemorySample:
    return GpuMemorySample(
        device_uuid=lease.device_uuid,
        process_id=1234,
        process_present=False,
        process_used_bytes=0,
        device_free_bytes=8_000,
        device_total_bytes=10_000,
        observed_at=now,
    )


def test_nvml_load_readiness_and_release_require_fresh_exact_measurement() -> None:
    now = datetime.now(UTC)
    lease = _lease(now)
    before = _sample(lease, now)
    require_gpu_memory_ready(
        lease, before, safety_margin_bytes=1_000, session_loaded=False, now=now
    )
    loaded = replace(
        before, process_present=True, process_used_bytes=3_500, device_free_bytes=4_500
    )
    require_gpu_memory_ready(lease, loaded, safety_margin_bytes=1_000, session_loaded=True, now=now)
    with pytest.raises(GpuAdmissionError, match="gpu_nvml_process_still_present"):
        gpu_release_proof_from_sample(
            lease, loaded, process_exit_confirmed=False, session_unloaded=True, now=now
        )
    proof = gpu_release_proof_from_sample(
        lease, before, process_exit_confirmed=False, session_unloaded=True, now=now
    )
    assert proof.nvml_process_used_bytes == 0
    assert proof.session_unloaded


def test_nvml_budget_staleness_and_device_mismatch_fail_closed() -> None:
    now = datetime.now(UTC)
    lease = _lease(now)
    sample = _sample(lease, now)
    for altered, code in (
        (replace(sample, device_free_bytes=4_999), "gpu_nvml_budget_exceeded"),
        (replace(sample, observed_at=now - timedelta(seconds=6)), "gpu_nvml_sample_stale"),
        (replace(sample, device_uuid=uuid4()), "gpu_nvml_sample_stale"),
    ):
        with pytest.raises(GpuAdmissionError, match=code):
            require_gpu_memory_ready(
                lease, altered, safety_margin_bytes=1_000, session_loaded=False, now=now
            )
    with pytest.raises(GpuAdmissionError, match="gpu_nvml_budget_exceeded"):
        require_gpu_memory_ready(
            lease,
            replace(sample, process_present=True, process_used_bytes=4_001),
            safety_margin_bytes=1_000,
            session_loaded=True,
            now=now,
        )


def test_nvml_process_reader_rejects_unavailable_process_memory() -> None:
    probe = NvmlGpuMemoryProbe.__new__(NvmlGpuMemoryProbe)
    calls = 0

    def processes(_handle: object, count_ptr: object, buffer: object) -> int:
        nonlocal calls
        calls += 1
        count = C.cast(count_ptr, C.POINTER(C.c_uint))
        if buffer is None:
            count[0] = 1
            return 7
        count[0] = 1
        buffer[0].pid = 1234
        buffer[0].usedGpuMemory = (1 << 64) - 1
        return 0

    probe._processes = processes  # type: ignore[method-assign]
    with pytest.raises(GpuAdmissionError, match="gpu_nvml_process_memory_unavailable"):
        probe._process_memory(C.c_void_p(1), 1234)
    assert calls == 2


def test_nvml_process_reader_uses_exact_pid_and_bounded_list() -> None:
    probe = NvmlGpuMemoryProbe.__new__(NvmlGpuMemoryProbe)

    def processes(_handle: object, count_ptr: object, buffer: object) -> int:
        count = C.cast(count_ptr, C.POINTER(C.c_uint))
        count[0] = 2
        if buffer is None:
            return 7
        assert isinstance(buffer, C.Array)
        buffer[0].pid = 2222
        buffer[0].usedGpuMemory = 100
        buffer[1].pid = 1234
        buffer[1].usedGpuMemory = 3_500
        return 0

    probe._processes = processes  # type: ignore[method-assign]
    assert probe._process_memory(C.c_void_p(1), 1234) == (True, 3_500)
    assert probe._process_memory(C.c_void_p(1), 9999) == (False, 0)


def test_nvml_sample_uses_exact_gpu_uuid_and_closes_library() -> None:
    probe = NvmlGpuMemoryProbe.__new__(NvmlGpuMemoryProbe)
    device_uuid = uuid4()
    events: list[str] = []

    def init() -> int:
        events.append("init")
        return 0

    def handle(name: bytes, pointer: object) -> int:
        assert name == f"GPU-{device_uuid}".encode("ascii")
        C.cast(pointer, C.POINTER(C.c_void_p))[0] = C.c_void_p(7)
        events.append("handle")
        return 0

    def memory(_handle: object, pointer: object) -> int:
        item = C.cast(pointer, C.POINTER(_Memory))[0]
        item.total = 10_000
        item.free = 8_000
        item.used = 2_000
        events.append("memory")
        return 0

    def processes(_handle: object, count_pointer: object, buffer: object) -> int:
        assert buffer is None
        C.cast(count_pointer, C.POINTER(C.c_uint))[0] = 0
        events.append("processes")
        return 0

    def shutdown() -> int:
        events.append("shutdown")
        return 0

    probe._init = init  # type: ignore[method-assign]
    probe._handle = handle  # type: ignore[method-assign]
    probe._memory = memory  # type: ignore[method-assign]
    probe._processes = processes  # type: ignore[method-assign]
    probe._shutdown = shutdown  # type: ignore[method-assign]
    sample = probe.sample(device_uuid=device_uuid, process_id=1234)
    assert sample.device_free_bytes == 8_000
    assert not sample.process_present
    assert events == ["init", "handle", "memory", "processes", "shutdown"]


def test_windows_nvml_loader_uses_documented_absolute_driver_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = tmp_path / "NVIDIA Corporation" / "NVSMI" / "nvml.dll"
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"")
    monkeypatch.setattr(nvml_gpu_memory.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMW6432", str(tmp_path))
    assert nvml_gpu_memory._library_name() == str(driver)


def test_invalid_nvml_measurement_cannot_make_release_proof() -> None:
    now = datetime.now(UTC)
    lease = _lease(now)
    with pytest.raises(GpuAdmissionError, match="gpu_nvml_measurement_invalid"):
        replace(_sample(lease, now), process_present=False, process_used_bytes=1)
    with pytest.raises(GpuAdmissionError, match="gpu_release_proof_invalid"):
        gpu_release_proof_from_sample(
            lease,
            _sample(lease, now),
            process_exit_confirmed=False,
            session_unloaded=False,
            now=now,
        )


def test_memory_gate_fences_database_before_measurement_and_stops_on_nvml_failure() -> None:
    now = datetime.now(UTC)
    lease = _lease(now)
    events: list[str] = []

    class Authority:
        def require_live(self, incoming: GpuLease) -> GpuLease:
            events.append("lease")
            return incoming

        def heartbeat(self, incoming: GpuLease, *, measured_vram_bytes: int) -> GpuLease:
            events.append(f"heartbeat:{measured_vram_bytes}")
            return incoming

    class Process:
        def stop_new_work(self) -> None:
            events.append("stop")

        def unload_or_exit_after_batch(self) -> None:
            events.append("unload")

    class Probe:
        def sample(self, *, device_uuid: object, process_id: int) -> GpuMemorySample:
            events.append("sample")
            assert device_uuid == lease.device_uuid and process_id == 1234
            return replace(
                _sample(lease, now),
                process_present=True,
                process_used_bytes=3_500,
                device_free_bytes=4_500,
            )

    authority = cast(GpuAdmissionAuthority, Authority())
    process = cast(GpuProcessControl, Process())
    probe = cast(GpuMemoryProbe, Probe())
    assert (
        require_gpu_memory_gate(
            authority,
            process,
            probe,
            lease,
            process_id=1234,
            safety_margin_bytes=1_000,
            session_loaded=True,
        )
        == lease
    )
    assert events == ["lease", "sample", "heartbeat:3500"]
    events.clear()
    with pytest.raises(GpuAdmissionError, match="gpu_nvml_budget_exceeded"):
        require_gpu_memory_gate(
            authority,
            process,
            probe,
            lease,
            process_id=1234,
            safety_margin_bytes=5_000,
            session_loaded=True,
        )
    assert events == ["lease", "sample", "stop", "unload"]
