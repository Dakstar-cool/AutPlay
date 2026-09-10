from __future__ import annotations

from pathlib import Path

from local_music_acquisition.models import PlaylistItem
from local_music_acquisition.providers import hitmo_provider
from local_music_acquisition.providers.hitmo_provider import HitmoProvider


def test_hitmo_provider_forwards_the_cli_byte_budget(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def download(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "results": [
                {
                    "status": "downloaded",
                    "file_ref": "sha256:0123456789ab",
                }
            ]
        }

    monkeypatch.setattr(hitmo_provider.hitmo, "download_hitmo_tracks", download)
    provider = HitmoProvider(
        cdp_endpoint="http://127.0.0.1:9222",
        timeout_seconds=12,
        max_bytes=3 * 1024 * 1024,
    )

    provider.acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)

    assert captured["timeout_seconds"] == 12
    assert captured["max_bytes"] == 3 * 1024 * 1024
