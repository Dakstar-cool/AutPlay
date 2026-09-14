from __future__ import annotations

import hashlib
import io
import json
import signal
import subprocess
import threading
import wave
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from local_music_acquisition import cli, queue
from local_music_acquisition.models import (
    AcquiredArtifact,
    PlaylistItem,
    ProviderFailure,
    ProviderMiss,
)
from local_music_acquisition.orchestrator import DownloadSession, download_playlist
from local_music_acquisition.providers import _music_sites_worker as sites
from local_music_acquisition.providers import _yt_dlp_worker as youtube
from local_music_acquisition.providers.music_sites import BandcampProvider, SoundCloudProvider
from local_music_acquisition.providers.yt_dlp import YtDlpProvider
from local_music_acquisition.xray import XrayConfig, XrayManager


@pytest.mark.parametrize("durable", [False, True])
def test_parallel_pipeline_providers_share_xray_through_fallback(runtime, tmp_path, durable):
    manager, children, timers, _ = runtime
    first = SoundCloudProvider(requires_proxy=True, xray=manager)
    second = BandcampProvider(requires_proxy=True, xray=manager)
    overlap = threading.Barrier(2)
    payload = io.BytesIO()
    with wave.open(payload, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 96000)
    audio = payload.getvalue()
    first_call = True

    def acquire(provider, item, output, proxy_url):
        nonlocal first_call
        assert proxy_url == manager.config.proxy_url
        # A provider lane is serialized; its first call falls back regardless of queue order.
        if provider.name == "soundcloud" and first_call:
            first_call = False
            raise ProviderMiss(provider.name, "exact_match_not_found")
        overlap.wait(timeout=5)
        assert len(children) == 1
        assert children[0].terminated == 0
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{item.title}.wav").write_bytes(audio)
        return AcquiredArtifact(provider.name, "sha256:" + hashlib.sha256(audio).hexdigest()[:12])

    first._acquire = lambda item, output, *, proxy_url: acquire(first, item, output, proxy_url)
    second._acquire = lambda item, output, *, proxy_url: acquire(second, item, output, proxy_url)
    playlist = tmp_path / "playlist.txt"
    playlist.write_text("Artist - One\nArtist - Two\n")
    rights = frozenset({"soundcloud", "bandcamp"})
    if durable:
        root = tmp_path / "queue"
        queue.enqueue(playlist, root, tmp_path / "music")
        summary = queue.run_queue(root, providers=(first, second), rights_confirmed=rights)
        assert len(list((tmp_path / "music" / "tracks").glob("*/receipt.json"))) == 2
    else:
        summary = download_playlist(
            playlist,
            tmp_path / "music",
            providers=(first, second),
            rights_confirmed=rights,
            max_workers=2,
        )
    assert summary["downloaded"] == 2
    assert len(children) == 1
    timers[-1].fire()
    assert children[0].terminated == 1


def test_proxy_start_failure_is_a_track_failure_and_direct_provider_still_works(tmp_path):
    manager = XrayManager(XrayConfig(binary=tmp_path / "missing", config=tmp_path / "missing"))
    failed = SoundCloudProvider(requires_proxy=True, xray=manager)
    direct = BandcampProvider()
    direct._acquire = lambda item, output: AcquiredArtifact(direct.name, "sha256:0123456789ab")
    session = DownloadSession((failed, direct), frozenset({failed.name, direct.name}))
    try:
        result = session.download(PlaylistItem(1, "Artist", "Track"), tmp_path)
        assert result.status == "downloaded"
        assert result.provider == "bandcamp"
        assert session.lanes[0].terminal_failures == 1
        assert manager._process is None
    finally:
        manager.close()


def test_requires_proxy_without_manager_fails_closed(tmp_path):
    provider = SoundCloudProvider(requires_proxy=True)
    with pytest.raises(ProviderFailure, match=r"soundcloud\.xray_not_configured"):
        provider.acquire(PlaylistItem(1, "Artist", "Song"), tmp_path)


