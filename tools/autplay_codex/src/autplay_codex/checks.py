"""Shell-free, bounded validation command execution."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO, cast

from .models import CheckResult, CheckStatus
from .redaction import Redactor


class CheckRunner:
    """Run checked-in argument vectors without shell interpolation."""

    def __init__(self, repo_root: Path, timeout_seconds: int) -> None:
        self.repo_root = repo_root
        self.timeout_seconds = timeout_seconds
        self.redactor = Redactor(repo_root)

    def run(self, commands: Iterable[tuple[str, ...]]) -> list[CheckResult]:
        results: list[CheckResult] = []
        for index, command in enumerate(commands, start=1):
            result = self._run_one(index, command)
            results.append(result)
            if result.status is CheckStatus.FAILED:
                break
        return results

    def _run_one(self, index: int, command: tuple[str, ...]) -> CheckResult:
        started = time.monotonic()
        environment = dict(os.environ)
        environment["NO_COLOR"] = "1"
        name = f"{index}:{Path(command[0]).name}"
        try:
            process, windows_job = _start_isolated_process(
                command,
                repo_root=self.repo_root,
                environment=environment,
            )
            try:
                stdout = _BoundedCapture()
                stderr = _BoundedCapture()
                assert process.stdout is not None
                assert process.stderr is not None
                stdout_stream = cast(BinaryIO, process.stdout)
                stderr_stream = cast(BinaryIO, process.stderr)
                readers = (
                    _reader_thread(stdout_stream, stdout),
                    _reader_thread(stderr_stream, stderr),
                )
                timed_out = False
                try:
                    return_code = process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process_tree(process, windows_job)
                    return_code = None
                if windows_job is not None:
                    windows_job.close()
                    windows_job = None
                for reader in readers:
                    reader.join(timeout=10)
                if any(reader.is_alive() for reader in readers):
                    stdout_stream.close()
                    stderr_stream.close()
                    for reader in readers:
                        reader.join(timeout=1)
                if any(reader.is_alive() for reader in readers):
                    raise OSError("output reader did not stop after subprocess exit")
                reader_errors = [error for error in (stdout.error, stderr.error) if error]
                if reader_errors and not timed_out:
                    raise OSError("output reader failed: " + "; ".join(reader_errors))
                duration = int((time.monotonic() - started) * 1000)
                combined = "\n".join(
                    part for part in (stdout.text().strip(), stderr.text().strip()) if part
                )
                if timed_out:
                    return CheckResult(
                        name=name,
                        command=command,
                        status=CheckStatus.FAILED,
                        return_code=None,
                        duration_ms=duration,
                        details=self.redactor.text(
                            f"command exceeded {self.timeout_seconds}s timeout\n{combined}"
                        ),
                    )
                details = self.redactor.text(combined or "command produced no output")
                status = CheckStatus.PASSED if return_code == 0 else CheckStatus.FAILED
                return CheckResult(
                    name=name,
                    command=command,
                    status=status,
                    return_code=return_code,
                    duration_ms=duration,
                    details=details,
                )
            finally:
                if windows_job is not None:
                    windows_job.close()
        except OSError as exc:
            duration = int((time.monotonic() - started) * 1000)
            return CheckResult(
                name=name,
                command=command,
                status=CheckStatus.FAILED,
                return_code=None,
                duration_ms=duration,
                details=self.redactor.text(f"cannot start command: {exc}"),
            )


def checks_passed(results: list[CheckResult]) -> bool:
    """Require at least one command and no failed/skipped mandatory result."""

    return bool(results) and all(result.status is CheckStatus.PASSED for result in results)


class _BoundedCapture:
    """Retain bounded head/tail bytes while draining a subprocess pipe."""

    def __init__(self, limit: int = 3_500) -> None:
        self.limit = limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.error: str | None = None

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        head_limit = self.limit // 2
        if len(self.head) < head_limit:
            take = min(head_limit - len(self.head), len(chunk))
            self.head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            self.tail.extend(chunk)
            tail_limit = self.limit - head_limit
            if len(self.tail) > tail_limit:
                del self.tail[: len(self.tail) - tail_limit]

    def text(self) -> str:
        marker = b"\n... <capture truncated> ...\n" if self.total > self.limit else b""
        return bytes(self.head + marker + self.tail).decode("utf-8", errors="replace")


def _reader_thread(stream: BinaryIO, capture: _BoundedCapture) -> threading.Thread:
    def drain() -> None:
        try:
            with stream:
                while chunk := stream.read(8_192):
                    capture.add(chunk)
        except (OSError, ValueError) as exc:
            capture.error = str(exc)

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return thread


def _start_isolated_process(
    command: tuple[str, ...],
    *,
    repo_root: Path,
    environment: dict[str, str],
) -> tuple[subprocess.Popen[bytes], _WindowsJob | None]:
    if os.name != "nt":
        return (
            subprocess.Popen(
                list(command),
                cwd=repo_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            ),
            None,
        )

    job = _WindowsJob()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "autplay_codex.process_guard", *command],
            cwd=repo_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        job.assign(process.pid)
        assert process.stdin is not None
        process.stdin.write(b"\0")
        process.stdin.close()
        return process, job
    except BaseException:
        job.close()
        if process is not None:
            with suppress(OSError):
                process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=5)
        raise


def _terminate_process_tree(
    process: subprocess.Popen[bytes], windows_job: _WindowsJob | None
) -> None:
    """Stop the isolated subprocess group, then force the whole tree if needed."""

    if os.name == "nt":
        with suppress(OSError):
            process.send_signal(signal.CTRL_BREAK_EVENT)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if windows_job is None:
            raise OSError("Windows process tree has no owning job object")
        windows_job.terminate()
    else:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        with suppress(OSError):
            os.killpg(  # type: ignore[attr-defined]
                process.pid,
                signal.SIGKILL,  # type: ignore[attr-defined]
            )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", ctypes.c_ulong),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_ulong),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_ulong),
        ("scheduling_class", ctypes.c_ulong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _BasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class _WindowsJob:
    """Own a Win32 job whose complete process tree dies on termination/close."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _PROCESS_ASSIGN_ACCESS = 0x0001 | 0x0100

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows job objects are unavailable on this platform")
        loader = ctypes.WinDLL
        self._api: Any = loader("kernel32", use_last_error=True)
        self._handle: int | None = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = _ExtendedLimitInformation()
        limits.basic_limit_information.limit_flags = self._KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(
            self._handle,
            self._EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process_id: int) -> None:
        if self._handle is None:
            raise OSError("cannot assign a process to a closed job object")
        process_handle = self._api.OpenProcess(self._PROCESS_ASSIGN_ACCESS, False, process_id)
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not self._api.AssignProcessToJobObject(self._handle, process_handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        finally:
            self._api.CloseHandle(process_handle)

    def terminate(self) -> None:
        if self._handle is not None and not self._api.TerminateJobObject(self._handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        if self._handle is not None:
            self._api.CloseHandle(self._handle)
            self._handle = None
