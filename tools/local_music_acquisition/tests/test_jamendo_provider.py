from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_music_acquisition.models import PlaylistItem, ProviderMiss
from local_music_acquisition.providers import jamendo
from local_music_acquisition.providers.jamendo_provider import JamendoProvider


def _candidate(artist: str, title: str) -> jamendo.TrackCandidate:
    return jamendo.TrackCandidate(
        "1",
        title,
        artist,
        None,
        180,
        "https://creativecommons.org/licenses/by/4.0/",
        "https://www.jamendo.com/track/1",
        "https://prod-1.storage.jamendo.com/download/track/1/mp32/",
    )


def _provider() -> JamendoProvider:
    provider = object.__new__(JamendoProvider)
    provider._transport = object()
    provider._limit = 5
    provider._max_bytes = 1024 * 1024
    return provider


def test_jamendo_provider_downloads_ranked_exact_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _candidate("Artist", "Title")
    audio_path = tmp_path / "Artist - Title.mp3"
    audio_path.write_bytes(b"ID3-jamendo-fixture")
    monkeypatch.setattr(jamendo, "search_tracks", lambda *args, **kwargs: (candidate,))
    captured: dict[str, object] = {}

    def download(ranked, *_args, **_kwargs):
        captured["ranked"] = ranked
        return SimpleNamespace(audio_path=audio_path)

    monkeypatch.setattr(jamendo, "download_track", download)

    artifact = _provider().acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)

    assert isinstance(captured["ranked"], jamendo.RankedTrack)
    assert artifact.provider == "jamendo"
    assert artifact.artifact_ref == (
        "sha256:" + hashlib.sha256(audio_path.read_bytes()).hexdigest()[:12]
    )


def test_jamendo_provider_uses_only_exact_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        jamendo, "search_tracks", lambda *args, **kwargs: (_candidate("Other", "Title"),)
    )

    with pytest.raises(ProviderMiss, match=r"jamendo\.exact_match_not_found"):
        _provider().acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)
