"""Disposable loopback-only HTTP fixture. No production URLs or credentials."""

from __future__ import annotations

import hmac
import io
import math
import re
import secrets
import socket
import struct
import threading
import wave
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BYTES = 32 * 1024 * 1024
SLOW_PREFIX_BYTES = 4096
MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


@dataclass(frozen=True)
class Fixture:
    identifier: str
    extension: str
    data: bytes

    @property
    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.data).hexdigest()


def synthetic_wav() -> Fixture:
    """Eight seconds of quiet, deterministic 440 Hz stereo PCM, 48 kHz/16 bit."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(8 * 48000):
            sample = round(1200 * math.sin(2 * math.pi * 440 * index / 48000))
            frames.extend(struct.pack("<hh", sample, sample))
        output.writeframes(frames)
    return Fixture("pcm-wav", ".wav", buffer.getvalue())


def load_fixtures(directory: Path | None) -> list[Fixture]:
    fixtures = [synthetic_wav()]
    if directory is not None:
        # Deliberately non-recursive. The caller supplies only synthetic/authorized media.
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in MIME:
                if len(fixtures) >= 17 or not 0 < path.stat().st_size <= MAX_BYTES:
                    raise ValueError("FIXTURE_LIMIT")
                data = path.read_bytes()
                if len(data) > MAX_BYTES:
                    raise ValueError("FIXTURE_LIMIT")
                fixtures.append(Fixture(f"fixture-{len(fixtures):02d}", path.suffix.lower(), data))
    return fixtures


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, fixtures: list[Fixture], token: str, sink: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.fixtures = {fixture.identifier: fixture for fixture in fixtures}
        self.token = token
        self.sink = sink
        self.sink_port = 0
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        self.stop_event = threading.Event()

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: socket.socket, client_address: Any) -> None:
        # Never emit a raw exception, request, path or token.
        pass

    def record(self, event: dict[str, Any]) -> bool:
        with self.lock:
            if len(self.events) >= 512:
                return False
            self.events.append(event)
            return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.events)


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_HEAD(self) -> None:
        self.respond(head=True)

    def do_GET(self) -> None:
        self.respond(head=False)

    def empty(self, status: int, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.close_connection = True

    def respond(self, head: bool) -> None:
        received = self.headers.get("Authorization", "")
        authenticated = hmac.compare_digest(
            received.encode(), ("Bearer " + self.server.token).encode()
        )
        if self.server.sink:
            self.server.record({"route": "sink", "auth_received": bool(received), "status": 204})
            self.empty(204)
            return
        match = re.fullmatch(r"/(media|redirect|cross|drop|slow)/([a-z0-9-]+)", self.path)
        if match is None or match[2] not in self.server.fixtures:
            self.empty(404)
            return
        route, identifier = match.groups()
        event: dict[str, Any] = {
            "route": route,
            "fixture": identifier,
            "method": self.command,
            "authenticated": authenticated,
        }
        if not authenticated:
            event["status"] = 401
            self.server.record(event)
            self.empty(401, WWW_Authenticate="Bearer")
            return
        if route in {"redirect", "cross"}:
            port = self.server.server_port if route == "redirect" else self.server.sink_port
            target = f"/media/{identifier}" if route == "redirect" else "/sink"
            event["status"] = 302
            self.server.record(event)
            self.empty(302, Location=f"http://127.0.0.1:{port}{target}")
            return
        fixture = self.server.fixtures[identifier]
        size = len(fixture.data)
        start, end, status = 0, size - 1, 200
        raw_range = self.headers.get("Range")
        if raw_range is not None:
            range_match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw_range)
            if range_match is None or not any(range_match.groups()):
                event["status"] = 416
                self.server.record(event)
                self.empty(416, Content_Range=f"bytes */{size}")
                return
            left, right = range_match.groups()
            # Reject oversized integers before parsing; single ranges only.
            if len(left) > 12 or len(right) > 12:
                self.empty(416, Content_Range=f"bytes */{size}")
                return
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                suffix = int(right)
                start = max(0, size - suffix) if suffix > 0 else size
            if start >= size or start > end:
                event["status"] = 416
                self.server.record(event)
                self.empty(416, Content_Range=f"bytes */{size}")
                return
            status = 206
        event.update(status=status, start=start, end=end)
        if route == "slow":
            event["body_delayed"] = not head and end - start + 1 > SLOW_PREFIX_BYTES
        if not self.server.record(event):
            self.empty(429)
            return
        self.send_response(status)
        self.send_header("Content-Type", MIME[fixture.extension])
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.close_connection = True
        if head:
            return
        try:
            body = fixture.data[start : end + 1]
            if route == "slow" and len(body) > SLOW_PREFIX_BYTES:
                # Complete tiny metadata ranges immediately. Deliver a bounded prefix
                # of larger ranges so the native owner can observe an incomplete body.
                self.wfile.write(body[:SLOW_PREFIX_BYTES])
                self.wfile.flush()
                if self.server.stop_event.wait(3):
                    return
                body = body[SLOW_PREFIX_BYTES:]
            if route == "drop":
                self.wfile.write(body[: max(1, len(body) // 2)])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.wfile.write(body)
        except OSError, TimeoutError:
            pass


class FixtureService:
    def __init__(self, fixtures: list[Fixture]) -> None:
        self.token = secrets.token_urlsafe(32)
        self.primary = FixtureHTTPServer(fixtures, self.token)
        self.sink = FixtureHTTPServer([], self.token, sink=True)
        self.primary.sink_port = self.sink.server_port
        self.threads: list[threading.Thread] = []

    def __enter__(self) -> FixtureService:
        for server in (self.primary, self.sink):
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            thread.start()
            self.threads.append(thread)
        return self

    def __exit__(self, *args: Any) -> None:
        for server in (self.primary, self.sink):
            server.stop_event.set()
            server.shutdown()
            server.server_close()
            server.token = ""
        for thread in self.threads:
            thread.join(timeout=2)
        self.token = ""

    def url(self, route: str, fixture: Fixture) -> str:
        return f"http://127.0.0.1:{self.primary.server_port}/{route}/{fixture.identifier}"


def verify_http() -> dict[str, Any]:
    """Exercise real sockets; a fixture contract check, never an audio-engine pass."""
    from http.client import HTTPConnection

    fixture = synthetic_wav()
    checks: list[str] = []
    with FixtureService([fixture]) as service:

        def request(
            route: str, *, range_value: str | None = None, auth: bool = True, method: str = "GET"
        ) -> tuple[int, dict[str, str], bytes]:
            connection = HTTPConnection("127.0.0.1", service.primary.server_port, timeout=5)
            headers = {"Authorization": "Bearer " + service.token} if auth else {}
            if range_value is not None:
                headers["Range"] = range_value
            try:
                connection.request(method, f"/{route}/{fixture.identifier}", headers=headers)
                response = connection.getresponse()
                status, response_headers = response.status, dict(response.getheaders())
                return status, response_headers, response.read()
            finally:
                connection.close()

        assert request("media", auth=False)[0] == 401
        checks.append("missing_auth_401")
        assert request("media")[2] == fixture.data
        checks.append("full_body_200")
        for value, expected in (
            ("bytes=0-43", fixture.data[:44]),
            ("bytes=100-", fixture.data[100:]),
            ("bytes=-10", fixture.data[-10:]),
        ):
            status, headers, body = request("media", range_value=value)
            assert status == 206 and body == expected
            assert headers["Accept-Ranges"] == "bytes" and headers["Cache-Control"] == "no-store"
        checks.append("bounded_open_suffix_range_206")
        for value in (f"bytes={len(fixture.data)}-", "bytes=1-0", "bytes=0-1,4-5", "bytes=-0"):
            status, headers, body = request("media", range_value=value)
            assert (
                status == 416
                and not body
                and headers["Content-Range"] == f"bytes */{len(fixture.data)}"
            )
        checks.append("invalid_range_416")
        assert request("media", method="HEAD")[2] == b""
        checks.append("head_no_body")
        assert request("slow", range_value="bytes=0-1")[2] == fixture.data[:2]
        import time

        slow_connection = HTTPConnection("127.0.0.1", service.primary.server_port, timeout=2)
        slow_started = time.monotonic()
        try:
            slow_connection.request(
                "GET",
                f"/slow/{fixture.identifier}",
                headers={"Authorization": "Bearer " + service.token, "Range": "bytes=0-"},
            )
            slow_response = slow_connection.getresponse()
            assert slow_response.status == 206
            assert slow_response.read(SLOW_PREFIX_BYTES) == fixture.data[:SLOW_PREFIX_BYTES]
            assert time.monotonic() - slow_started < 2
            assert slow_response.length == len(fixture.data) - SLOW_PREFIX_BYTES
            assert any(
                event["route"] == "slow" and event.get("body_delayed")
                for event in service.primary.snapshot()
            )
        finally:
            slow_connection.close()
        checks.append("slow_metadata_and_incomplete_prefix")
        assert request("redirect")[1]["Location"] == service.url("media", fixture)
        assert f":{service.sink.server_port}/sink" in request("cross")[1]["Location"]
        checks.append("same_and_cross_origin_redirect_targets")
        from http.client import IncompleteRead

        try:
            request("drop", range_value="bytes=0-1000")
        except IncompleteRead:
            checks.append("truncated_body_detected")
        else:
            raise AssertionError("TRUNCATION_NOT_DETECTED")
        assert service.sink.snapshot() == []
        assert all("Authorization" not in event for event in service.primary.snapshot())
        checks.append("sanitized_events_no_sink_request")
    for server in (service.primary, service.sink):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            assert connection.connect_ex(("127.0.0.1", server.server_port)) != 0
    checks.append("disposable_server_ports_closed")
    return {
        "status": "passed",
        "kind": "http_fixture_only",
        "checks": checks,
        "fixture_sha256": fixture.sha256,
    }
