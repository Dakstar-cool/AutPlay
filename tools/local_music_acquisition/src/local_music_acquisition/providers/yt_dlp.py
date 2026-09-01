"""Fail-closed yt-dlp contour with a private subprocess boundary."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import unicodedata
from pathlib import Path

from ..models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss

_SAFE_CODE = re.compile(r"[a-z0-9_.-]{1,100}")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Synchronously stop the worker and all descendants before returning."""

    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is None or completed.returncode != 0:
            process.kill()
            process.wait()
            raise RuntimeError("yt_dlp_process_tree_termination_failed")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            process.kill()
            process.wait()
            raise RuntimeError("yt_dlp_process_tree_termination_failed") from error
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class YtDlpProvider:
    """Search five YouTube results and acquire only a strict exact identity match."""

    name = "yt_dlp"
    requires_rights_confirmation = True

    def __init__(
        self, *, timeout_seconds: float = 300.0, max_bytes: int = 200 * 1024 * 1024
    ) -> None:
        if not 10 <= timeout_seconds <= 600:
            raise ValueError("yt_dlp_timeout_invalid")
        if not 1024 <= max_bytes <= 1024 * 1024 * 1024:
            raise ValueError("yt_dlp_max_bytes_invalid")
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        request = {
            "artist": item.artist,
            "title": item.title,
            "output_directory": str(output_directory.resolve()),
            "max_bytes": self._max_bytes,
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
        }
        environment.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "YTDLP_NO_PLUGINS": "1",
            }
        )
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "local_music_acquisition.providers._yt_dlp_worker"],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise ProviderFailure(self.name, "worker_unavailable") from error
        try:
            stdout, _stderr = process.communicate(
                json.dumps(request), timeout=self._timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            try:
                _terminate_process_tree(process)
            except RuntimeError as termination_error:
                raise ProviderFailure(
                    self.name, "process_tree_termination_failed"
                ) from termination_error
            raise ProviderFailure(self.name, "timeout") from error
        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise ProviderFailure(self.name, "worker_response_invalid") from error
        if process.returncode != 0 or not isinstance(response, dict):
            raise ProviderFailure(self.name, "worker_response_invalid")
        if response.get("status") == "miss" and response.get("code") == "exact_match_not_found":
            raise ProviderMiss(self.name, "exact_match_not_found")
        if response.get("status") != "downloaded":
            code = response.get("code")
            if not isinstance(code, str) or _SAFE_CODE.fullmatch(code) is None:
                code = "worker_failed"
            raise ProviderFailure(self.name, code)
        artifact_ref = response.get("artifact_ref")
        if (
            not isinstance(artifact_ref, str)
            or re.fullmatch(r"sha256:[0-9a-f]{12}", artifact_ref) is None
        ):
            raise ProviderFailure(self.name, "worker_response_invalid")
        return AcquiredArtifact(self.name, artifact_ref)


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    folded = re.sub(r"[^\w]+", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


_VERSION_MARKERS = re.compile(
    r"\b(live|cover|remix|remastered?|slowed|reverb|karaoke|instrumental|acoustic|edit|demo)\b",
    re.IGNORECASE,
)


def candidate_matches(candidate: dict[str, object], *, artist: str, title: str) -> bool:
    """Accept structured exact metadata or a strict ``Artist - Title`` display title."""

    candidate_artist = candidate.get("artist")
    candidate_track = candidate.get("track")
    display = candidate.get("title")
    if isinstance(candidate_artist, str) and isinstance(candidate_track, str):
        actual_artist, actual_title = candidate_artist, candidate_track
    elif isinstance(display, str) and " - " in display:
        actual_artist, actual_title = (part.strip() for part in display.split(" - ", 1))
    else:
        return False
    if _normalize(actual_artist) != _normalize(artist) or _normalize(actual_title) != _normalize(
        title
    ):
        return False
    requested_markers = {
        _normalize(value) for value in _VERSION_MARKERS.findall(f"{artist} {title}")
    }
    actual_markers = {
        _normalize(value) for value in _VERSION_MARKERS.findall(f"{actual_artist} {actual_title}")
    }
    return actual_markers == requested_markers
