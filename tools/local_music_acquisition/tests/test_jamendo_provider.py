from __future__ import annotations

import hashlib
import json
import subprocess
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


def test_search_skips_malformed_candidate_without_losing_valid_result() -> None:
    valid = {
        "id": "1",
        "name": "Title",
        "artist_name": "Artist",
        "album_name": "",
        "duration": 180,
        "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
        "shareurl": "https://www.jamendo.com/track/1",
        "audiodownload": "https://prod-1.storage.jamendo.com/download/track/1/mp32/",
        "audiodownload_allowed": True,
    }
    malformed = dict(valid, id="2", license_ccurl="")
    payload = json.dumps(
        {"headers": {"status": "success", "code": 0}, "results": [malformed, valid]}
    ).encode()

    assert jamendo.parse_search_response(payload, limit=2) == (_candidate("Artist", "Title"),)


def test_permission_refresh_keeps_strict_candidate_validation() -> None:
    payload = json.dumps(
        {
            "headers": {"status": "success", "code": 0},
            "results": [{"id": "1", "audiodownload_allowed": True}],
        }
    ).encode()

    with pytest.raises(jamendo.JamendoToolError, match="search_response_invalid"):
        jamendo.parse_search_response(payload, limit=1, strict_candidates=True)


def test_jamendo_transport_retries_once_with_bounded_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = object.__new__(jamendo.CurlTransport)
    transport._client_id = "client-id"
    transport._curl = "curl"
    transport._timeout_seconds = 10
    transport._last_request_started_at = None
    captured: dict[str, object] = {}

    def run(arguments, **options):
        captured["arguments"] = arguments
        captured["timeout"] = options["timeout"]
        Path(arguments[arguments.index("--output") + 1]).write_bytes(b"{}")
        return subprocess.CompletedProcess(arguments, 0, "application/json\n200", "")

    monkeypatch.setattr(subprocess, "run", run)
    destination = tmp_path / "response.json"

    transport._request_to_file(
        "https://api.jamendo.com/v3.0/tracks/",
        destination,
        parameters=(("format", "json"),),
        accept="application/json",
        max_bytes=1024,
        failure_code="search_request_failed",
    )

    arguments = captured["arguments"]
    assert "--http1.1" in arguments
    assert arguments[arguments.index("--retry") + 1] == "1"
    assert captured["timeout"] == 24
