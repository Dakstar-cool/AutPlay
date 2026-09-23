"""Optional NVML process/device memory reader; never imports a CUDA runtime."""

from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from autplay.domain.gpu_admission import GpuAdmissionError
from autplay.domain.gpu_memory import GpuMemorySample

_SUCCESS = 0
_INSUFFICIENT_SIZE = 7
_VALUE_NOT_AVAILABLE = (1 << 64) - 1
_MAX_PROCESSES = 4096


class _Memory(C.Structure):
    _fields_ = [("total", C.c_ulonglong), ("free", C.c_ulonglong), ("used", C.c_ulonglong)]


class _Process(C.Structure):
    _fields_ = [
        ("pid", C.c_uint),
        ("usedGpuMemory", C.c_ulonglong),
        ("gpuInstanceId", C.c_uint),
        ("computeInstanceId", C.c_uint),
    ]


class NvmlGpuMemoryProbe:
    """Read one exact NVIDIA device UUID and PID, failing closed on missing data.

    Sampling initializes and shuts down NVML in the same call. NVML reference counts
    initialization, so this does not consume another caller's initialization.
    """

    def __init__(self) -> None:
        try:
            library = C.CDLL(_library_name())
            self._init = library.nvmlInit_v2
            self._shutdown = library.nvmlShutdown
            self._handle = library.nvmlDeviceGetHandleByUUID
            self._memory = library.nvmlDeviceGetMemoryInfo
            self._processes = library.nvmlDeviceGetComputeRunningProcesses_v3
        except (OSError, AttributeError) as error:
            raise GpuAdmissionError("gpu_nvml_unavailable") from error
        self._init.argtypes = []
        self._init.restype = C.c_int
        self._shutdown.argtypes = []
        self._shutdown.restype = C.c_int
        self._handle.argtypes = [C.c_char_p, C.POINTER(C.c_void_p)]
        self._handle.restype = C.c_int
        self._memory.argtypes = [C.c_void_p, C.POINTER(_Memory)]
        self._memory.restype = C.c_int
        self._processes.argtypes = [C.c_void_p, C.POINTER(C.c_uint), C.POINTER(_Process)]
        self._processes.restype = C.c_int

    def sample(self, *, device_uuid: UUID, process_id: int) -> GpuMemorySample:
        if type(device_uuid) is not UUID or type(process_id) is not int or process_id <= 0:
            raise GpuAdmissionError("gpu_nvml_request_invalid")
        _check(self._init())
        try:
            observed_at = datetime.now(UTC)
            handle = C.c_void_p()
            _check(self._handle(f"GPU-{device_uuid}".encode("ascii"), C.byref(handle)))
            memory = _Memory()
            _check(self._memory(handle, C.byref(memory)))
            process_present, process_used = self._process_memory(handle, process_id)
            return GpuMemorySample(
                device_uuid=device_uuid,
                process_id=process_id,
                process_present=process_present,
                process_used_bytes=process_used,
                device_free_bytes=memory.free,
                device_total_bytes=memory.total,
                observed_at=observed_at,
            )
        finally:
            _check(self._shutdown())

    def _process_memory(self, handle: C.c_void_p, process_id: int) -> tuple[bool, int]:
        count = C.c_uint(0)
        result = self._processes(handle, C.byref(count), None)
        if result == _SUCCESS and count.value == 0:
            return False, 0
        if result != _INSUFFICIENT_SIZE:
            _check(result)
            raise GpuAdmissionError("gpu_nvml_process_query_invalid")
        for _ in range(3):
            if count.value == 0 or count.value > _MAX_PROCESSES:
                raise GpuAdmissionError("gpu_nvml_process_count_invalid")
            capacity = count.value
            buffer = (_Process * capacity)()
            result = self._processes(handle, C.byref(count), buffer)
            if result == _INSUFFICIENT_SIZE:
                continue
            _check(result)
            if count.value > capacity:
                raise GpuAdmissionError("gpu_nvml_process_count_invalid")
            used = 0
            found = False
            for entry in buffer[: count.value]:
                if entry.pid == process_id:
                    if entry.usedGpuMemory == _VALUE_NOT_AVAILABLE:
                        raise GpuAdmissionError("gpu_nvml_process_memory_unavailable")
                    found = True
                    used += entry.usedGpuMemory
            return found, used
        raise GpuAdmissionError("gpu_nvml_process_list_changed")


def _check(code: int) -> None:
    if code != _SUCCESS:
        raise GpuAdmissionError("gpu_nvml_error")


def _library_name() -> str:
    if sys.platform != "win32":
        return ctypes.util.find_library("nvidia-ml") or "libnvidia-ml.so.1"
    # NVIDIA documents these two Windows locations; neither is normally in PATH.
    candidates: list[Path] = []
    for root in (os.environ.get("PROGRAMW6432"), os.environ.get("PROGRAMFILES")):
        if root:
            candidates.append(Path(root) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll")
    if system_root := os.environ.get("SYSTEMROOT"):
        candidates.append(Path(system_root) / "System32" / "nvml.dll")
    for path in candidates:
        if path.is_absolute() and path.is_file():
            return str(path)
    raise GpuAdmissionError("gpu_nvml_unavailable")
