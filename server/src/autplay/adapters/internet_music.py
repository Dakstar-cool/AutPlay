"""Bounded browser-free music lookup and exact selected-ID acquisition."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class InternetMusicProvider:
    """Provider ranking is preserved; search never downloads media."""

    def _command(self) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-playlist",
            "--no-warnings",
            "--socket-timeout",
            "15",
            "--retries",
            "1",
            "--extractor-retries",
            "1",
            "--no-progress",
            "--js-runtimes",
            "node",
        ]
        proxy = os.environ.get("AUTPLAY_MUSIC_PROXY")
        if proxy:
            command += ["--proxy", proxy]
        return command

    def search(self, query: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            [
                *self._command(),
                "--flat-playlist",
                "--skip-download",
                "--dump-single-json",
                "--",
                "ytsearch10:" + query,
            ],
            capture_output=True,
            timeout=35,
            check=True,
        )
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("music_search_response_too_large")
        document = json.loads(result.stdout)
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in document.get("entries", []):
            identity = row.get("id", "")
            duration = row.get("duration")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity) or identity in seen:
                continue
            if row.get("is_live") or duration is None or not 1 <= duration <= 7200:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "candidate_id": identity,
                    "provider": "YouTube",
                    "title": str(row.get("title") or "Audio")[:500],
                    "artist": str(row.get("uploader") or row.get("channel") or "Unknown artist")[
                        :500
                    ],
                    "duration_ms": int(duration * 1000),
                    "rank": len(candidates) + 1,
                }
            )
            if len(candidates) == 5:
                break
        return candidates

    def download(self, candidate_id: str, directory: Path) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate_id):
            raise ValueError("music_candidate_invalid")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        command = [
            *self._command(),
            "--format",
            "bestaudio/best",
            "--max-filesize",
            "2G",
            "--no-overwrites",
            "--restrict-filenames",
            "--print",
            "after_move:filepath",
            "--output",
            str(directory / "audio.%(ext)s"),
            "--",
            "https://www.youtube.com/watch?v=" + candidate_id,
        ]
        completed = subprocess.run(command, capture_output=True, timeout=240, check=True)
        if len(completed.stdout) > 16384:
            raise ValueError("music_download_response_invalid")
        path = Path(completed.stdout.decode().strip().splitlines()[-1]).resolve()
        if (
            path.parent != directory.resolve()
            or not path.is_file()
            or not 0 < path.stat().st_size <= 2 * 1024**3
        ):
            raise ValueError("music_download_invalid")
        return path
