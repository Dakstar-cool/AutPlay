"""Fail-closed network transport policy inside a single private provider worker."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from yt_dlp.downloader.external import ExternalFD
from yt_dlp.utils import DownloadError

from ..xray import proxy_address


@contextmanager
def socks_transport(proxy_url: object) -> Iterator[None]:
    """Block external network tools, including native HLS's implicit FFmpeg fallback.

    yt-dlp's pinned ExternalFD base owns FFmpeg/curl/etc. network downloads.
    FFmpeg postprocessors use a separate class and remain available for local files.
    This scope runs in a dedicated subprocess, with one request per worker.
    """
    if proxy_url is None:
        yield
        return
    proxy_address(proxy_url)
    original = ExternalFD.real_download

    def blocked(_downloader: Any, _filename: Any, _info: Any) -> bool:
        raise DownloadError("proxy_external_downloader_unsupported")

    ExternalFD.real_download = blocked
    try:
        yield
    finally:
        ExternalFD.real_download = original
