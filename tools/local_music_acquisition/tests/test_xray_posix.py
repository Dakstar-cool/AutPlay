from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX server signals/launcher")


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_real_cli_signal_waits_for_download_and_reaps_xray(tmp_path, signum):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    binary, config = tmp_path / "xray", tmp_path / "config.json"
    child_pid, active = tmp_path / "child.pid", tmp_path / "download.active"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os,socket,sys,time\nfrom pathlib import Path\n"
        f"Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "print('secret-UUID', flush=True)\n"
        "print('secret-key', file=sys.stderr, flush=True)\n"
        f"s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen()\n"
        "time.sleep(30)\n"
    )
    binary.chmod(0o700)
    config.write_text("{}")
    playlist = tmp_path / "playlist.txt"
    playlist.write_text("Artist - One\nArtist - Two\n")
    script = (
        "import sys,time\nfrom pathlib import Path\n"
        "from local_music_acquisition import cli\n"
        "from local_music_acquisition.models import AcquiredArtifact\n"
        "from local_music_acquisition.providers.music_sites import SoundCloudProvider\n"
        "def acquire(self,item,output,*,proxy_url):\n"
        f" Path({str(active)!r}).write_text('active')\n"
        " time.sleep(0.5)\n"
        " return AcquiredArtifact(self.name,'sha256:0123456789ab')\n"
        "SoundCloudProvider._acquire=acquire\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
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
            str(binary),
            "--xray-config",
            str(config),
            "--xray-proxy-url",
            f"socks5h://127.0.0.1:{port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not active.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert active.exists()
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 1, stderr
        report = json.loads(stdout)
        assert report["downloaded"] == 1
        assert report["outcomes"][1]["error_code"] == "download_interrupted"
        assert "secret" not in stdout + stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_docker_launcher_mounts_existing_xray_without_host_network(tmp_path):
    script = Path(__file__).parents[1] / "docker" / "run-queue.sh"
    arguments = tmp_path / "docker-args.json"
    binary = tmp_path / "docker"
    binary.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
        f"Path({str(arguments)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    binary.chmod(0o700)
    for name in ("playlist.txt", "seccomp.json", "xray-config.json"):
        (tmp_path / name).write_text("{}")
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ACQUISITION_IMAGE": "fixture-image",
        "ACQUISITION_SECCOMP": str(tmp_path / "seccomp.json"),
        "ACQUISITION_PROXY_PROVIDERS": "yt_dlp,soundcloud,bandcamp",
        "ACQUISITION_XRAY_BINARY": sys.executable,
        "ACQUISITION_XRAY_CONFIG": str(tmp_path / "xray-config.json"),
        "ACQUISITION_CONTAINER_NAME": "fixture-queue",
        "ACQUISITION_UID": "1000",
        "ACQUISITION_GID": "1001",
    }
    result = subprocess.run(
        [
            "bash",
            str(script),
            str(tmp_path / "playlist.txt"),
            str(tmp_path / "queue"),
            str(tmp_path / "music"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(arguments.read_text())
    assert args[args.index("--user") + 1] == "1000:1001"
    assert args[args.index("--name") + 1] == "fixture-queue"
    assert args[args.index("--xray-proxy-url") + 1] == "socks5h://127.0.0.1:10808"
    assert args[args.index("--xray-idle-timeout") + 1] == "60"
    assert args[args.index("--xray-config") + 1] == "/run/secrets/xray-config.json"
    assert any("dst=/run/secrets/xray-config.json,readonly" in arg for arg in args)
    assert all(f"--{site}-requires-proxy" in args for site in ("yt-dlp", "soundcloud", "bandcamp"))
    assert "--network" not in args and "--privileged" not in args
