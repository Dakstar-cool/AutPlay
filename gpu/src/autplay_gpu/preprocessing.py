"""Bounded deterministic FFmpeg preprocessing for verified Vault sources."""

from __future__ import annotations

import math
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from autplay.domain.enrichment import (
    MAX_AUDIO_SEGMENTS,
    MAX_SEGMENT_BYTES,
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
    EmbeddingJobTarget,
)

MAX_FFMPEG_ERROR_BYTES: Final = 16 * 1024


class AudioPreprocessingError(RuntimeError):
    """A verified source could not produce bounded deterministic PCM."""


@dataclass(frozen=True, slots=True)
class VerifiedAudioSource:
    """A server-resolved immutable Vault object, never a job-supplied path."""

    path: Path
    duration_ms: int
    byte_size: int
    sha256: bytes

    def __post_init__(self) -> None:
        if not self.path.is_absolute() or self.duration_ms < 1 or self.byte_size < 1:
            raise ValueError("verified audio source metadata is invalid")
        if len(self.sha256) != 32:
            raise ValueError("verified audio source hash must be SHA-256")


class VerifiedAudioSourceResolver(Protocol):
    """Resolve a typed DB target to an already-authorized immutable local object."""

    def resolve(self, target: EmbeddingJobTarget) -> VerifiedAudioSource: ...


type FfmpegRunner = Callable[[Sequence[str], float, int], bytes]


class FfmpegSegmentPreprocessor:
    """Decode evenly distributed mono float32 segments with strict resource bounds."""

    def __init__(
        self,
        resolver: VerifiedAudioSourceResolver,
        *,
        executable: str | None = None,
        maximum_segments: int = 32,
        timeout_seconds: float = 120.0,
        runner: FfmpegRunner | None = None,
    ) -> None:
        if not 1 <= maximum_segments <= MAX_AUDIO_SEGMENTS:
            raise ValueError("maximum_segments is invalid")
        if not 1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds is invalid")
        self._resolver = resolver
        self._executable = executable or shutil.which("ffmpeg") or "ffmpeg"
        self._maximum_segments = maximum_segments
        self._timeout_seconds = timeout_seconds
        self._runner = runner or _run_ffmpeg

    def decode(
        self, target: EmbeddingJobTarget, model: ApprovedEmbeddingModel
    ) -> tuple[DecodedAudioSegment, ...]:
        """Resolve and decode a deterministic segment plan for one model manifest."""

        source = self._resolver.resolve(target)
        if not source.path.is_file():
            raise AudioPreprocessingError("verified Vault source is unavailable")
        starts = _segment_starts(
            duration_ms=source.duration_ms,
            segment_duration_ms=model.segment_duration_ms,
            maximum_segments=self._maximum_segments,
        )
        segments: list[DecodedAudioSegment] = []
        for index, start_ms in enumerate(starts):
            duration_ms = min(model.segment_duration_ms, source.duration_ms - start_ms)
            expected_bytes = math.ceil(duration_ms * model.input_sample_rate_hz / 1000) * 4
            if expected_bytes < 4 or expected_bytes > MAX_SEGMENT_BYTES:
                raise AudioPreprocessingError("decoded segment exceeds the model resource bound")
            command = (
                self._executable,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_ms / 1000:.3f}",
                "-t",
                f"{duration_ms / 1000:.3f}",
                "-i",
                str(source.path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(model.input_sample_rate_hz),
                "-acodec",
                "pcm_f32le",
                "-f",
                "f32le",
                "pipe:1",
            )
            pcm = self._runner(command, self._timeout_seconds, expected_bytes)
            if not pcm or len(pcm) % 4 or len(pcm) > expected_bytes:
                raise AudioPreprocessingError("FFmpeg returned an invalid PCM segment")
            segments.append(DecodedAudioSegment(index=index, start_ms=start_ms, pcm_f32le=pcm))
        return tuple(segments)


def _segment_starts(
    *, duration_ms: int, segment_duration_ms: int, maximum_segments: int
) -> tuple[int, ...]:
    if duration_ms < 1 or segment_duration_ms < 1:
        raise ValueError("audio duration is invalid")
    count = min(maximum_segments, max(1, math.ceil(duration_ms / segment_duration_ms)))
    last_start = max(0, duration_ms - segment_duration_ms)
    if count == 1:
        return (0,)
    return tuple(round(index * last_start / (count - 1)) for index in range(count))


def _run_ffmpeg(command: Sequence[str], timeout_seconds: float, maximum_output: int) -> bytes:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        raise AudioPreprocessingError("bounded FFmpeg decoding failed") from error
    if len(completed.stderr) > MAX_FFMPEG_ERROR_BYTES:
        raise AudioPreprocessingError("FFmpeg diagnostic output exceeded its bound")
    if len(completed.stdout) > maximum_output:
        raise AudioPreprocessingError("FFmpeg PCM output exceeded its bound")
    return completed.stdout


__all__ = (
    "AudioPreprocessingError",
    "FfmpegSegmentPreprocessor",
    "VerifiedAudioSource",
    "VerifiedAudioSourceResolver",
)
