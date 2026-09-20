"""Retained cgroup v2 ownership for fixed children gated on durable GO."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from autplay.domain.resource_admission import ResourceAdmissionError


def _require_linux() -> None:
    if sys.platform != "linux":
        raise ResourceAdmissionError("resource_process_tree_unavailable")


def _private_flags() -> int:
    # Windows os type stubs intentionally omit Linux-only flags.
    linux = cast(Any, os)
    return int(linux.O_NOFOLLOW) | int(linux.O_CLOEXEC)


def _directory_flags() -> int:
    return os.O_RDONLY | int(cast(Any, os).O_DIRECTORY) | _private_flags()


def _cgroup2(descriptor: int) -> None:
    # fstatfs writes the native struct; only its first native-long field is used.
    # An oversized, native-long-aligned buffer covers the supported Linux ABIs.
    api = ctypes.CDLL(None, use_errno=True)
    api.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    api.fstatfs.restype = ctypes.c_int
    result = (ctypes.c_long * 64)()
    if api.fstatfs(descriptor, ctypes.byref(result)) != 0 or result[0] != 0x63677270:
        raise ResourceAdmissionError("resource_process_tree_unavailable")


def _directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ResourceAdmissionError("resource_process_tree_unavailable")
    flags = _directory_flags()
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class LinuxCgroupTree:
    """Own one private domain cgroup beneath an explicitly delegated root.

    This never mounts, delegates or changes controllers. The fixed child must not
    fork before attachment/GO. Root and child must not migrate themselves or use
    external process brokers; this is containment of trusted tools, not a sandbox
    for hostile same-UID code. Only Popen may reap the exact root process.
    """

    def __init__(self, root: Path) -> None:
        _require_linux()
        self._lock = threading.Lock()
        self._stopped = self._sealed = self._attach_attempted = False
        self._parent = self._group = -1
        self._files: dict[str, int] = {}
        self._name = f"autplay-{uuid4().hex}"
        made = False
        try:
            self._parent = _directory(root)
            _cgroup2(self._parent)
            os.mkdir(self._name, mode=0o700, dir_fd=self._parent)
            made = True
            self._group = os.open(
                self._name,
                _directory_flags(),
                dir_fd=self._parent,
            )
            for name, mode in (
                ("cgroup.events", os.O_RDONLY),
                ("cgroup.type", os.O_RDONLY),
                ("cgroup.procs", os.O_WRONLY),
                ("cgroup.kill", os.O_WRONLY),
            ):
                self._files[name] = os.open(name, mode | _private_flags(), dir_fd=self._group)
            self._identity = os.fstat(self._group)
            if self._read("cgroup.type") != b"domain\n" or not self._empty():
                raise ResourceAdmissionError("resource_process_tree_unavailable")
            self._evidence = hashlib.sha256(
                b"autplay:linux-cgroup-empty:v1:"
                + uuid4().bytes
                + f"{self._identity.st_dev}:{self._identity.st_ino}".encode("ascii")
            ).digest()
        except BaseException:
            if made:
                with suppress(OSError):
                    os.rmdir(self._name, dir_fd=self._parent)
            self._close_descriptors()
            raise

    def _check(self) -> None:
        if self._group < 0:
            raise ResourceAdmissionError("resource_process_tree_closed")
        actual = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
        if not os.path.samestat(self._identity, actual):
            raise OSError("resource_process_tree_identity")

    def _read(self, name: str) -> bytes:
        descriptor = self._files[name]
        os.lseek(descriptor, 0, os.SEEK_SET)
        result = os.read(descriptor, 4097)
        if len(result) > 4096:
            raise OSError("resource_process_tree_unavailable")
        return result

    def _write(self, name: str, payload: bytes) -> None:
        self._check()
        descriptor = self._files[name]
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.write(descriptor, payload) != len(payload):
            raise OSError("resource_process_tree_unavailable")

    def _empty(self) -> bool:
        self._check()
        values = [line.split() for line in self._read("cgroup.events").splitlines()]
        populated = [fields for fields in values if fields and fields[0] == b"populated"]
        if len(populated) != 1 or populated[0] not in ([b"populated", b"0"], [b"populated", b"1"]):
            raise OSError("resource_process_tree_unavailable")
        return populated[0][1] == b"0"

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._check()
            if self._stopped or self._sealed or self._attach_attempted:
                raise ResourceAdmissionError("resource_process_tree_stopped")
            # Pinned CPython 3.14 uses this lock for every Popen poll/wait. Keep an
            # exited child unreaped through the numeric cgroup.procs write, so its
            # PID cannot be reused between the liveness check and attachment.
            wait_lock = getattr(process, "_waitpid_lock", None)
            if not isinstance(wait_lock, type(threading.Lock())):
                raise ResourceAdmissionError("resource_process_tree_identity")
            # A caller's blocking Popen.wait() must not deadlock pre-GO attach
            # and the stop latch. A busy reaper is not attachment authority.
            if not wait_lock.acquire(blocking=False):
                raise ResourceAdmissionError("resource_process_tree_busy")
            try:
                linux = cast(Any, os)
                if (
                    process.returncode is not None
                    or linux.waitid(
                        linux.P_PID, process.pid, linux.WEXITED | linux.WNOHANG | linux.WNOWAIT
                    )
                    is not None
                ):
                    raise ResourceAdmissionError("resource_process_tree_identity")
                self._attach_attempted = True
                try:
                    self._write("cgroup.procs", f"{process.pid}\n".encode("ascii"))
                except BaseException:
                    self._stopped = True
                    raise
            finally:
                wait_lock.release()

    def request_stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._group >= 0:
                self._write("cgroup.kill", b"1\n")

    def seal_if_empty(self) -> bytes | None:
        with self._lock:
            if not self._empty():
                return None
            self._sealed = True
            return self._evidence

    def close_after_exit(self) -> None:
        with self._lock:
            if self._group < 0:
                return
            if not self._sealed or not self._empty():
                raise ResourceAdmissionError("resource_execution_unconfirmed")
            os.rmdir(self._name, dir_fd=self._parent)
            self._close_descriptors()

    def _close_descriptors(self) -> None:
        for descriptor in (*self._files.values(), self._group, self._parent):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
        self._files.clear()
        self._group = self._parent = -1
