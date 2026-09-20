"""Actual host evidence for a trusted offline restored-execution drain."""

from __future__ import annotations

import ctypes
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import rfc8785

from autplay.domain.recommendations import JsonValue
from autplay.domain.resource_admission import ResourceAdmissionError

from .linux_process_tree import _cgroup2, _directory, _directory_flags, _private_flags

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True, slots=True)
class OfflineProcessEvidence:
    evidence_sha256: bytes
    checked_pids: tuple[int, ...]
    checked_cgroups: tuple[str, ...]


class OfflineProcessEvidenceProbe:
    """Fail closed unless every persisted root PID and delegated tree is empty."""

    def __init__(self, linux_cgroup_root: Path | None) -> None:
        self._linux_cgroup_root = linux_cgroup_root

    def verify(self, pids: tuple[int, ...]) -> OfflineProcessEvidence:
        checked = tuple(sorted(set(pids)))
        if any(type(pid) is not int or not 1 <= pid < 2**63 for pid in checked):
            raise ResourceAdmissionError("offline_process_evidence_invalid")
        cgroups: tuple[str, ...]
        if os.name == "nt":
            if any(pid > 0xFFFFFFFF for pid in checked):
                raise ResourceAdmissionError("offline_process_evidence_invalid")
            for pid in checked:
                self._require_windows_process_absent(pid)
            cgroups = ()
        elif sys.platform == "linux":
            for pid in checked:
                self._require_posix_process_absent(pid)
            cgroups = self._require_linux_trees_empty()
        else:
            raise ResourceAdmissionError("offline_process_evidence_unavailable")
        document: dict[str, JsonValue] = {
            "schema_version": 1,
            "platform": "windows" if os.name == "nt" else "linux",
            "checked_pids": list(checked),
            "checked_cgroups": list(cgroups),
            "result": "ABSENT_AND_EMPTY",
        }
        return OfflineProcessEvidence(sha256(rfc8785.dumps(document)).digest(), checked, cgroups)

    @staticmethod
    def _require_windows_process_absent(pid: int) -> None:
        api: ctypes.CDLL = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        api.GetExitCodeProcess.restype = ctypes.c_int
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            if cast(Any, ctypes).get_last_error() == _ERROR_INVALID_PARAMETER:
                return
            raise ResourceAdmissionError("offline_process_evidence_unavailable")
        try:
            code = ctypes.c_uint32()
            if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ResourceAdmissionError("offline_process_evidence_unavailable")
            if code.value == _STILL_ACTIVE:
                raise ResourceAdmissionError("offline_process_still_running")
        finally:
            api.CloseHandle(handle)

    @staticmethod
    def _require_posix_process_absent(pid: int) -> None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as error:
            raise ResourceAdmissionError("offline_process_evidence_unavailable") from error
        raise ResourceAdmissionError("offline_process_still_running")

    def _require_linux_trees_empty(self) -> tuple[str, ...]:
        root = self._linux_cgroup_root
        if root is None:
            raise ResourceAdmissionError("offline_process_evidence_unavailable")
        root_descriptor = group_descriptor = events_descriptor = -1
        checked: list[str] = []
        try:
            root_descriptor = _directory(root)
            _cgroup2(root_descriptor)
            entries = sorted(os.listdir(root_descriptor))
            for name in entries:
                if not name.startswith("autplay-"):
                    continue
                group_descriptor = os.open(name, _directory_flags(), dir_fd=root_descriptor)
                events_descriptor = os.open(
                    "cgroup.events",
                    os.O_RDONLY | _private_flags(),
                    dir_fd=group_descriptor,
                )
                payload = os.read(events_descriptor, 4097)
                if len(payload) > 4096:
                    raise ResourceAdmissionError("offline_process_evidence_unavailable")
                values = [line.split() for line in payload.splitlines()]
                populated = [value for value in values if value and value[0] == b"populated"]
                if populated != [[b"populated", b"0"]]:
                    raise ResourceAdmissionError("offline_process_still_running")
                checked.append(name)
                os.close(events_descriptor)
                os.close(group_descriptor)
                events_descriptor = group_descriptor = -1
            if sorted(os.listdir(root_descriptor)) != entries:
                raise ResourceAdmissionError("offline_process_evidence_unavailable")
        except ResourceAdmissionError as error:
            if error.code.startswith("offline_process_"):
                raise
            raise ResourceAdmissionError("offline_process_evidence_unavailable") from error
        except OSError as error:
            raise ResourceAdmissionError("offline_process_evidence_unavailable") from error
        finally:
            for descriptor in (events_descriptor, group_descriptor, root_descriptor):
                if descriptor >= 0:
                    with suppress(OSError):
                        os.close(descriptor)
        return tuple(checked)


__all__ = ("OfflineProcessEvidence", "OfflineProcessEvidenceProbe")
