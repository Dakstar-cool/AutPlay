"""Synthetic loopback Jamendo API/audio transport; production URLs stay fixed."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.request import OpenerDirector, Request, urlopen

from autplay.adapters.child_process import discovery_child_launch
from autplay.adapters.filesystem.vault_process import ChildLaunch
from autplay.adapters.jamendo import JamendoProvider

PAYLOAD = b"ID3" + b"synthetic Jamendo bytes" * 4096
CLIENT_ID = "fixture-client-id"


def provider(base: str) -> JamendoProvider:
    class Opener:
        def open(self, request: Request, *, timeout: float) -> HTTPResponse:
            remote = request.full_url
            if remote.startswith("https://api.jamendo.com/v3.0/tracks/"):
                path = "/api"
            elif remote.startswith("https://prod-1.storage.jamendo.com/download/track/10/"):
                path = "/audio"
            else:
                raise ValueError("unexpected synthetic request")
            return cast(HTTPResponse, urlopen(base + path, timeout=timeout))

    result = JamendoProvider(CLIENT_ID)
    result._opener = cast(OpenerDirector, Opener())
    return result


@dataclass(frozen=True)
class LocalDiscovery:
    base: str
    launch: ChildLaunch
    requested: threading.Event
    lookups: list[int]


@contextmanager
def local_discovery(*, mode: str = "none", payload: bytes = PAYLOAD) -> Iterator[LocalDiscovery]:
    requested, stop = threading.Event(), threading.Event()
    lookups: list[int] = []
    audio_count = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal audio_count
            if self.path == "/api":
                lookups.append(1)
                artist = (
                    "21"
                    if mode == "artist_before" or (mode == "artist_after" and len(lookups) > 1)
                    else "20"
                )
                data = json.dumps(
                    {
                        "headers": {"status": "success", "code": 0},
                        "results": [
                            {
                                "id": "10",
                                "name": "Morning Light",
                                "artist_name": "Open Artist",
                                "artist_id": artist,
                                "album_name": "Open Album",
                                "duration": "209",
                                "license_ccurl": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                                "shareurl": "https://www.jamendo.com/track/10",
                                "audiodownload_allowed": True,
                                "audiodownload": "https://prod-1.storage.jamendo.com/download/track/10/mp32/?client_id="
                                + CLIENT_ID,
                            }
                        ],
                    }
                ).encode()
                self.send_response(206 if mode == "json_206" else 200)
                self.send_header(
                    "Content-Length", str(len(data) + (100 if mode == "json_short" else 0))
                )
                self.end_headers()
                self.wfile.write(data)
                return
            assert self.path == "/audio"
            audio_count += 1
            requested.set()
            if mode == "http" and audio_count == 1:
                self.send_error(503)
                return
            self.send_response(206 if mode == "206" else 200)
            self.send_header("Content-Type", "audio/mpeg")
            if mode in {"cl_duplicate", "te_cl"}:
                self.send_header("Content-Length", str(len(payload)))
                self.send_header(
                    "Content-Length" if mode == "cl_duplicate" else "Transfer-Encoding",
                    str(len(payload)) if mode == "cl_duplicate" else "chunked",
                )
            elif mode in {"te_duplicate", "te_unsupported"}:
                self.send_header("Transfer-Encoding", "gzip")
                if mode == "te_duplicate":
                    self.send_header("Transfer-Encoding", "chunked")
            elif mode == "cl_negative":
                self.send_header("Content-Length", "-1")
            elif mode == "chunked_short":
                self.send_header("Transfer-Encoding", "chunked")
            elif mode != "unframed":
                self.send_header(
                    "Content-Length", str(len(payload) + (100 if mode == "short" else 0))
                )
            self.end_headers()
            with suppress(OSError):
                if mode == "chunked_short":
                    self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                    return
                block = max(1, (len(payload) + 39) // 40)
                for start in range(0, len(payload), block):
                    if stop.wait(0.8 if mode == "long" else 0):
                        break
                    self.wfile.write(payload[start : start + block])
                    self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def launch() -> tuple[list[str], dict[str, str]]:
        arguments, environment = discovery_child_launch(CLIENT_ID)
        environment["AUTPLAY_TEST_DISCOVERY_URL"] = base
        script = Path(__file__).parent / "fixtures" / "discovery_download_child.py"
        return [arguments[0], "-I", str(script)], environment

    try:
        yield LocalDiscovery(base, launch, requested, lookups)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
