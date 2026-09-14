from __future__ import annotations

import functools
import http.server
import json
import subprocess
import threading
from types import SimpleNamespace
from typing import ClassVar

import pytest
from yt_dlp.utils import DownloadError

from local_music_acquisition import cli
from local_music_acquisition.models import PlaylistItem, ProviderMiss
from local_music_acquisition.orchestrator import DownloadSession
from local_music_acquisition.providers import _music_sites_worker as worker
from local_music_acquisition.providers.music_sites import BandcampProvider, SoundCloudProvider


def _format(identity="http_mp3_standard", **extra):
    return {
        "format_id": identity,
        "vcodec": "none",
        "acodec": "mp3",
        "ext": "mp3",
        "protocol": "http",
        "url": "https://cf-media.sndcdn.com/audio.mp3",
        "abr": 128,
        **extra,
    }


def _info(**extra):
    return {
        "id": "1234",
        "extractor_key": "Soundcloud",
        "artist": "Artist",
        "track": "Song",
        "title": "Song",
        "webpage_url": "https://soundcloud.com/artist/song",
        "formats": [_format()],
        "duration": 12,
        **extra,
    }


@pytest.mark.parametrize(
    "value",
    [
        "http://soundcloud.com/artist/song",
        "https://soundcloud.com.evil.test/artist/song",
        "https://user:password@soundcloud.com/artist/song",
        "https://soundcloud.com/artist/sets",
        "https://soundcloud.com/artist/song?secret_token=private",
        "file:///music.mp3",
        "https://soundcloud.com:443/artist/song",
        "https://artist.bandcamp.com/album/songs",
    ],
)
def test_track_urls_reject_other_origins_secrets_and_playlists(value):
    assert worker._track_url(value, "soundcloud") is None
    assert worker._track_url(value, "bandcamp") is None


def test_source_identity_requires_exact_extractor_and_numeric_id():
    assert worker._candidate(_info(), "soundcloud")
    assert worker._candidate(_info(extractor_key="Generic"), "soundcloud") is None
    assert worker._candidate(_info(id="../song"), "soundcloud") is None
    assert worker._candidate(_info(), "bandcamp") is None


@pytest.mark.parametrize(
    "message,code",
    [
        ("Read timed out: https://private.test?token=secret", "search_timeout"),
        ("HTTP Error 503: Service Unavailable", "search_service_unavailable"),
        ("HTTP Error 429: rate limit", "search_access_restricted"),
        ("SSL certificate verify failed", "search_tls_failed"),
    ],
)
def test_search_errors_preserve_actionable_category_without_private_details(message, code):
    result = worker._search_error(DownloadError(message))
    assert result.code == code
    assert result.status == "failed"
    assert "private" not in str(result)


def test_soundcloud_credit_punctuation_is_normalized_without_dropping_guests():
    info = _info(artist=None, artists=["Artist\uff0c Guest"], track="Song. w/ Guest")
    assert worker._matches(info, "Artist, Guest", "Song")
    assert not worker._matches(info, "Artist", "Song")
    assert not worker._matches(info, "Artist, Guest", "Song (Live)")
    assert not worker._matches(_info(artist=None, uploader="Artist"), "Artist", "Song")


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"format_id": "hls_mp3_preview"}, "preview_only"),
        ({"has_drm": True}, "drm_protected"),
        ({"protocol": "m3u8"}, "preview_only"),
        ({"url": "http://127.0.0.1/private.mp3"}, "preview_only"),
    ],
)
def test_preview_drm_external_downloader_and_untrusted_media_are_rejected(extra, code):
    with pytest.raises(worker._SourceError, match=code):
        worker._formats(_info(formats=[_format(**extra)]), "soundcloud")


def test_bandcamp_prefers_mp3_320_original_and_rejects_listening_stream():
    stream = _format("mp3-128", url="https://t4.bcbits.com/stream/audio.mp3")
    original = _format("mp3-320", url="https://t4.bcbits.com/download/audio.mp3")
    lossless = _format("flac", ext="flac", url="https://t4.bcbits.com/download/audio.flac")
    assert worker._formats(_info(formats=[stream, lossless, original]), "bandcamp")[0] == original
    with pytest.raises(worker._SourceError, match="original_download_unavailable"):
        worker._formats(_info(formats=[stream]), "bandcamp")


def test_soundcloud_prefers_original_then_direct_mp3():
    direct = _format()
    hls = _format("hls_aac_160k", protocol="m3u8_native", ext="m4a", abr=160)
    original = _format("download", ext="flac")
    assert worker._formats(_info(formats=[hls, direct]), "soundcloud")[0] == direct
    assert worker._formats(_info(formats=[direct, original]), "soundcloud")[0] == original


def test_full_metadata_is_rechecked_before_download():
    class Metadata:
        def extract_info(self, url, *, download):
            assert download is False
            if url.startswith("scsearch"):
                return {"entries": [_info()]}
            return _info(track="Song (Live)")

    with pytest.raises(worker._SourceError, match="source_unavailable"):
        worker._select(Metadata(), "soundcloud", "Artist", "Song")


def test_metadata_hydration_is_bounded_and_drm_does_not_abort_other_candidates():
    class Metadata:
        calls = 0

        def extract_info(self, url, *, download):
            if url.startswith("scsearch"):
                return {"entries": [_info(id=str(n)) for n in range(20)]}
            self.calls += 1
            raise DownloadError("This track is DRM protected: private URL")

    metadata = Metadata()
    with pytest.raises(worker._SourceError, match="drm_protected"):
        worker._select(metadata, "soundcloud", "Artist", "Song")
    assert metadata.calls == 3


