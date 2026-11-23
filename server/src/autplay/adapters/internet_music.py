"""Search the public songs catalogue and acquire one verified audio track."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.provider_child import ProviderChildError, download_youtube
from autplay.adapters.filesystem.provider_media import ProviderMediaError, po_token_url


class InternetMusicProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        self.retryable = code not in {
            "provider_format_unsupported",
            "provider_runtime_unsupported",
            "provider_source_unavailable",
            "provider_track_policy_rejected",
            "provider_token_configuration_invalid",
            "provider_download_too_large",
        }
        super().__init__(code)


class InternetMusicProvider:
    def search(self, query: str) -> list[dict[str, Any]]:
        launcher, environment = provider_child_launch()
        result = subprocess.run(
            [launcher[0], "-I", "-m", "autplay.adapters.youtube_music_catalog", query],
            capture_output=True,
            timeout=35,
            check=True,
            env=environment,
        )
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("music_search_response_too_large")
        candidates: list[dict[str, Any]] = json.loads(result.stdout)
        return candidates

    def download(self, candidate_id: str, directory: Path) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate_id):
            raise ValueError("music_candidate_invalid")
        try:
            po_token_url(required=True)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            return download_youtube(candidate_id, directory, 2 * 1024**3)
        except (ProviderChildError, ProviderMediaError) as error:
            raise InternetMusicProviderError(error.code) from error
