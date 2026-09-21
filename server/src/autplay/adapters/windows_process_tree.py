"""Retained Windows Job Object: normal CreateProcess descendants cannot break away."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import threading
from typing import Any, cast
from uuid import uuid4

from autplay.domain.resource_admission import ResourceAdmissionError


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64),
        ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64),
        ("period_kernel_time", ctypes.c_int64),
        ("page_faults", ctypes.c_uint32),
        ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32),
        ("terminated_processes", ctypes.c_uint32),
    ]


class WindowsJobTree:
    """Private non-inheritable job, retained until exact empty-tree acknowledgement.

    This contains the fixed provider launcher's subprocesses, not arbitrary hostile
    code or work delegated to external brokers such as WMI. No PID is reopened.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise ResourceAdmissionError("resource_process_tree_unavailable")
        self._lock = threading.Lock()
        self._stopped = self._sealed = self._attach_attempted = False
        self._evidence = hashlib.sha256(b"autplay:windows-job-empty:v1:" + uuid4().bytes).digest()
        # ctypes' portable stubs omit Windows-only exports. Bind them only after
        # the platform gate; every native function below has an explicit ABI.
        self._api: ctypes.CDLL = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        self._api.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._api.CreateJobObjectW.restype = ctypes.c_void_p
        self._api.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._api.SetInformationJobObject.restype = ctypes.c_int
        self._api.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._api.AssignProcessToJobObject.restype = ctypes.c_int
        self._api.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._api.QueryInformationJobObject.restype = ctypes.c_int
        self._api.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._api.TerminateJobObject.restype = ctypes.c_int
        self._api.GetProcessId.argtypes = [ctypes.c_void_p]
        self._api.GetProcessId.restype = ctypes.c_uint32
        self._api.CloseHandle.argtypes = [ctypes.c_void_p]
        self._api.CloseHandle.restype = ctypes.c_int
        handle = self._api.CreateJobObjectW(None, None)
        if not isinstance(handle, int) or handle == 0:
            raise self._failure()
        self._handle: int | None = handle
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; neither breakaway flag is set.
        try:
            if not self._api.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise self._failure()
        except BaseException:
            self._api.CloseHandle(handle)
            self._handle = None
            raise

    @staticmethod
    def _failure() -> OSError:
        return OSError(cast(Any, ctypes).get_last_error(), "resource_process_tree_unavailable")

    def _open_handle(self) -> int:
        if self._handle is None:
            raise ResourceAdmissionError("resource_process_tree_closed")
        return self._handle

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            job = self._open_handle()
            if self._stopped or self._sealed or self._attach_attempted:
                raise ResourceAdmissionError("resource_process_tree_stopped")
            # CPython 3.14.7 retains this HANDLE in Popen; opening by pid would permit ABA.
            handle = getattr(process, "_handle", None)
            if (
                not isinstance(handle, int)
                or handle <= 0
                or self._api.GetProcessId(handle) != process.pid
            ):
                raise ResourceAdmissionError("resource_process_tree_identity")
            self._attach_attempted = True
            if not self._api.AssignProcessToJobObject(job, handle):
                self._stopped = True
                raise self._failure()

    def request_stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._handle is not None and not self._api.TerminateJobObject(self._handle, 124):
                raise self._failure()

    def _empty(self) -> bool:
        accounting = _Accounting()
        if not self._api.QueryInformationJobObject(
            self._open_handle(), 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        ):
            raise self._failure()
        return bool(accounting.active_processes == 0)

    def seal_if_empty(self) -> bytes | None:
        with self._lock:
            if not self._empty():
                return None
            self._sealed = True
            return self._evidence

    def close_after_exit(self) -> None:
        with self._lock:
            if self._handle is None:
                return
            if not self._sealed or not self._empty():
                raise ResourceAdmissionError("resource_execution_unconfirmed")
            if not self._api.CloseHandle(self._handle):
                raise self._failure()
            self._handle = None
