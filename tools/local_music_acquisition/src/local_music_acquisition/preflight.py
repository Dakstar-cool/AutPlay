"""Bounded runtime checks which never print credentials or contact music providers."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .xray import XrayConfig


def _secret_status(path: Path | None) -> str:
    if path is None:
        return "not_configured"
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= 4096:
            return "invalid_file"
        if os.name != "nt" and stat.S_IMODE(details.st_mode) & 0o077:
            return "permissions_invalid"
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return "file_unavailable"
    return "configured"


def node_status() -> str:
    if shutil.which("node") is None:
        return "node_missing"
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", result.stdout)
        if result.returncode or match is None:
            return "node_version_unavailable"
        return "configured" if int(match.group(1)) >= 22 else "node_22_required"
    except (OSError, subprocess.SubprocessError):
        return "node_unavailable"


def check_runtime(options: argparse.Namespace) -> dict[str, object]:
    checks = {
        "ffmpeg": "configured" if shutil.which("ffmpeg") else "missing",
        "ffprobe": "configured" if shutil.which("ffprobe") else "missing",
        "jamendo": "disabled"
        if options.disable_jamendo
        else _secret_status(options.jamendo_client_id_file),
        "hitmo": "disabled" if options.disable_hitmo else "requires_running_local_cdp_browser",
        "yt_dlp": "disabled" if options.disable_yt_dlp else node_status(),
        "soundcloud": (
            _secret_status(options.soundcloud_client_id_file)
            if options.soundcloud_client_id_file
            else "configured"
        )
        if options.enable_soundcloud
        else "disabled",
        "bandcamp": "configured" if options.enable_bandcamp else "disabled",
        "yandex": "disabled"
        if options.yandex_token_file is None
        else _secret_status(options.yandex_token_file),
    }
    if any(
        getattr(options, f"{name}_requires_proxy", False)
        for name in ("yt_dlp", "soundcloud", "bandcamp")
    ):
        settings = XrayConfig(
            binary=options.xray_binary,
            config=options.xray_config,
            proxy_url=options.xray_proxy_url,
            startup_timeout=options.xray_startup_timeout,
            idle_timeout=options.xray_idle_timeout,
            stop_timeout=options.xray_stop_timeout,
        )
        checks["xray_binary"] = (
            "configured" if os.access(settings.binary, os.X_OK) else "unavailable"
        )
        checks["xray_config"] = (
            "configured"
            if settings.config.is_file() and os.access(settings.config, os.R_OK)
            else "unavailable"
        )
    return {"runtime": checks, "provider_network_verified": False}
