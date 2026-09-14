from __future__ import annotations

import wave
from pathlib import Path

import pytest

from local_music_acquisition.audio_validation import audio_duration


def _audio(path: Path, seconds: int) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000 * seconds)
    return path


def test_decodable_two_second_fragment_is_not_a_completed_song(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="audio_too_short_needs_review"):
        audio_duration(_audio(tmp_path / "clip.wav", 2))


def test_duration_must_match_source_even_if_fragment_decodes(tmp_path: Path) -> None:
    path = _audio(tmp_path / "song.wav", 12)
    assert audio_duration(path, expected_seconds=12) == 12
    with pytest.raises(ValueError, match="audio_duration_mismatch"):
        audio_duration(path, expected_seconds=233)
