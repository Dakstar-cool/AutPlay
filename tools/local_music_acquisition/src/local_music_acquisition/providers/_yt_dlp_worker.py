"""Private JSON worker for the bounded yt-dlp contour."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.globals import plugin_dirs
from yt_dlp.utils import DownloadError

from .hitmo import _looks_like_audio, _publish_exclusive, _safe_filename
from .yt_dlp import candidate_matches

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


class _SilentLogger:
    def debug(self, _message: str) -> None: ...

    def info(self, _message: str) -> None: ...

    def warning(self, _message: str) -> None: ...

    def error(self, _message: str) -> None: ...


def _size_hook(max_bytes: int):
    def check(progress: dict[str, object]) -> None:
        for name in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
            value = progress.get(name)
            if isinstance(value, int) and value > max_bytes:
                raise DownloadError("download_too_large")

    return check


def _probe_audio(path: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        document = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    streams = document.get("streams") if isinstance(document, dict) else None
    return (
        completed.returncode == 0
        and isinstance(streams, list)
        and any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
        )
    )


def _content_ref(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()[:12]}"


def _options() -> dict[str, object]:
    return {
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "logger": _SilentLogger(),
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "noplaylist": True,
        "cachedir": False,
        "cookiefile": None,
        "usenetrc": False,
        "username": None,
        "password": None,
        "js_runtimes": {"node": {}},
        "remote_components": set(),
    }


def _find_exact(entries: object, *, artist: str, title: str) -> dict[str, object] | None:
    if not isinstance(entries, list):
        return None
    for entry in entries[:5]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        extractor = entry.get("extractor_key") or entry.get("extractor")
        if not isinstance(video_id, str) or _VIDEO_ID.fullmatch(video_id) is None:
            continue
        if not isinstance(extractor, str) or not extractor.casefold().startswith("youtube"):
            continue
        if candidate_matches(entry, artist=artist, title=title):
            return entry
    return None


def _download(request: dict[str, object]) -> dict[str, str]:
    artist = request.get("artist")
    title = request.get("title")
    output_value = request.get("output_directory")
    max_bytes = request.get("max_bytes")
    if (
        not isinstance(artist, str)
        or not artist.strip()
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(output_value, str)
        or not isinstance(max_bytes, int)
        or not 1024 <= max_bytes <= 1024 * 1024 * 1024
    ):
        return {"status": "failed", "code": "request_invalid"}
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return {"status": "failed", "code": "ffmpeg_unavailable"}

    search_options = _options() | {
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "skip_download": True,
    }
    try:
        with YoutubeDL(search_options) as ydl:
            search = ydl.extract_info(f"ytsearch5:{artist} - {title}", download=False)
    except DownloadError:
        return {"status": "failed", "code": "search_failed"}
    selected = _find_exact(
        search.get("entries") if isinstance(search, dict) else None, artist=artist, title=title
    )
    if selected is None:
        return {"status": "miss", "code": "exact_match_not_found"}

    video_id = str(selected["id"])
    output_directory = Path(output_value)
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"status": "failed", "code": "output_unavailable"}
    with tempfile.TemporaryDirectory(prefix="local-music-ytdlp-") as temporary_value:
        temporary = Path(temporary_value)
        download_options = _options() | {
            "format": "bestaudio/best",
            "outtmpl": str(temporary / "%(id)s.%(ext)s"),
            "max_filesize": max_bytes,
            "overwrites": False,
            "continuedl": False,
            "progress_hooks": [_size_hook(max_bytes)],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            ],
        }
        try:
            with YoutubeDL(download_options) as ydl:
                ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        except DownloadError:
            return {"status": "failed", "code": "download_failed"}
        candidates = [
            path for path in temporary.iterdir() if path.is_file() and path.suffix == ".mp3"
        ]
        if len(candidates) != 1:
            return {"status": "failed", "code": "download_result_invalid"}
        source = candidates[0]
        try:
            size = source.stat().st_size
        except OSError:
            return {"status": "failed", "code": "download_result_invalid"}
        if not 0 < size <= max_bytes or not _looks_like_audio(source) or not _probe_audio(source):
            return {"status": "failed", "code": "downloaded_content_invalid"}
        name = _safe_filename(f"{artist} - {title}.mp3")
        try:
            published = _publish_exclusive(source, output_directory, name)
            artifact_ref = _content_ref(published)
        except OSError:
            return {"status": "failed", "code": "publish_failed"}
    return {"status": "downloaded", "artifact_ref": artifact_ref}


def main() -> int:
    # The Python API otherwise loads plugins from default user and system locations.
    plugin_dirs.value = []
    try:
        raw = sys.stdin.read(32 * 1024 + 1)
        if len(raw) > 32 * 1024:
            raise ValueError
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        response = {"status": "failed", "code": "request_invalid"}
    else:
        response = _download(request)
    sys.stdout.write(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
