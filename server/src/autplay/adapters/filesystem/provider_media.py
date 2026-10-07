"""Fixed provider helper: extraction without files, then explicit media stdout.

Run only inside the provider's retained OS tree. The caller bounds each media
write. Fragment downloaders must never run: even stdout mode can write files.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autplay.adapters.media.tools import SubprocessExecutableRunner
from autplay.domain.track_metadata import (
    MAX_SOURCE_METADATA_BYTES,
    sanitize_source_metadata,
    source_metadata_document,
)
from autplay.domain.vault import VaultError

SOURCE_METADATA_FILE = "source-metadata.json"
# The existing private protocol encodes Unicode as ASCII escapes.
PROVIDER_RESULT_BYTES = 6 * MAX_SOURCE_METADATA_BYTES + 1024


def youtube_source_metadata(info: dict[str, Any], candidate_id: str) -> dict[str, object] | None:
    """Only credits and dates actually reported for the exact downloaded track."""
    if info.get("id") != candidate_id:
        return None
    fields: dict[str, object] = {}
    for target, source in (
        ("title", "track"),
        ("artist", "artist"),
        ("album", "album"),
        ("album_artist", "album_artist"),
        ("track_number", "track_number"),
        ("disc_number", "disc_number"),
        ("genres", "genres"),
        ("mb_recording_id", "mb_recording_id"),
        ("mb_release_id", "mb_release_id"),
        ("mb_release_group_id", "mb_release_group_id"),
    ):
        value = info.get(source)
        if isinstance(value, str) and len(value) <= 500:
            fields[target] = value.strip()
        elif type(value) is int:
            fields[target] = value
        elif target == "genres" and isinstance(value, list):
            fields[target] = [
                part.strip()
                for part in value[:12]
                if isinstance(part, str) and 1 <= len(part.strip()) <= 100
            ]
    for name in ("artist", "album_artist"):
        values = info.get(name + "s")
        if (
            name not in fields
            and isinstance(values, list)
            and 1 <= len(values) <= 12
            and all(isinstance(part, str) and 1 <= len(part.strip()) <= 500 for part in values)
        ):
            joined = ", ".join(part.strip() for part in values)
            if len(joined) <= 500:
                fields[name] = joined
    for key in ("release_date", "original_release_date"):
        value = info.get(key)
        if isinstance(value, str) and len(value) <= 10:
            if re.fullmatch(r"[0-9]{8}", value):
                value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
            fields[key] = value
    if not fields.get("release_date"):
        year, timestamp = info.get("release_year"), info.get("release_timestamp")
        if type(year) is int and 1 <= year <= 9999:
            fields["release_date"] = f"{year:04d}"
        elif isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            with suppress(ValueError, OverflowError, OSError):
                fields["release_date"] = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
    external: dict[str, object] = {}
    for key in ("isrc", "native_album_id", "native_artist_id"):
        value = info.get(key)
        if isinstance(value, str) and len(value) <= 128:
            external[key] = value.strip().upper() if key == "isrc" else value.strip()
    artwork: list[dict[str, object]] = []
    image = info.get("thumbnail")
    if isinstance(image, str) and len(image) <= 2048:
        try:
            host = urlsplit(image).hostname or ""
        except ValueError:
            host = ""
        if host in {"ytimg.com", "youtube.com"} or host.endswith((".ytimg.com", ".youtube.com")):
            artwork.append({"kind": "thumbnail", "url": image, "source_id": candidate_id})
    try:
        return source_metadata_document(
            sanitize_source_metadata(
                {
                    "schema_version": 1,
                    "provider": "YOUTUBE",
                    "source_id": candidate_id,
                    "fields": fields,
                    "external_ids": external,
                    "artwork": artwork,
                }
            )
        )
    except ValueError, TypeError, OverflowError:
        return None


def write_source_metadata(info: dict[str, Any], candidate_id: str) -> None:
    """A fixed private sidecar is optional; media stdout remains byte-for-byte audio."""
    with suppress(ValueError, TypeError, OverflowError, OSError):
        evidence = youtube_source_metadata(info, candidate_id)
        if evidence is None:
            return
        payload = json.dumps(evidence, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_SOURCE_METADATA_BYTES:
            return
        with Path(SOURCE_METADATA_FILE).open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())


MEDIA_ERROR_EXIT_CODES = {
    "provider_token_configuration_invalid": 20,
    "provider_token_unavailable": 21,
    "provider_challenge_unresolved": 22,
    "provider_source_unavailable": 23,
    "provider_format_unsupported": 24,
    "provider_runtime_unsupported": 25,
    "provider_download_failed": 26,
}


class ProviderMediaError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def po_token_url(*, required: bool = False) -> str | None:
    raw = os.environ.get("AUTPLAY_MUSIC_PO_TOKEN_URL", "")
    if not raw:
        if required:
            raise ProviderMediaError("provider_token_configuration_invalid")
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise ProviderMediaError("provider_token_configuration_invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in {"music-po-token", "127.0.0.1", "::1", "localhost"}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ProviderMediaError("provider_token_configuration_invalid")
    return raw.rstrip("/")


def classify_download_error(error: BaseException) -> str:
    message = str(error).lower().replace("\u2019", "'")
    if any(
        marker in message
        for marker in (
            "connection refused",
            "connection reset",
            "network is unreachable",
            "timed out",
            "temporary failure in name resolution",
            "failed to resolve",
            "unable to fetch",
        )
    ):
        return "provider_token_unavailable"
    if any(
        marker in message
        for marker in (
            "sign in to confirm you're not a bot",
            "http error 403",
            "po token",
        )
    ):
        return "provider_challenge_unresolved"
    if any(
        marker in message
        for marker in (
            "video unavailable",
            "private video",
            "has been removed",
            "not available in your country",
        )
    ):
        return "provider_source_unavailable"
    if any(
        marker in message
        for marker in ("requested format is not available", "no video formats found")
    ):
        return "provider_format_unsupported"
    return "provider_download_failed"


def options(*, require_token_provider: bool = False) -> dict[str, Any]:
    # Use the pinned parser and its one pinned PO-token plugin without loading
    # operator config, remote components, credentials, or provider commands.
    parsed = importlib.import_module("yt_dlp").parse_options(
        [
            "--ignore-config",
            "--no-remote-components",
            "--no-cache-dir",
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--quiet",
            "--no-js-runtimes",
            "--js-runtimes",
            "node",
            "--socket-timeout",
            "15",
            "--retries",
            "1",
            "--extractor-retries",
            "1",
            "--fragment-retries",
            "1",
            "--file-access-retries",
            "1",
            "--match-filters",
            "!is_live & duration<=7200",
            "--format",
            "bestaudio/best",
            "--output",
            "-",
        ]
    )
    params = dict(parsed.ydl_opts)
    # Surface extractor failures to the classifier instead of returning None.
    params["ignoreerrors"] = False
    # yt-dlp otherwise falls back to process or Windows proxy discovery. The
    # provider child receives only this explicitly configured proxy variable.
    params["proxy"] = os.environ.get("AUTPLAY_MUSIC_PROXY", "")
    token_url = po_token_url(required=require_token_provider)
    if token_url is not None:
        params["extractor_args"] = {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilhttp": {"base_url": [token_url]},
        }
    return params


def stream_format(ydl: Any, info: dict[str, Any]) -> None:
    """Download exactly one selected audio format with no downloader fallback."""
    ffmpeg = importlib.import_module("yt_dlp.downloader.external").FFmpegFD
    if (
        info.get("requested_formats")
        or info.get("has_drm")
        or info.get("is_live")
        or info.get("impersonate")
    ):
        raise ProviderMediaError("provider_format_unsupported")
    codec = str(info.get("acodec", "")).split(".")[0]
    container = {"aac": "adts", "mp4a": "adts", "opus": "ogg"}.get(codec)
    info = dict(info, to_stdout=True)
    if container is None:
        raise ProviderMediaError("provider_format_unsupported")
    # Avoid extractor-defined FFmpeg arguments. Only headers and URLs are data;
    # codec/container/output choices belong to this fixed helper.
    info.pop("downloader_options", None)
    params = dict(ydl.params)
    if info.get("protocol") in {"http", "https"} and not info.get("fragments"):
        # Preserve progressive bytes: remuxing can turn a truncated WebM into a
        # valid shorter Ogg even with FFmpeg -xerror. Exact HttpFD validates HTTP
        # framing and writes only stdout; it never uses fragment files. Disable
        # in-stream retries because a server ignoring Range could duplicate bytes.
        if any(str(name).lower() == "range" for name in info.get("http_headers", {})):
            raise ProviderMediaError("provider_format_unsupported")
        params.update(
            retries=0,
            continuedl=False,
            nopart=True,
            updatetime=False,
            http_chunk_size=0,
            buffersize=32768,
            noresizebuffer=True,
        )
        http = importlib.import_module("yt_dlp.downloader.http").HttpFD
        if not http(ydl, params).download("-", info)[0]:
            raise ProviderMediaError("provider_download_failed")
        return
    if not ffmpeg.can_download(info):
        raise ProviderMediaError("provider_format_unsupported")
    params["ffmpeg_location"] = validated_ffmpeg()
    params["external_downloader_args"] = {
        "ffmpeg_i": [
            "-nostdin",
            "-xerror",
            "-rw_timeout",
            "15000000",
            "-multiple_requests",
            "1",
        ],
        "ffmpeg_o": ["-vn", "-sn", "-dn", "-c:a", "copy", "-f", container],
    }
    # FFmpegFD checks compatibility but its CLI selector can fall back. Instantiate
    # this exact class instead, and never call YoutubeDL.dl/process_info here.
    result = ffmpeg(ydl, params).download("-", info)
    if not result[0]:
        raise ProviderMediaError("provider_download_failed")


def validated_ffmpeg() -> str:
    """Use the same resolved, supported binary for preflight and media execution."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise ProviderMediaError("provider_runtime_unsupported")
    executable = str(Path(executable).resolve(strict=True))
    try:
        result = SubprocessExecutableRunner().run(
            [executable, "-version"],
            timeout_seconds=5,
            max_output_bytes=16384,
        )
    except VaultError as error:
        raise ProviderMediaError("provider_runtime_unsupported") from error
    if (
        result.returncode != 0
        or re.match(rb"ffmpeg version 8\.1\.2(?:[- ]|\r?\n)", result.stdout) is None
    ):
        raise ProviderMediaError("provider_runtime_unsupported")
    return executable


def main() -> int:
    if len(sys.argv) != 2 or re.fullmatch(r"[A-Za-z0-9_-]{11}", sys.argv[1]) is None:
        return 2
    ytdlp = importlib.import_module("yt_dlp")
    try:
        with ytdlp.YoutubeDL(options(require_token_provider=True)) as ydl:
            info = ydl.extract_info(
                "https://www.youtube.com/watch?v=" + sys.argv[1], download=False
            )
            if not isinstance(info, dict) or info.get("_type", "video") != "video":
                return 2
            selected = info.get("requested_downloads") or [info]
            if len(selected) != 1:
                return 2
            stream_format(ydl, selected[0])
            write_source_metadata(info, sys.argv[1])
        return 0
    except ProviderMediaError as error:
        return MEDIA_ERROR_EXIT_CODES[error.code]
    except ytdlp.utils.DownloadError as error:
        return MEDIA_ERROR_EXIT_CODES[classify_download_error(error)]
    except ValueError, OSError:
        return MEDIA_ERROR_EXIT_CODES["provider_download_failed"]


if __name__ == "__main__":
    raise SystemExit(main())
