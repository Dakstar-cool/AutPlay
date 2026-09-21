"""Bounded loopback provider and private launcher shared by process/DB proofs."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.vault_process import ChildLaunch


@dataclass(frozen=True)
class LocalProvider:
    launch: ChildLaunch
    requested: threading.Event


@contextmanager
def local_provider(
    payload: bytes,
    *,
    fail_first: bool = False,
    duration_seconds: float = 0,
) -> Iterator[LocalProvider]:
    requested = threading.Event()
    stopped = threading.Event()
    count = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal count
            count += 1
            requested.set()
            if fail_first and count == 1:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with suppress(OSError):
                block_size = max(1, (len(payload) + 39) // 40)
                for offset in range(0, len(payload), block_size):
                    if stopped.wait(duration_seconds / 40):
                        break
                    self.wfile.write(payload[offset : offset + block_size])
                    self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()

    def launch() -> tuple[list[str], dict[str, str]]:
        arguments, environment = provider_child_launch()
        script = Path(__file__).parent / "fixtures" / "provider_download_child.py"
        environment["AUTPLAY_TEST_PROVIDER_URL"] = f"http://127.0.0.1:{server.server_port}/audio"
        return [arguments[0], "-I", str(script)], environment

    try:
        yield LocalProvider(launch, requested)
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