def test_bandcamp_search_checks_schema_bounds_and_origins(monkeypatch):
    class Response:
        status_code = 200
        headers: ClassVar = {"Content-Type": "application/json"}
        payload = json.dumps(
            {
                "auto": {
                    "results": [
                        {
                            "type": "t",
                            "id": 1234,
                            "band_name": "Artist",
                            "name": "Song",
                            "item_url_path": "https://artist.bandcamp.com/track/song",
                        }
                    ]
                }
            }
        ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield self.payload

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert self.trust_env is False
            assert url == "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"
            assert kwargs["allow_redirects"] is False
            return Response()

    monkeypatch.setattr(worker.requests, "Session", Session)
    assert worker._candidate(worker._bandcamp_search("Artist", "Song")[0], "bandcamp")
    Response.payload = b'{"auto": []}'
    with pytest.raises(worker._SourceError, match="search_response_invalid"):
        worker._bandcamp_search("Artist", "Song")
    Response.payload = b" " * (worker._SEARCH_BYTES + 1)
    with pytest.raises(worker._SourceError, match="search_response_too_large"):
        worker._bandcamp_search("Artist", "Song")
    Response.status_code = 429
    with pytest.raises(worker._SourceError, match="search_access_restricted"):
        worker._bandcamp_search("Artist", "Song")


@pytest.mark.parametrize("provider_type", [SoundCloudProvider, BandcampProvider])
def test_provider_uses_private_worker_and_redacted_miss(monkeypatch, tmp_path, provider_type):
    provider = provider_type()

    def popen(command, **kwargs):
        assert command[-1] == "local_music_acquisition.providers._music_sites_worker"
        assert kwargs["env"]["YTDLP_NO_PLUGINS"] == "1"

        def communicate(raw, timeout):
            assert json.loads(raw)["provider"] == provider.name
            return json.dumps({"status": "miss", "code": "drm_protected"}), "private URL"

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(ProviderMiss, match=f"{provider.name}.drm_protected"):
        provider.acquire(PlaylistItem(1, "Artist", "Song"), tmp_path)


def test_unavailable_recording_is_a_miss_and_does_not_open_provider_circuit(tmp_path):
    class Missing:
        name = "soundcloud"
        requires_rights_confirmation = False

        def acquire(self, item, output):
            raise ProviderMiss(self.name, "preview_only")

    session = DownloadSession((Missing(),), frozenset())
    for _ in range(3):
        result = session.download(PlaylistItem(1, "Artist", "Song"), tmp_path)
        assert result.status == "not_found"
        assert result.error_code == "soundcloud.preview_only"
    assert session.available


@pytest.mark.parametrize("site", ["soundcloud", "bandcamp"])
def test_cli_requires_source_rights_before_network_or_input_read(tmp_path, capsys, site):
    result = cli.main(
        [
            str(tmp_path / "missing.txt"),
            "--output-dir",
            str(tmp_path / "music"),
            "--disable-jamendo",
            "--disable-hitmo",
            "--disable-yt-dlp",
            f"--enable-{site}",
        ]
    )
    assert result == 2
    assert f"{site}_rights_confirmation_required" in capsys.readouterr().err
    assert not (tmp_path / "music").exists()


def test_cli_enables_both_sources_in_order_for_durable_queue(monkeypatch, tmp_path):
    from local_music_acquisition import queue

    calls = []
    monkeypatch.setattr(queue, "enqueue", lambda *args, **kwargs: calls.append(kwargs))

    def run(root, **kwargs):
        assert [p.name for p in kwargs["providers"]] == ["soundcloud", "bandcamp"]
        assert kwargs["rights_confirmed"] == frozenset({"soundcloud", "bandcamp"})
        return {
            "state": "finished",
            "failed": 0,
            "not_found": 0,
            "malformed": 0,
            "pending": 0,
            "retry": 0,
            "needs_review": 0,
        }

    monkeypatch.setattr(queue, "run_queue", run)
    result = cli.main(
        [
            str(tmp_path / "input.txt"),
            "--output-dir",
            str(tmp_path),
            "--queue-dir",
            str(tmp_path / "queue"),
            "--disable-jamendo",
            "--disable-hitmo",
            "--disable-yt-dlp",
            "--enable-soundcloud",
            "--soundcloud-rights-confirmed",
            "--enable-bandcamp",
            "--bandcamp-rights-confirmed",
        ]
    )
    assert result == 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "provider,seconds,expected_ok",
    [
        ("soundcloud", 12, True),
        ("bandcamp", 12, True),
        ("soundcloud", 1, False),
    ],
)
def test_worker_downloads_real_http_audio_and_rejects_short_fragments(
    monkeypatch,
    tmp_path,
    provider,
    seconds,
    expected_ok,
):
    runtime_options = worker._options
    monkeypatch.setattr(worker, "_options", lambda proxy_url=None: runtime_options(proxy_url))
    audio = tmp_path / "fixture.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            str(seconds),
            "-y",
            str(audio),
        ],
        check=True,
    )

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(QuietHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        selected = _info(
            formats=[_format(url=f"http://127.0.0.1:{server.server_port}/fixture.mp3")]
        )
        monkeypatch.setattr(worker, "_select", lambda *args: selected)
        output = tmp_path / "result"
        request = {
            "provider": provider,
            "artist": "Artist",
            "title": "Song",
            "max_bytes": 1024 * 1024,
            "output_directory": str(output),
        }
        if expected_ok:
            result = worker._download(request)
            assert result["status"] == "downloaded"
            assert result["expected_duration_seconds"] == 12
            assert (output / "Artist - Song.mp3").read_bytes() == audio.read_bytes()
        else:
            with pytest.raises(worker._SourceError, match="downloaded_content_invalid"):
                worker._download(request)
            assert not output.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
