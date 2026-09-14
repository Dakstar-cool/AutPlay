from __future__ import annotations

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.downloader.external import ExternalFD, FFmpegFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import DownloadError

from local_music_acquisition.providers import _music_sites_worker, _yt_dlp_worker
from local_music_acquisition.providers._proxy_transport import socks_transport

PROXY = "socks5h://127.0.0.1:10808"


@pytest.mark.parametrize("downloader", [FFmpegFD, HlsFD])
def test_external_network_and_native_hls_fallback_fail_before_ffmpeg_starts(
    monkeypatch,
    tmp_path,
    downloader,
):
    original = ExternalFD.real_download
    local_postprocess = FFmpegPostProcessor.run_ffmpeg
    options = _yt_dlp_worker._options(PROXY)
    info = {
        "url": "https://media.invalid/playlist.m3u8",
        "protocol": "m3u8_native",
        "ext": "mp4",
        "is_live": True,
        "hls_media_playlist_data": "#EXTM3U\n#EXTINF:1\nhttps://media.invalid/chunk.ts\n",
    }
    monkeypatch.setattr(
        FFmpegFD, "_call_downloader", lambda *args: pytest.fail("external network tool started")
    )
    with socks_transport(PROXY), YoutubeDL(options) as ydl:
        with pytest.raises(DownloadError, match="proxy_external_downloader_unsupported"):
            downloader(ydl, options).real_download(str(tmp_path / "audio.mp4"), info)
        assert FFmpegPostProcessor.run_ffmpeg is local_postprocess
    assert ExternalFD.real_download is original


@pytest.mark.parametrize("worker", [_yt_dlp_worker, _music_sites_worker])
def test_both_workers_apply_and_restore_socks_guard_even_on_failure(monkeypatch, worker):
    original = ExternalFD.real_download

    def acquire(request):
        assert ExternalFD.real_download is not original
        raise OSError("fixture failure")

    monkeypatch.setattr(worker, "_download_track", acquire)
    with pytest.raises(OSError, match="fixture failure"):
        worker._download({"proxy_url": PROXY})
    assert ExternalFD.real_download is original


def test_direct_provider_has_no_external_transport_override():
    original = ExternalFD.real_download
    with socks_transport(None):
        assert ExternalFD.real_download is original
