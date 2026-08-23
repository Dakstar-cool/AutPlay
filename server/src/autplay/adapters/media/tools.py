"""Bounded ffprobe and fpcalc adapters with sanitized stable failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from io import BufferedReader
from pathlib import Path
from time import monotonic
from typing import Any, cast

from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    MediaToolOutputError,
    MediaToolTimeoutError,
    MediaValidationError,
)
from autplay.ports.vault import (
    ExecutableRunner,
    MediaDecodeValidator,
    MediaInspector,
    ProcessResult,
)

if sys.platform == "win32":
    from subprocess import CREATE_NEW_PROCESS_GROUP as _CREATE_NEW_PROCESS_GROUP
else:
    _CREATE_NEW_PROCESS_GROUP = 0


class SubprocessExecutableRunner:
    """Execute an exact argument vector without a shell or inherited input."""

    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int
    ) -> ProcessResult:
        """Run one bounded process and discard untrusted diagnostic detail on failure."""

        if not arguments or timeout_seconds <= 0 or max_output_bytes < 1:
            raise ValueError("invalid executable runner limits")
        try:
            process = subprocess.Popen(
                list(arguments),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_minimal_environment(),
                start_new_session=os.name != "nt",
                creationflags=_CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as error:
            raise MediaValidationError() from error
        if process.stdout is None or process.stderr is None:
            _terminate_process(process)
            raise MediaValidationError()

        stdout = bytearray()
        stderr = bytearray()
        output_total = [0]
        output_lock = threading.Lock()
        output_exceeded = threading.Event()
        readers = (
            threading.Thread(
                target=_read_bounded_output,
                args=(process.stdout, stdout),
                kwargs={
                    "maximum": max_output_bytes,
                    "total": output_total,
                    "lock": output_lock,
                    "exceeded": output_exceeded,
                },
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded_output,
                args=(process.stderr, stderr),
                kwargs={
                    "maximum": max_output_bytes,
                    "total": output_total,
                    "lock": output_lock,
                    "exceeded": output_exceeded,
                },
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        deadline = monotonic() + timeout_seconds
        timed_out = False
        while process.poll() is None:
            if output_exceeded.is_set():
                _terminate_process(process)
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process(process)
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
        for reader in readers:
            reader.join(timeout=2.0)
        if any(reader.is_alive() for reader in readers):
            _terminate_process(process)
            raise MediaValidationError()
        if timed_out:
            raise MediaToolTimeoutError()
        if output_exceeded.is_set():
            raise MediaToolOutputError()
        return ProcessResult(
            returncode=process.returncode,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )


def _minimal_environment() -> dict[str, str]:
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": os.environ.get("PATH", "")}
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if value := os.environ.get(name):
                environment[name] = value
    return environment


def _read_bounded_output(
    stream: BufferedReader,
    target: bytearray,
    *,
    maximum: int,
    total: list[int],
    lock: threading.Lock,
    exceeded: threading.Event,
) -> None:
    try:
        while not exceeded.is_set() and (chunk := stream.read(8192)):
            with lock:
                remaining = max(0, maximum - total[0])
                target.extend(chunk[: remaining + 1])
                total[0] += len(chunk)
                if total[0] > maximum:
                    exceeded.set()
    finally:
        stream.close()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.kill(-process.pid, 9)
        else:
            process.kill()
        process.wait(timeout=5.0)
    except OSError, subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class FfprobeInspector:
    """Validate decoded audio and normalize only bounded ffprobe JSON fields."""

    def __init__(
        self,
        executable: str,
        *,
        runner: ExecutableRunner | None = None,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        if not executable or timeout_seconds <= 0 or max_output_bytes < 1:
            raise ValueError("invalid ffprobe configuration")
        self._executable = executable
        self._runner = runner or SubprocessExecutableRunner()
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        """Run ffprobe with explicit JSON output and reject malformed/non-audio results."""

        result = self._runner.run(
            (
                self._executable,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,bit_rate:stream=codec_type,codec_name,sample_rate,channels,bit_rate,bits_per_raw_sample,bits_per_sample",
                "-of",
                "json",
                "-i",
                str(path),
            ),
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )
        if result.returncode != 0:
            raise MediaValidationError()
        document = _parse_document(result.stdout)
        streams = document.get("streams")
        if not isinstance(streams, list):
            raise MediaToolOutputError()
        audio_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ),
            None,
        )
        if not isinstance(audio_stream, dict):
            raise MediaValidationError()
        format_data = document.get("format")
        if not isinstance(format_data, dict):
            raise MediaToolOutputError()
        codec = _bounded_text(audio_stream.get("codec_name"), 100).lower()
        container = _normalize_container(codec, _bounded_text(format_data.get("format_name"), 100))
        sample_rate_hz = _positive_int(audio_stream.get("sample_rate"))
        channels = _positive_int(audio_stream.get("channels"), upper=64)
        duration_ms = _duration_millis(format_data.get("duration"))
        bitrate_bps = _optional_positive_int(audio_stream.get("bit_rate"))
        if bitrate_bps is None:
            bitrate_bps = _optional_positive_int(format_data.get("bit_rate"))
        bit_depth = _optional_positive_int(audio_stream.get("bits_per_raw_sample"), upper=64)
        if bit_depth is None:
            bit_depth = _optional_positive_int(audio_stream.get("bits_per_sample"), upper=64)
        return AudioTechnicalMetadata(
            codec=codec,
            container=container,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            duration_ms=duration_ms,
            bitrate_bps=bitrate_bps,
            bit_depth=bit_depth,
        )


class FfmpegDecodeValidator:
    """Bounded full-decode gate kept separate from metadata probing."""

    def __init__(
        self,
        executable: str,
        *,
        runner: ExecutableRunner | None = None,
        timeout_seconds: float = 120.0,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        if not executable or timeout_seconds <= 0 or max_output_bytes < 1:
            raise ValueError("invalid ffmpeg configuration")
        self._executable = executable
        self._runner = runner or SubprocessExecutableRunner()
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def validate(self, path: Path) -> None:
        """Decode all input audio to a null sink without shell interpolation."""

        result = self._runner.run(
            (
                self._executable,
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ),
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )
        if result.returncode != 0:
            raise MediaValidationError()


class ValidatedMediaInspector:
    """Require a successful full decode before accepting ffprobe metadata."""

    def __init__(self, validator: MediaDecodeValidator, inspector: MediaInspector) -> None:
        self._validator = validator
        self._inspector = inspector

    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        """Decode the complete stream, then return bounded technical metadata."""

        self._validator.validate(path)
        return self._inspector.inspect(path)


class ChromaprintTool:
    """Generate fingerprint payload evidence with explicit algorithm/version provenance."""

    def __init__(
        self,
        executable: str,
        *,
        algorithm_version: str,
        runner: ExecutableRunner | None = None,
        timeout_seconds: float = 120.0,
        max_output_bytes: int = 1024 * 1024,
        max_duration_seconds: int = 3600,
    ) -> None:
        if (
            not executable
            or not algorithm_version
            or timeout_seconds <= 0
            or max_output_bytes < 1
            or max_duration_seconds < 1
        ):
            raise ValueError("invalid fpcalc configuration")
        self._executable = executable
        self._algorithm_version = algorithm_version
        self._runner = runner or SubprocessExecutableRunner()
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._max_duration_seconds = max_duration_seconds

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        """Run fpcalc JSON mode and return an opaque bounded payload, not identity."""

        result = self._runner.run(
            (
                self._executable,
                "-json",
                "-length",
                str(self._max_duration_seconds),
                str(path),
            ),
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )
        if result.returncode != 0:
            raise MediaValidationError()
        document = _parse_document(result.stdout)
        duration_seconds = _positive_int(document.get("duration"), upper=self._max_duration_seconds)
        fingerprint = _bounded_text(document.get("fingerprint"), self._max_output_bytes)
        payload = fingerprint.encode("ascii")
        return ChromaprintEvidence(
            algorithm="chromaprint",
            algorithm_version=self._algorithm_version,
            duration_ms=duration_seconds * 1000,
            payload=payload,
        )


def _parse_document(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise MediaToolOutputError()
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MediaToolOutputError() from error
    if not isinstance(decoded, dict):
        raise MediaToolOutputError()
    return cast(dict[str, Any], decoded)


def _bounded_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise MediaToolOutputError()
    if "\x00" in value:
        raise MediaToolOutputError()
    return value


def _positive_int(value: object, *, upper: int | None = None) -> int:
    try:
        parsed = int(cast(str | int, value))
    except (TypeError, ValueError) as error:
        raise MediaToolOutputError() from error
    if parsed < 1 or (upper is not None and parsed > upper):
        raise MediaToolOutputError()
    return parsed


def _optional_positive_int(value: object, *, upper: int | None = None) -> int | None:
    # FFmpeg uses zero for optional codec parameters whose value is unknown,
    # including MP3 bits_per_sample. Keep malformed and negative values fail-closed.
    if value is None or value == "N/A" or value == 0 or value == "0":
        return None
    return _positive_int(value, upper=upper)


def _duration_millis(value: object) -> int:
    if not isinstance(value, str):
        raise MediaToolOutputError()
    try:
        duration = float(value)
    except ValueError as error:
        raise MediaToolOutputError() from error
    millis = int(duration * 1000)
    if duration <= 0 or millis < 1 or millis > 24 * 60 * 60 * 1000:
        raise MediaToolOutputError()
    return millis


def _normalize_container(codec: str, raw_container: str) -> str:
    allowed_by_codec = {
        "aac": frozenset({"aac", "adts", "mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
        "flac": frozenset({"flac", "ogg"}),
        "mp3": frozenset({"mp3"}),
        "opus": frozenset({"ogg", "matroska", "webm"}),
    }
    allowed = allowed_by_codec.get(codec)
    if allowed is None:
        raise MediaValidationError()
    observed = [item.strip().lower() for item in raw_container.split(",")]
    selected = next((item for item in observed if item in allowed), None)
    if selected is None:
        raise MediaValidationError()
    return selected


__all__ = (
    "ChromaprintTool",
    "FfmpegDecodeValidator",
    "FfprobeInspector",
    "SubprocessExecutableRunner",
    "ValidatedMediaInspector",
)
