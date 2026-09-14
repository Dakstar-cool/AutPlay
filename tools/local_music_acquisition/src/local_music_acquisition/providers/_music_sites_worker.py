"""Private, bounded SoundCloud/Bandcamp search and file acquisition worker."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from yt_dlp import YoutubeDL
from yt_dlp.extractor.bandcamp import BandcampIE
from yt_dlp.extractor.soundcloud import SoundcloudIE, SoundcloudSearchIE
from yt_dlp.globals import plugin_dirs
from yt_dlp.utils import DownloadError

from ..audio_validation import audio_duration
from ..matching import SEARCH_LIMIT, candidate_matches
from ..source_catalog import track_url as _track_url
from ..source_client import valid_client_id
from ._proxy_transport import socks_transport
from ._yt_dlp_worker import _content_ref, _options, _probe_audio, _size_hook
from .hitmo import _looks_like_audio, _publish_exclusive, _safe_filename

_EXTRACTORS = {"soundcloud": "Soundcloud", "bandcamp": "Bandcamp"}
_AUDIO_EXTENSIONS = frozenset({"mp3", "flac", "wav", "m4a", "ogg", "opus"})
_ORIGINAL_FORMATS = {
    "mp3-320": 100,
    "mp3-v0": 90,
    "flac": 80,
    "wav": 70,
    "aac-hi": 60,
    "vorbis": 50,
    "falac": 40,
}
_METADATA_LIMIT = 3
_SEARCH_BYTES = 2 * 1024 * 1024


class _SourceError(RuntimeError):
    def __init__(self, code: str, *, miss: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.status = "miss" if miss else "failed"


def _candidate(info: object, provider: str) -> tuple[dict[str, Any], str] | None:
    if not isinstance(info, dict):
        return None
    extractor = info.get("ie_key") or info.get("extractor_key")
    if extractor != _EXTRACTORS[provider] or not re.fullmatch(r"[0-9]{1,24}", str(info.get("id"))):
        return None
    url = _track_url(info.get("webpage_url"), provider)
    return (info, url) if url else None


def _matches(info: dict[str, Any], artist: str, title: str) -> bool:
    # Publisher recording tags take precedence over uploader/channel display names.
    tags = dict(info)
    if not tags.get("artist") and tags.get("artists"):
        tags["artist"] = tags["artists"]
    return candidate_matches(tags, artist=artist, title=title)


def _bandcamp_search(artist: str, title: str, proxy_url: str | None = None) -> list[dict[str, Any]]:
    # This public search endpoint avoids interpreting the HTML search interstitial as a miss.
    with requests.Session() as session:
        session.trust_env = False
        if proxy_url:
            session.proxies.update({"http": proxy_url, "https": proxy_url})
        try:
            with session.post(
                "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic",
                json={"search_text": f"{artist} {title}", "search_filter": "t", "full_page": True},
                timeout=(10, 15),
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.status_code in {401, 403, 429}:
                    raise _SourceError("search_access_restricted")
                if response.status_code != 200:
                    raise _SourceError("search_failed")
                if "application/json" not in response.headers.get("Content-Type", ""):
                    raise _SourceError("search_response_invalid")
                payload = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    payload.extend(chunk)
                    if len(payload) > _SEARCH_BYTES:
                        raise _SourceError("search_response_too_large")
                document = json.loads(payload)
        except (requests.RequestException, ValueError) as error:
            raise _SourceError("search_failed") from error
    auto = document.get("auto") if isinstance(document, dict) else None
    results = auto.get("results") if isinstance(auto, dict) else None
    if not isinstance(results, list):
        raise _SourceError("search_response_invalid")
    entries = []
    for row in results[:SEARCH_LIMIT]:
        if not isinstance(row, dict) or row.get("type") != "t":
            continue
        entries.append(
            {
                "id": str(row.get("id")),
                "ie_key": "Bandcamp",
                "artist": row.get("band_name"),
                "track": row.get("name"),
                "webpage_url": row.get("item_url_path"),
            }
        )
    return entries


def _translate_error(error: DownloadError) -> _SourceError:
    # Inspect locally; raw extractor messages can contain signed URLs and never cross the worker.
    message = str(error).casefold()
    if "timed out" in message or "timeout" in message:
        return _SourceError("metadata_timeout")
    if "certificate" in message or "sslerror" in message:
        return _SourceError("metadata_tls_failed")
    if "drm" in message:
        return _SourceError("drm_protected", miss=True)
    if any(
        marker in message
        for marker in (
            "not available in your country",
            "geo restricted",
            "track is not available",
            "track not found",
        )
    ):
        return _SourceError("source_unavailable", miss=True)
    if any(
        marker in message
        for marker in (
            "client challenge",
            "captcha",
            "http error 403",
            "http error 429",
            "sign in",
            "log in",
        )
    ):
        return _SourceError("source_access_restricted")
    return _SourceError("metadata_failed")


def _search_error(error: DownloadError) -> _SourceError:
    message = str(error).casefold()
    for markers, code in (
        (("timed out", "timeout"), "search_timeout"),
        (("http error 503", "http error 502"), "search_service_unavailable"),
        (("http error 403", "http error 429", "captcha"), "search_access_restricted"),
        (("certificate", "sslerror"), "search_tls_failed"),
        (("client id", "client_id"), "search_client_id_unavailable"),
        (("unsupported url", "no suitable extractor"), "search_extractor_unavailable"),
        (("requested format",), "search_format_unavailable"),
    ):
        if any(marker in message for marker in markers):
            return _SourceError(code)
    return _SourceError("search_failed")


def _formats(info: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    raw_formats = info.get("formats")
    if not isinstance(raw_formats, list):
        raise _SourceError("source_unavailable", miss=True)
    available = []
    for fmt in raw_formats:
        if not isinstance(fmt, dict):
            continue
        identity = str(fmt.get("format_id", ""))
        if (
            fmt.get("has_drm")
            or info.get("has_drm")
            or "preview" in identity.casefold()
            or "preview" in str(fmt.get("format_note", "")).casefold()
            or fmt.get("vcodec") != "none"
            or fmt.get("ext") not in _AUDIO_EXTENSIONS
            or fmt.get("protocol") not in {"http", "https", "m3u8_native"}
        ):
            continue
        if provider == "bandcamp" and identity not in _ORIGINAL_FORMATS:
            continue
        url = _media_url(fmt.get("url"), provider)
        if url is None:
            continue
        fmt = dict(fmt, url=url)
        available.append(fmt)
    if not available:
        if info.get("has_drm") or any(
            isinstance(f, dict) and f.get("has_drm") for f in raw_formats
        ):
            raise _SourceError("drm_protected", miss=True)
        code = "original_download_unavailable" if provider == "bandcamp" else "preview_only"
        raise _SourceError(code, miss=True)
    if provider == "bandcamp":
        return sorted(available, key=lambda f: _ORIGINAL_FORMATS[str(f["format_id"])], reverse=True)

    # Prefer an original; otherwise direct MP3 avoids unnecessary lossy transcoding and HLS joins.
    def score(fmt: dict[str, Any]) -> tuple[bool, bool, float]:
        return (
            fmt.get("format_id") == "download",
            fmt.get("ext") == "mp3" and fmt.get("protocol") in {"http", "https"},
            float(fmt.get("abr") or 0),
        )

    return sorted(available, key=score, reverse=True)


def _media_url(value: object, provider: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        roots = (
            ("sndcdn.com", "soundcloud.com")
            if provider == "soundcloud"
            else ("bcbits.com", "bandcamp.com")
        )
        host = parsed.hostname or ""
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.fragment
            or not any(host == root or host.endswith("." + root) for root in roots)
        ):
            return None
        return parsed._replace(scheme="https").geturl()
    except ValueError:
        return None


def _select(
    ydl: Any, provider: str, artist: str, title: str, proxy_url: str | None = None
) -> dict[str, Any]:
    entries: Any
    if provider == "bandcamp":
        entries = (
            _bandcamp_search(artist, title, proxy_url)
            if proxy_url
            else _bandcamp_search(artist, title)
        )
    else:
        try:
            search = ydl.extract_info(f"scsearch{SEARCH_LIMIT}:{artist} {title}", download=False)
        except DownloadError as error:
            raise _search_error(error) from error
        entries = search.get("entries") if isinstance(search, dict) else None
        if not isinstance(entries, list):
            raise _SourceError("search_response_invalid")
    inspected = 0
    last_miss = _SourceError("exact_match_not_found", miss=True)
    for raw in entries[:SEARCH_LIMIT]:
        candidate = _candidate(raw, provider)
        if candidate is None or not _matches(candidate[0], artist, title):
            continue
        inspected += 1
        try:
            detail = ydl.extract_info(candidate[1], download=False)
        except DownloadError as error:
            translated = _translate_error(error)
            if translated.status != "miss":
                raise translated from error
            last_miss = translated
        else:
            verified = _candidate(detail, provider)
            if (
                verified is not None
                and str(detail["id"]) == str(candidate[0]["id"])
                and verified[1] == candidate[1]
                and _matches(detail, artist, title)
            ):
                try:
                    detail["formats"] = [_formats(detail, provider)[0]]
                    return dict(detail)
                except _SourceError as error:
                    last_miss = error
            else:
                last_miss = _SourceError("source_unavailable", miss=True)
        if inspected >= _METADATA_LIMIT:
            break
    raise last_miss


def _select_url(ydl: Any, provider: str, artist: str, title: str, url: str) -> dict[str, Any]:
    if _track_url(url, provider) != url:
        raise _SourceError("source_url_invalid")
    try:
        detail = ydl.extract_info(url, download=False)
    except DownloadError as error:
        raise _translate_error(error) from error
    verified = _candidate(detail, provider)
    if verified is None or verified[1] != url or not _matches(verified[0], artist, title):
        raise _SourceError("exact_match_not_found", miss=True)
    return dict(verified[0], formats=[_formats(verified[0], provider)[0]])


def _download(request: dict[str, Any]) -> dict[str, Any]:
    with socks_transport(request.get("proxy_url")):
        return _download_track(request)


def _download_track(request: dict[str, Any]) -> dict[str, Any]:
    provider, artist, title = request.get("provider"), request.get("artist"), request.get("title")
    max_bytes, output_value = request.get("max_bytes"), request.get("output_directory")
    if (
        not isinstance(provider, str)
        or provider not in _EXTRACTORS
        or not isinstance(artist, str)
        or not artist.strip()
        or len(artist) > 2048
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 2048
        or not isinstance(output_value, str)
        or not output_value
        or type(max_bytes) is not int
        or not 1024 <= max_bytes <= 1024 * 1024 * 1024
    ):
        raise _SourceError("request_invalid")
    source_url = request.get("source_url")
    if "source_url" in request and _track_url(source_url, provider) != source_url:
        raise _SourceError("source_url_invalid")
    if "source_url" in request and not isinstance(source_url, str):
        raise _SourceError("source_url_invalid")
    client_id = request.get("soundcloud_client_id")
    if client_id is not None and (provider != "soundcloud" or not valid_client_id(client_id)):
        raise _SourceError("client_id_invalid")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise _SourceError("ffmpeg_unavailable")
    options = _options(request.get("proxy_url")) | {
        "extract_flat": "in_playlist",
        "playlistend": SEARCH_LIMIT,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "enable_file_urls": False,
    }
    with tempfile.TemporaryDirectory(prefix=f"local-music-{provider}-") as temporary_value:
        temporary = Path(temporary_value)
        if client_id is not None:
            options["cachedir"] = str(temporary / "client-cache")
        options.update(
            {
                "outtmpl": str(temporary / "%(id)s.%(ext)s"),
                "format": "bestaudio",
                "max_filesize": max_bytes,
                "overwrites": False,
                "continuedl": False,
                "progress_hooks": [_size_hook(max_bytes)],
            }
        )
        # Register only the source's built-in extractors; no Generic or third-party dispatch.
        with YoutubeDL(options, auto_init=False) as ydl:
            if client_id is not None:
                ydl.cache.store("soundcloud", "client_id", client_id)
            for extractor in (
                (SoundcloudIE, SoundcloudSearchIE) if provider == "soundcloud" else (BandcampIE,)
            ):
                ydl.add_info_extractor(extractor())
            selected = (
                _select_url(ydl, provider, artist, title, source_url)
                if source_url is not None
                else _select(ydl, provider, artist, title, request.get("proxy_url"))
            )
            expected = request.get("expected_duration_seconds") or selected.get("duration")
            if (
                isinstance(expected, bool)
                or not isinstance(expected, (int, float))
                or not math.isfinite(expected)
                or expected <= 0
            ):
                raise _SourceError("duration_unknown")
            try:
                # Download the metadata already checked above, without a second unchecked lookup.
                ydl.process_ie_result(selected, download=True)
            except DownloadError as error:
                raise _SourceError("download_failed") from error
        candidates = [
            p
            for p in temporary.iterdir()
            if p.is_file() and p.suffix.removeprefix(".") in _AUDIO_EXTENSIONS
        ]
        if len(candidates) != 1:
            raise _SourceError("download_result_invalid")
        source = candidates[0]
        if (
            not 0 < source.stat().st_size <= max_bytes
            or not _looks_like_audio(source)
            or not _probe_audio(source)
        ):
            raise _SourceError("downloaded_content_invalid")
        try:
            audio_duration(source, expected_seconds=expected)
            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
        except (ValueError, subprocess.SubprocessError) as error:
            raise _SourceError("downloaded_content_invalid") from error
        if decoded.returncode:
            raise _SourceError("downloaded_content_invalid")
        output = Path(output_value)
        output.mkdir(parents=True, exist_ok=True)
        published = _publish_exclusive(
            source, output, _safe_filename(f"{artist} - {title}{source.suffix}")
        )
        return {
            "status": "downloaded",
            "artifact_ref": _content_ref(published),
            "expected_duration_seconds": expected,
        }


def _refresh_client_id() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="soundcloud-client-") as temporary:
        options = _options() | {"cachedir": temporary, "retries": 0, "extractor_retries": 0}
        with YoutubeDL(options, auto_init=False) as ydl:
            extractor = SoundcloudIE()
            ydl.add_info_extractor(extractor)
            try:
                extractor.initialize()
            except DownloadError as error:
                raise _translate_error(error) from error
            value = ydl.cache.load("soundcloud", "client_id")
            if not valid_client_id(value):
                raise _SourceError("client_id_unavailable")
            return {"status": "configured", "client_id": str(value)}


def main() -> int:
    plugin_dirs.value = []
    try:
        raw = sys.stdin.read(32 * 1024 + 1)
        if len(raw) > 32 * 1024:
            raise _SourceError("request_invalid")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise _SourceError("request_invalid")
        response = (
            _refresh_client_id()
            if request.get("action") == "refresh_client_id"
            else _download(request)
        )
    except _SourceError as error:
        response = {"status": error.status, "code": error.code}
    except (ValueError, TypeError):
        response = {"status": "failed", "code": "request_invalid"}
    except OSError:
        response = {"status": "failed", "code": "operational_failure"}
    sys.stdout.write(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
