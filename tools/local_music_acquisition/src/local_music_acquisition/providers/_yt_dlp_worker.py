"""Private JSON worker for the bounded yt-dlp contour."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.globals import plugin_dirs
from yt_dlp.utils import DownloadError

from ..audio_validation import audio_duration
from ..matching import SEARCH_LIMIT
from ..preflight import node_status
from ..source_catalog import track_url
from ..xray import proxy_address
from ._proxy_transport import socks_transport
from .hitmo import _looks_like_audio, _publish_exclusive, _safe_filename
from .yt_dlp import candidate_matches

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


class _SilentLogger:
    def debug(self, _message: str) -> None: ...

    def info(self, _message: str) -> None: ...

    def warning(self, _message: str) -> None: ...

    def error(self, _message: str) -> None: ...


def _size_hook(max_bytes: int) -> Callable[[dict[str, object]], None]:
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


def _options(proxy_url: object = None) -> dict[str, object]:
    if proxy_url is not None:
        proxy_address(proxy_url)
    return {
        "proxy": proxy_url or "",
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
    for entry in entries[:SEARCH_LIMIT]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        extractor = entry.get("ie_key") or entry.get("extractor_key") or entry.get("extractor")
        if not isinstance(video_id, str) or _VIDEO_ID.fullmatch(video_id) is None:
            continue
        if not isinstance(extractor, str) or extractor.casefold() != "youtube":
            continue
        if candidate_matches(entry, artist=artist, title=title):
            return entry
    return None


def _find_with_metadata(
    ydl: Any, entries: object, *, artist: str, title: str
) -> dict[str, object] | None:
    exact = _find_exact(entries, artist=artist, title=title)
    if exact is not None or not isinstance(entries, list):
        return exact
    hydrated = 0
    for entry in entries[:SEARCH_LIMIT]:
        if not isinstance(entry, dict) or entry.get("channel_is_verified") is not True:
            continue
        # A verified channel is a discovery hint, not sufficient download identity.
        hint = {**entry, "artist": entry.get("uploader"), "track": entry.get("title")}
        if _find_exact([hint], artist=artist, title=title) is None:
            continue
        detail = ydl.extract_info(f"https://www.youtube.com/watch?v={entry['id']}", download=False)
        hydrated += 1
        if isinstance(detail, dict) and detail.get("id") == entry.get("id"):
            # Require actual recording tags for title-only publications.
            tags = {"artist": detail.get("artist"), "track": detail.get("track")}
            if candidate_matches(tags, artist=artist, title=title):
                return _find_exact([detail], artist=artist, title=title)
        if hydrated >= 3:
            break
    return None


def _find_url(ydl: Any, url: str, *, artist: str, title: str) -> dict[str, object] | None:
    canonical = track_url(url, "yt_dlp")
    if canonical is None:
        raise ValueError("source_url_invalid")
    detail = ydl.extract_info(canonical, download=False)
    if not isinstance(detail, dict) or detail.get("id") != canonical.split("v=")[-1]:
        return None
    return _find_exact([detail], artist=artist, title=title)


def _download(request: dict[str, object]) -> dict[str, str]:
    with socks_transport(request.get("proxy_url")):
        return _download_track(request)


def _download_track(request: dict[str, object]) -> dict[str, str]:
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
    source_url = request.get("source_url")
    if "source_url" in request and track_url(source_url, "yt_dlp") is None:
        return {"status": "failed", "code": "source_url_invalid"}
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return {"status": "failed", "code": "ffmpeg_unavailable"}
    node = node_status()
    if node != "configured":
        return {"status": "failed", "code": node}

    search_options = _options(request.get("proxy_url")) | {
        "extract_flat": "in_playlist",
        "playlistend": SEARCH_LIMIT,
        "skip_download": True,
    }
    try:
        with YoutubeDL(search_options) as ydl:
            if isinstance(source_url, str):
                selected = _find_url(ydl, source_url, artist=artist, title=title)
            else:
                search = ydl.extract_info(
                    f"ytsearch{SEARCH_LIMIT}:{artist} - {title}", download=False
                )
                selected = _find_with_metadata(
                    ydl,
                    search.get("entries") if isinstance(search, dict) else None,
                    artist=artist,
                    title=title,
                )
    except DownloadError:
        return {"status": "failed", "code": "search_failed"}
    if selected is None:
        return {"status": "miss", "code": "exact_match_not_found"}
    if selected.get("has_drm"):
        return {"status": "miss", "code": "drm_protected"}

    video_id = str(selected["id"])
    expected = request.get("expected_duration_seconds") or selected.get("duration")
    output_directory = Path(output_value)
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"status": "failed", "code": "output_unavailable"}
    with tempfile.TemporaryDirectory(prefix="local-music-ytdlp-") as temporary_value:
        temporary = Path(temporary_value)
        download_options = _options(request.get("proxy_url")) | {
            "format": (
                "bestaudio[protocol=https]/bestaudio[protocol=http]/"
                "bestaudio[protocol=m3u8_native]/bestaudio[protocol=http_dash_segments]"
                if request.get("proxy_url")
                else "bestaudio/best"
            ),
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
                if source_url is not None:
                    ydl.process_ie_result(selected, download=True)
                else:
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
        try:
            audio_duration(
                source,
                expected_seconds=float(expected) if isinstance(expected, (int, float)) else None,
            )
        except ValueError as error:
            return {"status": "failed", "code": str(error)}
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
