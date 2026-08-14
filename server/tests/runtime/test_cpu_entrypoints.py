"""Fresh-process CPU-only import and API start smoke tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

DATABASE_URL = "postgresql+psycopg://runtime:runtime-password@127.0.0.1:1/autplay"
AUTH_SECRET = "runtime-subprocess-signing-secret-at-least-32-bytes"


@dataclass
class RunningApi:
    process: subprocess.Popen[str]
    stdout: str = ""
    stderr: str = ""


def _runtime_environment(*, port: int | None = None) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        {
            "AUTPLAY_DATABASE_URL": DATABASE_URL,
            "AUTPLAY_AUTH_SIGNING_SECRET": AUTH_SECRET,
            "AUTPLAY_PROFILE": "test",
        }
    )
    if port is not None:
        environment["AUTPLAY_API_HOST"] = "127.0.0.1"
        environment["AUTPLAY_API_PORT"] = str(port)
    return environment


def test_fresh_api_import_has_no_accelerator_side_effects() -> None:
    script = """
import importlib
import sys

for name in (
    "autplay.runtime.settings",
    "autplay.runtime.logging",
    "autplay.runtime.metrics",
    "autplay.adapters.postgresql.readiness",
    "autplay.adapters.postgresql.jobs_runtime",
    "autplay.application.job_worker",
    "autplay.entrypoints.api",
    "autplay.entrypoints.worker_cpu",
):
    importlib.import_module(name)

prohibited = sorted(
    name for name in sys.modules
    if name.split(".", 1)[0] in {"cupy", "cuda", "jax", "tensorflow", "torch"}
)
if prohibited:
    raise SystemExit(f"accelerator imports: {prohibited}")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=_runtime_environment(),
    )

    assert result.returncode == 0, result.stderr


def test_api_config_check_is_locked_and_sanitized() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.api", "--check-config"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=_runtime_environment(),
    )

    assert result.returncode == 0
    assert result.stdout == '{"status":"ok","service":"autplay-api"}\n'
    assert AUTH_SECRET not in result.stdout + result.stderr
    assert "runtime-password" not in result.stdout + result.stderr


def test_worker_config_check_succeeds_but_database_preflight_fails_closed() -> None:
    environment = _runtime_environment()
    environment.pop("AUTPLAY_AUTH_SIGNING_SECRET")
    checked = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.worker_cpu", "--check-config"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    started = subprocess.run(
        [sys.executable, "-m", "autplay.entrypoints.worker_cpu", "--once"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )

    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == '{"status":"ok","service":"autplay-worker-cpu"}\n'
    assert started.returncode == 3
    assert started.stdout == ""
    assert '"event":"database_unavailable"' in started.stderr
    assert "runtime-password" not in checked.stdout + checked.stderr
    assert "runtime-password" not in started.stdout + started.stderr


def test_api_process_serves_liveness_while_database_is_down() -> None:
    port = _unused_loopback_port()
    with _running_api(port) as running:
        response_body = _wait_for_liveness(port, running.process)

    assert response_body == '{"status":"live","component":"api"}'
    output = running.stdout + running.stderr
    assert AUTH_SECRET not in output
    assert "runtime-password" not in output


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return int(port)


@contextmanager
def _running_api(port: int) -> Iterator[RunningApi]:
    process = subprocess.Popen(
        [sys.executable, "-m", "autplay.entrypoints.api"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_runtime_environment(port=port),
    )
    running = RunningApi(process)
    try:
        yield running
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
        running.stdout = stdout
        running.stderr = stderr


def _wait_for_liveness(port: int, process: subprocess.Popen[str]) -> str:
    endpoint = f"http://127.0.0.1:{port}/health/live"
    deadline = time.monotonic() + 15.0
    last_error = "API did not start"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(f"API exited early ({process.returncode}): {stdout}\n{stderr}")
        try:
            with urllib.request.urlopen(endpoint, timeout=1.0) as response:
                return cast(bytes, response.read()).decode("utf-8")
        except (OSError, urllib.error.URLError) as error:
            last_error = type(error).__name__
            time.sleep(0.1)
    raise AssertionError(f"{last_error}: {endpoint}")
