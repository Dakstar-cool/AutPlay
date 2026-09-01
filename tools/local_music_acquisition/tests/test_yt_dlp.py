from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from local_music_acquisition.models import PlaylistItem, ProviderFailure, ProviderMiss
from local_music_acquisition.providers._yt_dlp_worker import _find_exact
from local_music_acquisition.providers.yt_dlp import (
    YtDlpProvider,
    _terminate_process_tree,
    candidate_matches,
)


def test_candidate_match_accepts_only_exact_artist_and_title() -> None:
    assert candidate_matches({"title": "Artist - Song"}, artist="Artist", title="Song")
    assert not candidate_matches({"title": "Artist - Song (live)"}, artist="Artist", title="Song")
    assert not candidate_matches({"title": "Other - Song"}, artist="Artist", title="Song")


def test_worker_accepts_only_bounded_youtube_video_identity() -> None:
    entries = [
        {"id": "abcdefghijk", "extractor_key": "Generic", "title": "Artist - Song"},
        {"id": "too-short", "extractor_key": "Youtube", "title": "Artist - Song"},
        {"id": "12345678901", "extractor_key": "Youtube", "title": "Artist - Song"},
    ]

    assert _find_exact(entries, artist="Artist", title="Song") == entries[2]


def test_provider_maps_exact_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = _FakeProcess(json.dumps({"status": "miss", "code": "exact_match_not_found"}))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ProviderMiss, match=r"yt_dlp\.exact_match_not_found"):
        YtDlpProvider().acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)


def test_provider_maps_worker_failure_without_leaking_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _FakeProcess(
        json.dumps({"status": "failed", "code": "download_failed"}),
        returncode=1,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ProviderFailure, match=r"yt_dlp\.worker_response_invalid"):
        YtDlpProvider().acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)


class _FakeProcess:
    def __init__(self, stdout: str, *, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.pid = 123
        self.stdin = io.StringIO()
        self.killed = False
        self.waited = False

    def communicate(self, _input: str, timeout: float) -> tuple[str, str]:
        del timeout
        return self.stdout, ""

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waited = True
        return self.returncode


def test_windows_timeout_kills_descendant_tree_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess("")
    calls: list[list[str]] = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_options: (
            calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )

    _terminate_process_tree(process)  # type: ignore[arg-type]

    assert calls == [["taskkill.exe", "/PID", "123", "/T", "/F"]]
    assert process.waited is True


def test_windows_tree_kill_failure_aborts_instead_of_advancing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess("")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_options: subprocess.CompletedProcess(command, 1, "", "denied"),
    )

    with pytest.raises(RuntimeError, match="yt_dlp_process_tree_termination_failed"):
        _terminate_process_tree(process)  # type: ignore[arg-type]

    assert process.killed is True
    assert process.waited is True


def test_provider_timeout_terminates_tree_before_reporting_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _FakeProcess("")

    def timeout(_input: str, timeout: float) -> tuple[str, str]:
        raise subprocess.TimeoutExpired("worker", timeout)

    process.communicate = timeout  # type: ignore[method-assign]
    terminated: list[int] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "local_music_acquisition.providers.yt_dlp._terminate_process_tree",
        lambda target: terminated.append(target.pid),
    )

    with pytest.raises(ProviderFailure, match=r"yt_dlp\.timeout"):
        YtDlpProvider(timeout_seconds=10).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)

    assert terminated == [123]


def test_provider_maps_timeout_tree_termination_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _FakeProcess("")

    def timeout(_input: str, timeout: float) -> tuple[str, str]:
        raise subprocess.TimeoutExpired("worker", timeout)

    process.communicate = timeout  # type: ignore[method-assign]
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    def fail_termination(_process: _FakeProcess) -> None:
        raise RuntimeError("yt_dlp_process_tree_termination_failed")

    monkeypatch.setattr(
        "local_music_acquisition.providers.yt_dlp._terminate_process_tree",
        fail_termination,
    )

    with pytest.raises(
        ProviderFailure, match=r"yt_dlp\.process_tree_termination_failed"
    ):
        YtDlpProvider(timeout_seconds=10).acquire(
            PlaylistItem(1, "Artist", "Title"), tmp_path
        )
