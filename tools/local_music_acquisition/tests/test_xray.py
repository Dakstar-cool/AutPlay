from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from local_music_acquisition import xray
from local_music_acquisition.xray import XrayConfig, XrayError, XrayManager


class Process:
    def __init__(self):
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.waited = 0
        self.ignore_term = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.ignore_term:
            self.returncode = -15

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.waited += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("private config", timeout)
        return self.returncode


class Timer:
    def __init__(self, interval, callback, args):
        self.interval, self.callback, self.args = interval, callback, args
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Even a cancelled callback might already be waiting on the manager's lock.
        self.callback(*self.args)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    config = tmp_path / "private-config.json"
    config.write_text('{"private": "UUID-and-key-must-never-appear"}')
    settings = XrayConfig(binary=tmp_path / "xray", config=config)
    manager = XrayManager(settings)
    children, timers, calls = [], [], []
    real_spawn = subprocess.Popen

    def spawn(command, **kwargs):
        if command[0] != str(settings.binary):
            return real_spawn(command, **kwargs)
        calls.append((command, kwargs))
        child = Process()
        children.append(child)
        return child

    def timer(*args, **kwargs):
        result = Timer(*args, **kwargs)
        timers.append(result)
        return result

    monkeypatch.setattr(xray.subprocess, "Popen", spawn)
    monkeypatch.setattr(xray.threading, "Timer", timer)
    monkeypatch.setattr(manager, "_listening", lambda *args: bool(children))
    yield manager, children, timers, calls
    manager.close()


def test_lazy_start_waits_for_readiness_and_stops_after_idle(runtime, monkeypatch):
    manager, children, timers, calls = runtime
    probes = iter([False, False, True])
    monkeypatch.setattr(manager, "_listening", lambda *args: next(probes))
    assert children == timers == []
    with manager.lease() as url:
        assert url == "socks5h://127.0.0.1:10808"
        assert len(children) == 1
        assert timers == []
        command, options = calls[0]
        assert command == [str(manager.config.binary), "run", "-config", str(manager.config.config)]
        assert all(options[key] == subprocess.DEVNULL for key in ("stdin", "stdout", "stderr"))
        assert "UUID" not in repr(manager.config)
    assert timers[-1].interval == 60
    assert children[0].terminated == 0
    timers[-1].fire()
    assert children[0].terminated == children[0].waited == 1


def test_new_lease_reuses_process_and_stale_timer_cannot_stop_it(runtime):
    manager, children, timers, _ = runtime
    with manager.lease():
        pass
    old = timers[-1]
    with manager.lease():
        assert old.cancelled
        old.fire()
        assert children[0].terminated == 0
    old.fire()
    assert children[0].terminated == 0
    assert len(children) == 1
    timers[-1].fire()
    assert children[0].terminated == 1


def test_parallel_leases_launch_one_child_and_wait_for_last_download(runtime):
    manager, children, timers, _ = runtime
    entered = threading.Barrier(5)
    release = threading.Event()

    def download():
        with manager.lease():
            entered.wait(timeout=5)
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download) for _ in range(4)]
        try:
            entered.wait(timeout=5)
            assert len(children) == 1
            assert timers == []
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert len(timers) == 1
    timers[0].fire()
    assert children[0].terminated == 1


def test_download_exception_releases_lease_and_close_cancels_idle(runtime):
    manager, children, timers, _ = runtime
    with pytest.raises(ValueError, match="download failed"), manager.lease():
        raise ValueError("download failed")
    manager.close()
    manager.close()
    assert timers[0].cancelled
    timers[0].fire()
    assert children[0].terminated == 1
    with pytest.raises(XrayError, match="xray_manager_closed"), manager.lease():
        pass


def test_close_during_active_download_stops_only_owned_child(runtime):
    manager, children, timers, _ = runtime
    with manager.lease():
        manager.close()
        assert children[0].terminated == 1
    assert timers == []


def test_stop_escalates_to_kill_and_reaps_the_child(runtime):
    manager, children, _, _ = runtime
    with manager.lease():
        children[0].ignore_term = True
    manager.close()
    assert children[0].terminated == children[0].killed == 1
    assert children[0].waited == 2


