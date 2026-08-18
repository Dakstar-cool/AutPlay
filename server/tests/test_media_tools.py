"""Media command adapters use argument vectors and reject hostile tool output."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from autplay.adapters.media.tools import (
    ChromaprintTool,
    FfmpegDecodeValidator,
    FfprobeInspector,
    SubprocessExecutableRunner,
    ValidatedMediaInspector,
)
from autplay.domain.vault import MediaToolOutputError, MediaToolTimeoutError, MediaValidationError
from autplay.ports.vault import ProcessResult


class FakeRunner:
    def __init__(self, result: ProcessResult | Exception) -> None:
        self._result = result
        self.arguments: tuple[str, ...] | None = None

    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int
    ) -> ProcessResult:
        self.arguments = tuple(arguments)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_ffprobe_extracts_audio_and_uses_argument_vector() -> None:
    runner = FakeRunner(
        ProcessResult(
            0,
            b'{"format":{"format_name":"mp3","duration":"12.5","bit_rate":"128000"},'
            b'"streams":[{"codec_type":"audio","codec_name":"mp3","sample_rate":"44100",'
            b'"channels":2,"bit_rate":"128000","bits_per_sample":"16"}]}',
            b"",
        )
    )
    metadata = FfprobeInspector("ffprobe", runner=runner).inspect(Path("safe-input.mp3"))
    assert metadata.duration_ms == 12500
    assert metadata.codec == "mp3"
    assert runner.arguments is not None
    assert runner.arguments[0] == "ffprobe"
    assert "safe-input.mp3" in runner.arguments


def test_media_tools_reject_corrupt_output_and_timeout() -> None:
    corrupt = FakeRunner(ProcessResult(0, b"not-json", b""))
    with pytest.raises(MediaToolOutputError):
        FfprobeInspector("ffprobe", runner=corrupt).inspect(Path("input"))
    timeout = FakeRunner(MediaToolTimeoutError())
    with pytest.raises(MediaToolTimeoutError):
        ChromaprintTool("fpcalc", algorithm_version="1", runner=timeout).fingerprint(Path("input"))


def test_fpcalc_evidence_is_bounded_and_non_identity() -> None:
    runner = FakeRunner(ProcessResult(0, b'{"duration":10,"fingerprint":"1,2,3"}', b""))
    evidence = ChromaprintTool("fpcalc", algorithm_version="1.6", runner=runner).fingerprint(
        Path("input")
    )
    assert evidence.algorithm == "chromaprint"
    assert evidence.duration_ms == 10000
    assert evidence.payload == b"1,2,3"
    assert runner.arguments is not None
    assert runner.arguments[:2] == ("fpcalc", "-json")


def test_nonzero_tool_exit_is_sanitized() -> None:
    runner = FakeRunner(ProcessResult(1, b"private path and token", b"secret"))
    with pytest.raises(MediaValidationError) as failure:
        FfprobeInspector("ffprobe", runner=runner).inspect(Path("input"))
    assert str(failure.value) == "media_validation_failed"


def test_ffmpeg_decode_validation_is_a_separate_full_decode_gate() -> None:
    runner = FakeRunner(ProcessResult(0, b"", b""))
    FfmpegDecodeValidator("ffmpeg", runner=runner).validate(Path("input"))
    assert runner.arguments is not None
    assert runner.arguments[:3] == ("ffmpeg", "-v", "error")
    assert runner.arguments[-3:] == ("-f", "null", "-")


def test_validated_inspector_requires_decode_before_probe() -> None:
    calls: list[str] = []

    class Validator:
        def validate(self, path: Path) -> None:
            del path
            calls.append("decode")

    class Inspector:
        def inspect(self, path: Path):  # type: ignore[no-untyped-def]
            calls.append("probe")
            return FfprobeInspector(
                "ffprobe",
                runner=FakeRunner(
                    ProcessResult(
                        0,
                        b'{"format":{"format_name":"flac","duration":"1"},'
                        b'"streams":[{"codec_type":"audio","codec_name":"flac",'
                        b'"sample_rate":"44100","channels":2}]}',
                        b"",
                    )
                ),
            ).inspect(path)

    metadata = ValidatedMediaInspector(Validator(), Inspector()).inspect(Path("input"))
    assert metadata.codec == "flac"
    assert calls == ["decode", "probe"]


def test_ffprobe_rejects_codec_container_outside_allowlist() -> None:
    runner = FakeRunner(
        ProcessResult(
            0,
            b'{"format":{"format_name":"avi","duration":"1"},'
            b'"streams":[{"codec_type":"audio","codec_name":"pcm_s16le",'
            b'"sample_rate":"44100","channels":2}]}',
            b"",
        )
    )
    with pytest.raises(MediaValidationError):
        FfprobeInspector("ffprobe", runner=runner).inspect(Path("input"))


def test_subprocess_runner_enforces_output_bound_without_unbounded_capture() -> None:
    runner = SubprocessExecutableRunner()
    with pytest.raises(MediaToolOutputError):
        runner.run(
            (sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"),
            timeout_seconds=5,
            max_output_bytes=1024,
        )


def test_subprocess_runner_terminates_timeout() -> None:
    runner = SubprocessExecutableRunner()
    with pytest.raises(MediaToolTimeoutError):
        runner.run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            timeout_seconds=0.1,
            max_output_bytes=1024,
        )
