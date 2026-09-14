"""Duration checks complement byte integrity and full ffmpeg decoding."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path


def audio_duration(path: Path, *, expected_seconds: float | None = None) -> float:
    """Reject unknown short clips and transfers inconsistent with source duration."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        document = json.loads(result.stdout)
        duration = float(document["format"]["duration"])
        has_audio = any(stream.get("codec_type") == "audio" for stream in document["streams"])
        if not has_audio or not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        raise ValueError("audio_duration_invalid") from error
    if expected_seconds is not None:
        if not math.isfinite(expected_seconds) or expected_seconds <= 0:
            raise ValueError("audio_expected_duration_invalid")
        if abs(duration - expected_seconds) > max(3.0, expected_seconds * 0.02):
            raise ValueError("audio_duration_mismatch")
    # Even a claimed two-second song needs manual review; an HTML media duration
    # alone is not proof that a tiny file represents the requested recording.
    if duration < 10:
        raise ValueError("audio_too_short_needs_review")
    return duration