def test_occupied_external_port_is_never_adopted_or_stopped(runtime, monkeypatch):
    manager, children, _, _ = runtime
    monkeypatch.setattr(manager, "_listening", lambda *args: True)
    with pytest.raises(XrayError, match="xray_port_in_use"), manager.lease():
        pass
    assert children == []


def test_spawn_failure_is_redacted_and_can_be_retried(runtime, monkeypatch):
    manager, children, _, _ = runtime
    spawn = xray.subprocess.Popen

    def unavailable(*args, **kwargs):
        raise OSError("UUID-and-key-must-never-appear")

    monkeypatch.setattr(xray.subprocess, "Popen", unavailable)
    with pytest.raises(XrayError) as error, manager.lease():
        pass
    assert str(error.value) == "xray_start_failed"
    assert error.value.__cause__ is None
    assert children == []
    monkeypatch.setattr(xray.subprocess, "Popen", spawn)
    with manager.lease():
        assert len(children) == 1


@pytest.mark.parametrize("exits", [False, True])
def test_startup_timeout_or_exit_reaps_child(runtime, monkeypatch, exits):
    manager, children, timers, _ = runtime
    manager.config = replace(manager.config, startup_timeout=0.02)

    def unavailable(*args):
        if children and exits:
            children[0].returncode = 23
        return False

    monkeypatch.setattr(manager, "_listening", unavailable)
    with (
        pytest.raises(XrayError, match="xray_exited" if exits else "xray_start_timeout"),
        manager.lease(),
    ):
        pytest.fail("provider must not start before readiness")
    assert len(children) == 1
    assert children[0].waited == 1
    assert children[0].returncode is not None
    assert timers == []


def test_crash_with_active_leases_does_not_spawn_replacement(runtime, monkeypatch):
    manager, children, _, _ = runtime
    with manager.lease():
        children[0].returncode = 1
        for _ in range(2):
            with pytest.raises(XrayError, match="xray_exited"), manager.lease():
                pass
        assert len(children) == 1
    monkeypatch.setattr(manager, "_listening", lambda *args: len(children) > 1)
    with manager.lease():
        assert len(children) == 2


@pytest.mark.parametrize("field", ["startup_timeout", "idle_timeout", "stop_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_timeouts_are_bounded(runtime, field, value):
    manager, *_ = runtime
    with pytest.raises(XrayError, match="timeout_invalid"):
        replace(manager.config, **{field: value})


@pytest.mark.parametrize(
    "url",
    [
        "socks5://127.0.0.1:10808",
        "http://127.0.0.1:10808",
        "socks5h://user:secret@127.0.0.1:10808",
        "socks5h://example.com:10808",
        "socks5h://127.0.0.1:0",
        "socks5h://127.0.0.1:65536",
        "socks5h://127.0.0.1:10808/path",
        "socks5h://127.0.0.1:10808?key=secret",
    ],
)
def test_proxy_url_rejects_nonlocal_or_secret_bearing_configuration(url):
    with pytest.raises(XrayError, match=r"^xray_proxy_url_invalid$"):
        xray.proxy_address(url)


def test_real_socket_readiness_idle_stop_and_restart(monkeypatch, tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = tmp_path / "config.json"
    config.write_text("{}")
    manager = XrayManager(
        XrayConfig(
            binary=Path(sys.executable),
            config=config,
            proxy_url=f"socks5h://127.0.0.1:{port}",
            idle_timeout=0.15,
            startup_timeout=5,
        )
    )
    children = []
    real_spawn = subprocess.Popen

    def spawn(command, **kwargs):
        child = real_spawn(
            [
                sys.executable,
                "-c",
                (
                    "import socket,time; time.sleep(0.1); s=socket.socket(); "
                    f"s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(30)"
                ),
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(xray.subprocess, "Popen", spawn)
    try:
        for _ in range(2):
            with manager.lease():
                assert manager._listening()
            assert children[-1].wait(timeout=5) != 0
            deadline = time.monotonic() + 2
            while manager._listening() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not manager._listening()
        assert len(children) == 2
    finally:
        manager.close()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
