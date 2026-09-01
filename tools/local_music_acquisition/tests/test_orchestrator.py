from __future__ import annotations

from pathlib import Path

import pytest

from local_music_acquisition.models import AcquiredArtifact, ProviderFailure, ProviderMiss
from local_music_acquisition.orchestrator import PlaylistDownloadError, download_playlist


class FakeProvider:
    requires_rights_confirmation = False

    def __init__(self, name: str, actions: dict[str, str], events: list[str]) -> None:
        self.name = name
        self.actions = actions
        self.events = events

    def acquire(self, item, _output: Path) -> AcquiredArtifact:
        self.events.append(f"{self.name}:{item.title}")
        action = self.actions.get(item.title, "ok")
        if action == "miss":
            raise ProviderMiss(self.name, "exact_match_not_found")
        if action == "fail":
            raise ProviderFailure(self.name, "provider_failed")
        return AcquiredArtifact(self.name, "sha256:0123456789ab")


def _playlist(tmp_path: Path, text: str = "Artist - Track\n") -> Path:
    path = tmp_path / "tracks.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_three_contours_are_strictly_ordered(tmp_path: Path) -> None:
    events: list[str] = []
    providers = (
        FakeProvider("jamendo", {"Track": "miss"}, events),
        FakeProvider("hitmo", {"Track": "miss"}, events),
        FakeProvider("yt_dlp", {}, events),
    )

    summary = download_playlist(_playlist(tmp_path), tmp_path / "music", providers=providers)

    assert events == ["jamendo:Track", "hitmo:Track", "yt_dlp:Track"]
    assert summary["yt_dlp_downloaded"] == 1
    assert summary["outcomes"][0]["fallback_used"] is True


def test_provider_failure_never_opens_next_contour(tmp_path: Path) -> None:
    events: list[str] = []
    providers = (
        FakeProvider("jamendo", {"Track": "miss"}, events),
        FakeProvider("hitmo", {"Track": "fail"}, events),
        FakeProvider("yt_dlp", {}, events),
    )

    summary = download_playlist(_playlist(tmp_path), tmp_path / "music", providers=providers)

    assert events == ["jamendo:Track", "hitmo:Track"]
    assert summary["outcomes"][0]["error_code"] == "hitmo.provider_failed"


@pytest.mark.parametrize(
    ("miss_provider", "miss_code"),
    [("other", "exact_match_not_found"), ("hitmo", "download_failed")],
)
def test_only_current_provider_exact_miss_opens_next_contour(
    tmp_path: Path, miss_provider: str, miss_code: str
) -> None:
    events: list[str] = []

    class InvalidMiss(FakeProvider):
        def acquire(self, item, _output: Path) -> AcquiredArtifact:
            events.append(f"{self.name}:{item.title}")
            raise ProviderMiss(miss_provider, miss_code)

    providers = (InvalidMiss("hitmo", {}, events), FakeProvider("yt_dlp", {}, events))

    summary = download_playlist(_playlist(tmp_path), tmp_path / "music", providers=providers)

    assert events == ["hitmo:Track"]
    assert summary["outcomes"][0]["error_code"] == "hitmo.result_invalid"


def test_rights_preflight_happens_before_file_or_provider_io(tmp_path: Path) -> None:
    events: list[str] = []
    provider = FakeProvider("yt_dlp", {}, events)
    provider.requires_rights_confirmation = True

    with pytest.raises(PlaylistDownloadError, match="yt_dlp_rights_confirmation_required"):
        download_playlist(tmp_path / "missing.txt", tmp_path / "music", providers=(provider,))

    assert events == []


def test_malformed_row_isolated_and_next_row_runs(tmp_path: Path) -> None:
    events: list[str] = []
    provider = FakeProvider("jamendo", {}, events)

    summary = download_playlist(
        _playlist(tmp_path, "bad row\nArtist - Track\n"),
        tmp_path / "music",
        providers=(provider,),
    )

    assert summary["malformed"] == 1
    assert events == ["jamendo:Track"]
