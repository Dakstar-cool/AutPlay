"""Portable public SoundCloud client bootstrap; no account tokens or cookies."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .preflight import _secret_status


def valid_client_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{32}", value) is not None


def read_client_id(path: Path) -> str:
    if _secret_status(path) != "configured":
        raise ValueError("soundcloud_client_id_file_invalid")
    value = path.read_text(encoding="ascii").strip()
    if not valid_client_id(value):
        raise ValueError("soundcloud_client_id_file_invalid")
    return value


def main(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Refresh the public SoundCloud web client ID.")
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args(arguments)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "local_music_acquisition.providers._music_sites_worker"],
            input=json.dumps({"action": "refresh_client_id"}),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        response = json.loads(result.stdout)
        value = response.get("client_id")
        if (
            result.returncode
            or response.get("status") != "configured"
            or not valid_client_id(value)
        ):
            raise ValueError("soundcloud_client_id_refresh_failed")
        output = options.output.absolute()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({"error": "soundcloud_client_id_refresh_failed"}), file=sys.stderr)
        return 2
    print(json.dumps({"soundcloud_client_id": "configured"}))
    return 0
