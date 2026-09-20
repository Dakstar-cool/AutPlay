"""Actual FFmpeg/HLS/unknown-length input obeys the contained writer's byte cap."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.provider_media import options, stream_format
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.media.tools import (
    FfmpegDecodeValidator,
    FfprobeInspector,
    SubprocessExecutableRunner,
    ValidatedMediaInspector,
)
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.resource_execution import ExecutionState, ExecutionStatus
from autplay.ports.vault import ProcessResult
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from process_tree_support import provider_ticket, wait_tree_exit
from test_provider_child import command


@pytest.mark.parametrize("fault", ["missing", "protocol", "aes", "codec"])
def test_unsupported_downloader_never_falls_back_or_writes_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    ytdlp = importlib.import_module("yt_dlp")
    ffmpeg = importlib.import_module("yt_dlp.downloader.external").FFmpegFD
    info: dict[str, Any] = {
        "url": "http://127.0.0.1:9/no-network",
        "protocol": "m3u8_native",
        "acodec": "aac",
    }
    if fault == "missing":
        monkeypatch.setattr(ffmpeg, "available", classmethod(lambda cls, path=None: False))
    elif fault == "protocol":
        info["protocol"] = "http_dash_segments_generator"
    elif fault == "aes":
        info["hls_aes"] = {"uri": "synthetic"}
    else:
        info["acodec"] = "unknown"
    monkeypatch.chdir(tmp_path)
    with ytdlp.YoutubeDL(options()) as ydl, pytest.raises(ValueError, match="unsupported"):
        stream_format(ydl, info)
    assert list(tmp_path.iterdir()) == []


def test_unsupported_ffmpeg_version_is_rejected_before_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ytdlp = importlib.import_module("yt_dlp")
    ffmpeg = importlib.import_module("yt_dlp.downloader.external").FFmpegFD
    monkeypatch.setattr(ffmpeg, "can_download", classmethod(lambda cls, info: True))
    monkeypatch.setattr(
        ffmpeg, "download", lambda *args: pytest.fail("media began before preflight")
    )
    monkeypatch.setattr(shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(
        SubprocessExecutableRunner,
        "run",
        lambda *args, **kwargs: ProcessResult(0, b"ffmpeg version 9.0.1\n", b""),
    )
    with ytdlp.YoutubeDL(options()) as ydl, pytest.raises(ValueError, match="runtime_unsupported"):
        stream_format(
            ydl, {"url": "http://127.0.0.1:9/no-media", "protocol": "m3u8_native", "acodec": "aac"}
        )


@contextmanager
def media_server(
    directory: Path,
    *,
    truncated: bool = False,
    framing: str = "normal",
    requests: dict[str, int] | None = None,
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if requests is not None:
                requests[self.path] = requests.get(self.path, 0) + 1
            source = directory / self.path.lstrip("/")
            assert source.parent == directory
            payload = source.read_bytes()
            self.send_response(200)
            # Deliberately no Content-Length: the downloader cannot trust a hint.
            if framing == "short_length":
                self.send_header("Content-Length", str(len(payload) + 100))
            else:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.send_header(
                "Content-Type",
                "application/vnd.apple.mpegurl"
                if source.suffix == ".m3u8"
                else "application/octet-stream",
            )
            self.end_headers()
            with suppress(OSError):
                if framing == "short_length":
                    self.wfile.write(payload)
                    return
                self.wfile.write(f"{len(payload):x}\r\n".encode())
                if truncated and source.suffix not in {".m3u8", ".mpd"}:
                    self.wfile.write(payload[: len(payload) // 2])
                else:
                    self.wfile.write(payload + b"\r\n")
                    if framing != "no_end_chunk":
                        self.wfile.write(b"0\r\n\r\n")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows FFmpeg process tree")
@pytest.mark.parametrize(
    "kind,mode",
    [
        (kind, mode)
        for kind in ("aac", "opus", "hls", "dash")
        for mode in ("success", "overflow", "truncated", "no_end_chunk", "short_length")
        if mode not in {"no_end_chunk", "short_length"} or kind in {"aac", "opus"}
    ],
)
def test_actual_media_stream_is_bounded_and_vault_compatible(
    tmp_path: Path,
    kind: str,
    mode: str,
) -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "real FFmpeg/ffprobe are required for this acceptance proof"
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    name = {"aac": "audio.m4a", "opus": "audio.webm", "hls": "audio.m3u8", "dash": "audio.mpd"}[
        kind
    ]
    codec = "opus" if kind == "opus" else "aac"
    arguments = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=997:sample_rate=48000:duration=6",
        "-c:a",
        "libopus" if kind == "opus" else "aac",
        "-b:a",
        "128k",
    ]
    if kind == "hls":
        arguments += ["-f", "hls", "-hls_time", "1", "-hls_list_size", "0"]
    elif kind == "dash":
        arguments += ["-f", "dash", "-seg_duration", "1"]
    subprocess.run([*arguments, name], cwd=fixture, check=True, capture_output=True, timeout=15)
    if kind == "hls":
        assert len(list(fixture.glob("*.ts"))) >= 5
    root = tmp_path / "vault"
    FilesystemVaultStorage(root)
    maximum = 32768 if mode == "overflow" else 1024**2
    requests: dict[str, int] = {}
    with media_server(
        fixture,
        truncated=mode == "truncated",
        framing=mode,
        requests=requests,
    ) as url:

        def launch() -> tuple[list[str], dict[str, str]]:
            args, env = provider_child_launch()
            env["AUTPLAY_TEST_MEDIA_URL"] = f"{url}/{name}"
            env["AUTPLAY_TEST_MEDIA_PROTOCOL"] = {
                "hls": "m3u8_native",
                "dash": "http_dash_segments",
            }.get(kind, "http")
            env["AUTPLAY_TEST_MEDIA_CODEC"] = codec
            if kind in {"aac", "opus"}:
                env["PATH"] = ""  # Progressive media must not depend on FFmpeg availability.
            return [
                args[0],
                "-I",
                str(Path(__file__).parent / "fixtures" / "provider_ffmpeg_child.py"),
            ], env

        child = RetainedVaultProcess(
            provider_ticket(),
            ResourceIoDeadline(monotonic()),
            tree_factory=WindowsJobTree,
            launch=launch,
        )
        identity = None
        try:
            identity = child.spawn()
            child.allow_go(
                ExecutionStatus(child.ticket, ExecutionState.RUNNING, None, identity, None)
            )
            document = command(root, child.ticket.execution_id)
            document["max_object_bytes"] = maximum
            child.go(document)
            tag, data = child.read_result()
            workspace = root / "provider-work" / child.ticket.execution_id.hex
            source = workspace / "audio.media"
            if mode != "success":
                expected = (
                    "provider_download_too_large"
                    if mode == "overflow"
                    else "provider_download_failed"
                )
                assert tag == b"E" and decode_document(data)["code"] == expected
                assert source.stat().st_size <= maximum
                assert not (root / "staging" / f"provider-{child.ticket.execution_id.hex}").exists()
            else:
                assert tag == b"R", decode_document(data)
                assert decode_document(data)["byte_size"] == source.stat().st_size
                assert wait_tree_exit(child, identity).exit_code == 0
                metadata = ValidatedMediaInspector(
                    FfmpegDecodeValidator(ffmpeg),
                    FfprobeInspector(ffprobe),
                ).inspect(source)
                assert metadata.codec == codec
                assert 5500 <= metadata.duration_ms <= 6500
            assert [path.name for path in workspace.iterdir()] == ["audio.media"]
            if kind in {"aac", "opus"}:
                assert requests == {f"/{name}": 1}  # No unsafe retry on a non-seekable stdout.
        finally:
            child.request_stop()
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()