@pytest.mark.parametrize("provider_type", [YtDlpProvider, SoundCloudProvider, BandcampProvider])
@pytest.mark.parametrize("proxied", [False, True])
def test_worker_gets_proxy_only_for_selected_provider(
    monkeypatch, tmp_path, provider_type, proxied
):
    active, requests = [], []

    class Manager:
        @contextmanager
        def lease(self):
            active.append(True)
            try:
                yield "socks5h://127.0.0.1:10808"
            finally:
                active.pop()

    def spawn(command, **kwargs):
        assert bool(active) is proxied
        assert all("PROXY" not in key.upper() for key in kwargs["env"])

        def communicate(raw, timeout):
            requests.append(json.loads(raw))
            assert bool(active) is proxied
            return '{"status":"downloaded","artifact_ref":"sha256:0123456789ab"}', "secret"

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setenv("ALL_PROXY", "http://secret:secret@unrelated.invalid")
    monkeypatch.setattr(subprocess, "Popen", spawn)
    provider = provider_type(requires_proxy=proxied, xray=Manager())
    provider.acquire(PlaylistItem(1, "Artist", "Track"), tmp_path)
    assert requests[0].get("proxy_url") == ("socks5h://127.0.0.1:10808" if proxied else None)
    assert active == []


def test_bandcamp_search_has_explicit_session_proxy_without_environment(monkeypatch):
    proxy = "socks5h://127.0.0.1:10808"
    calls = []

    class Session:
        def __init__(self):
            self.proxies = {}
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def post(self, url, **kwargs):
            calls.append((self.trust_env, self.proxies.copy()))
            yield SimpleNamespace(
                status_code=200,
                headers={"Content-Type": "application/json"},
                iter_content=lambda size: [b'{"auto":{"results":[]}}'],
            )

    monkeypatch.setattr(sites.requests, "Session", Session)
    sites._bandcamp_search("Artist", "Track", proxy)
    sites._bandcamp_search("Artist", "Track")
    assert calls == [(False, {"http": proxy, "https": proxy}), (False, {})]
    assert youtube._options(proxy)["proxy"] == proxy
    assert youtube._options()["proxy"] == ""


@pytest.mark.parametrize("interrupted", [False, True])
def test_cli_closes_owned_xray_and_restores_signal_handlers(
    runtime,
    monkeypatch,
    tmp_path,
    interrupted,
):
    manager, children, _, _ = runtime
    monkeypatch.setattr(cli, "XrayManager", lambda settings: manager)
    previous = signal.getsignal(signal.SIGTERM)

    def acquire(self, item, output, *, proxy_url):
        assert children[0].poll() is None
        if interrupted:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return AcquiredArtifact(self.name, "sha256:0123456789ab")

    monkeypatch.setattr(SoundCloudProvider, "_acquire", acquire)
    playlist = tmp_path / "playlist.txt"
    playlist.write_text("Artist - One\nArtist - Two\n")
    code = cli.main(
        [
            str(playlist),
            "--output-dir",
            str(tmp_path / "music"),
            "--disable-jamendo",
            "--disable-hitmo",
            "--disable-yt-dlp",
            "--enable-soundcloud",
            "--soundcloud-rights-confirmed",
            "--soundcloud-requires-proxy",
            "--xray-binary",
            str(manager.config.binary),
            "--xray-config",
            str(manager.config.config),
        ]
    )
    assert code == (1 if interrupted else 0)
    assert children[0].terminated == 1
    assert signal.getsignal(signal.SIGTERM) is previous


def test_runtime_check_never_starts_xray(runtime, tmp_path, capsys):
    manager, children, _, _ = runtime
    result = cli.main(
        [
            str(tmp_path / "missing.txt"),
            "--output-dir",
            str(tmp_path),
            "--check-runtime",
            "--disable-jamendo",
            "--disable-hitmo",
            "--disable-yt-dlp",
            "--enable-soundcloud",
            "--soundcloud-requires-proxy",
            "--xray-binary",
            str(manager.config.binary),
            "--xray-config",
            str(manager.config.config),
        ]
    )
    assert result == 0
    assert children == []
    report = capsys.readouterr().out
    assert '"xray_config": "configured"' in report
    assert "UUID" not in report
    assert str(tmp_path) not in report
