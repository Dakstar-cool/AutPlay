from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX container entrypoint")
def test_stop_during_browser_startup_never_launches_worker(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"
    if not script.exists():
        script = Path("/usr/local/bin/acquisition-entrypoint")
    marker = tmp_path / "browser-started"
    for name, body in {
        "chromium": '#!/bin/sh\ntouch "$START_MARKER"\nexec sleep 30\n',
        "curl": "#!/bin/sh\nsleep 0.1\nexit 1\n",
    }.items():
        executable = tmp_path / name
        executable.write_text(body)
        executable.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "START_MARKER": str(marker),
    }
    process = subprocess.Popen(
        ["sh", str(script), "missing-playlist"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, (stdout, stderr)
        assert stdout == b""
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
