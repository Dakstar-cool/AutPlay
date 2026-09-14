"""Fail-closed yt-dlp contour with a private subprocess boundary."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from ..matching import candidate_matches as candidate_matches
from ..models import (
    PROVIDER_MISS_CODES,
    AcquiredArtifact,
    PlaylistItem,
    ProviderFailure,
    ProviderMiss,
)
from ..source_catalog import SourceCatalog
from ..source_client import read_client_id
from ..xray import XrayError, XrayManager

_SAFE_CODE = re.compile(r"[a-z0-9_.-]{1,100}")

if sys.platform == "win32":
    from subprocess import CREATE_NEW_PROCESS_GROUP as _CREATE_NEW_PROCESS_GROUP
else:
    _CREATE_NEW_PROCESS_GROUP = 0


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Synchronously stop the worker and all descendants before returning."""

    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is None or completed.returncode != 0:
            process.kill()
            process.wait()
            raise RuntimeError("yt_dlp_process_tree_termination_failed")
    else:
        try:
            kill_process_group = getattr(os, "killpg", None)
            signal_kill = getattr(signal, "SIGKILL", None)
            if not callable(kill_process_group) or not isinstance(signal_kill, int):
                raise OSError("POSIX process-group termination is unavailable")
            kill_process_group(process.pid, signal_kill)
        except ProcessLookupError:
            pass
        except OSError as error:
            process.kill()
            process.wait()
            raise RuntimeError("yt_dlp_process_tree_termination_failed") from error
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class YtDlpProvider:
    """Search twenty YouTube results and acquire only a strict exact identity match."""

    name = "yt_dlp"
    requires_rights_confirmation = True
    worker_module = "local_music_acquisition.providers._yt_dlp_worker"

    def __init__(
        self,
        *,
        timeout_seconds: float = 300.0,
        max_bytes: int = 200 * 1024 * 1024,
        source_catalog: SourceCatalog | None = None,
        client_id_file: Path | None = None,
        requires_proxy: bool = False,
        xray: XrayManager | None = None,
    ) -> None:
        if not 10 <= timeout_seconds <= 600:
            raise ValueError(f"{self.name}_timeout_invalid")
        if not 1024 <= max_bytes <= 1024 * 1024 * 1024:
            raise ValueError(f"{self.name}_max_bytes_invalid")
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._source_catalog = source_catalog or SourceCatalog()
        if client_id_file is not None and self.name != "soundcloud":
            raise ValueError("client_id_provider_invalid")
        self._client_id = read_client_id(client_id_file) if client_id_file else None
        self.requires_proxy = requires_proxy
        self._xray = xray

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        if not self.requires_proxy:
            return self._acquire(item, output_directory)
        if self._xray is None:
            raise ProviderFailure(self.name, "xray_not_configured")
        try:
            with self._xray.lease() as proxy_url:
                return self._acquire(item, output_directory, proxy_url=proxy_url)
        except XrayError as error:
            raise ProviderFailure(self.name, str(error)) from None

    def _acquire(
        self, item: PlaylistItem, output_directory: Path, *, proxy_url: str | None = None
    ) -> AcquiredArtifact:
        source = self._source_catalog.find(self.name, item)
        expected_duration = item.expected_duration_seconds
        if source:
            if (
                expected_duration is not None
                and source.duration_seconds is not None
                and abs(expected_duration - source.duration_seconds) > 0.01
            ):
                raise ProviderFailure(self.name, "source_duration_conflict")
            expected_duration = expected_duration or source.duration_seconds
        request = {
            "provider": self.name,
            "artist": item.artist,
            "title": item.title,
            "output_directory": str(output_directory.resolve()),
            "max_bytes": self._max_bytes,
            "expected_duration_seconds": expected_duration,
        }
        if source:
            request["source_url"] = source.url
        if self._client_id:
            request["soundcloud_client_id"] = self._client_id
        if proxy_url is not None:
            request["proxy_url"] = proxy_url
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
        }
        environment.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "YTDLP_NO_PLUGINS": "1",
            }
        )
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", self.worker_module],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                creationflags=_CREATE_NEW_PROCESS_GROUP,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise ProviderFailure(self.name, "worker_unavailable") from error
        try:
            stdout, _stderr = process.communicate(
                json.dumps(request), timeout=self._timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            try:
                _terminate_process_tree(process)
            except RuntimeError as termination_error:
                raise ProviderFailure(
                    self.name, "process_tree_termination_failed"
                ) from termination_error
            raise ProviderFailure(self.name, "timeout") from error
        except BaseException:
            _terminate_process_tree(process)
            raise
        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise ProviderFailure(self.name, "worker_response_invalid") from error
        if process.returncode != 0 or not isinstance(response, dict):
            raise ProviderFailure(self.name, "worker_response_invalid")
        code = response.get("code")
        if (
            response.get("status") == "miss"
            and isinstance(code, str)
            and code in PROVIDER_MISS_CODES
        ):
            raise ProviderMiss(self.name, code)
        if response.get("status") != "downloaded":
            code = response.get("code")
            if not isinstance(code, str) or _SAFE_CODE.fullmatch(code) is None:
                code = "worker_failed"
            raise ProviderFailure(self.name, code)
        artifact_ref = response.get("artifact_ref")
        if (
            not isinstance(artifact_ref, str)
            or re.fullmatch(r"sha256:[0-9a-f]{12}", artifact_ref) is None
        ):
            raise ProviderFailure(self.name, "worker_response_invalid")
        expected = response.get("expected_duration_seconds", expected_duration)
        if expected is not None and (
            isinstance(expected, bool)
            or not isinstance(expected, (int, float))
            or not math.isfinite(expected)
            or expected <= 0
        ):
            raise ProviderFailure(self.name, "worker_response_invalid")
        return AcquiredArtifact(
            self.name,
            artifact_ref,
            identity_version=(
                f"reviewed-source-v1:{source.fingerprint}" if source else "recording-match-v2"
            ),
            expected_duration_seconds=expected_duration or expected,
        )
