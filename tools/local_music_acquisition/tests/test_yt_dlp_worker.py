from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from local_music_acquisition.providers._yt_dlp_worker import _probe_audio, _size_hook


def test_size_hook_stops_unknown_length_download_at_limit() -> None:
    hook = _size_hook(1024)

    hook({"downloaded_bytes": 1024})
    with pytest.raises(DownloadError, match="download_too_large"):
        hook({"downloaded_bytes": 1025})


def test_ffprobe_validation_requires_audio_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = subprocess.CompletedProcess(
        [], 0, json.dumps({"streams": [{"codec_type": "video"}]}), ""
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    assert not _probe_audio(tmp_path / "candidate.mp3")
