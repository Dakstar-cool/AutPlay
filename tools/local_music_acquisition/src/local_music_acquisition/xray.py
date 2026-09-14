"""Lazy, process-owned SOCKS transport shared by concurrent acquisition providers."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

if sys.platform == "win32":
    from subprocess import CREATE_NO_WINDOW as _CREATE_NO_WINDOW
else:
    _CREATE_NO_WINDOW = 0


class XrayError(RuntimeError):
    """Stable error codes only; never include subprocess output or configuration."""


def proxy_address(value: object) -> tuple[str, int]:
    """Accept only an unauthenticated local SOCKS5 endpoint with remote DNS."""
    try:
        if not isinstance(value, str):
            raise ValueError
        url = urlsplit(value)
        if (
            url.scheme != "socks5h"
            or url.hostname != "127.0.0.1"
            or url.port is None
            or not 1 <= url.port <= 65535
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
            or value != f"socks5h://127.0.0.1:{url.port}"
        ):
            raise ValueError
        return "127.0.0.1", url.port
    except ValueError:
        raise XrayError("xray_proxy_url_invalid") from None


@dataclass(frozen=True, slots=True)
class XrayConfig:
    binary: Path = field(default=Path("/usr/local/bin/xray"), repr=False)
    config: Path = field(default=Path("/usr/local/etc/xray/config.json"), repr=False)
    proxy_url: str = field(default="socks5h://127.0.0.1:10808", repr=False)
    startup_timeout: float = 15.0
    idle_timeout: float = 60.0
    stop_timeout: float = 5.0

    def __post_init__(self) -> None:
        proxy_address(self.proxy_url)
        for name, value, maximum in (
            ("startup", self.startup_timeout, 120),
            ("idle", self.idle_timeout, 3600),
            ("stop", self.stop_timeout, 30),
        ):
            if not 0 < value <= maximum:
                raise XrayError(f"xray_{name}_timeout_invalid")
        if not self.binary.is_absolute() or not self.config.is_absolute():
            raise XrayError("xray_paths_must_be_absolute")


class XrayManager:
    """One owner per acquisition runtime; construction performs no I/O or process start.

    All transitions, including readiness and stop, hold the same lock. An occupied
    external port is rejected: a listening socket alone cannot prove ownership.
    """

    def __init__(self, config: XrayConfig) -> None:
        self.config = config
        self._address = proxy_address(config.proxy_url)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._active = 0
        self._idle: threading.Timer | None = None
        self._generation = 0
        self._closed = False

    @contextmanager
    def lease(self) -> Iterator[str]:
        """Keep the owned process alive throughout a provider's network activity."""
        with self._lock:
            if self._closed:
                raise XrayError("xray_manager_closed")
            self._cancel_idle_locked()
            try:
                self._start_locked()
            except BaseException:
                # A failed readiness check must not strand a child process.
                if self._active == 0:
                    self._stop_locked()
                raise
            self._active += 1
        try:
            yield self.config.proxy_url
        finally:
            with self._lock:
                self._active -= 1
                if self._active == 0 and not self._closed and self._process is not None:
                    generation = self._generation
                    self._idle = threading.Timer(
                        self.config.idle_timeout, self._expire_idle, args=(generation,)
                    )
                    self._idle.daemon = True
                    self._idle.start()

    def _listening(self, timeout: float = 0.1) -> bool:
        try:
            with socket.create_connection(self._address, timeout=timeout):
                return True
        except OSError:
            return False

    def _start_locked(self) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                if not self._listening():
                    raise XrayError("xray_proxy_unavailable")
                return
            self._process.wait()
            self._process = None
            # Do not replace a process while previous downloads still hold leases.
            if self._active:
                raise XrayError("xray_exited")
        if self._active:
            raise XrayError("xray_exited")
        if self._listening():
            raise XrayError("xray_port_in_use")
        try:
            # Only check readability; config bytes and Xray output never enter logs.
            with self.config.config.open("rb"):
                pass
            self._process = subprocess.Popen(
                [str(self.config.binary), "run", "-config", str(self.config.config)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
                start_new_session=os.name != "nt",
            )
        except OSError:
            raise XrayError("xray_start_failed") from None
        deadline = time.monotonic() + self.config.startup_timeout
        while True:
            if self._process.poll() is not None:
                raise XrayError("xray_exited")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise XrayError("xray_start_timeout")
            if self._listening(min(0.1, remaining)):
                if self._process.poll() is not None:
                    raise XrayError("xray_exited")
                return
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def _cancel_idle_locked(self) -> None:
        self._generation += 1
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    def _expire_idle(self, generation: int) -> None:
        with self._lock:
            # Timer.cancel() alone cannot retract a callback already waiting for the lock.
            if generation != self._generation or self._active or self._closed:
                return
            self._idle = None
            # Retain ownership on failure; the next lease/close can retry cleanup safely.
            with suppress(XrayError):
                self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=self.config.stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.config.stop_timeout)
        except (OSError, subprocess.SubprocessError):
            raise XrayError("xray_stop_failed") from None
        self._process = None

    def close(self) -> None:
        """Stop only our retained child handle; safe to call more than once."""
        with self._lock:
            self._closed = True
            self._cancel_idle_locked()
            self._stop_locked()
