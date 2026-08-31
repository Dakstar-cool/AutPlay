from __future__ import annotations

import errno
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import BinaryIO

import pytest


def _load_jamendo_download() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.jamendo_download")
    finally:
        sys.path.remove(str(repository_root))


jamendo_download = _load_jamendo_download()


def _response(*results: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "headers": {"status": "success", "code": 0, "results_count": len(results)},
            "results": list(results),
        }
    ).encode()


def _track(
    *,
    track_id: str,
    title: str,
    artist: str,
    allowed: bool,
    license_url: str = "https://creativecommons.org/licenses/by-nc-sa/4.0/",
) -> dict[str, object]:
    return {
        "id": track_id,
        "name": title,
        "artist_name": artist,
        "album_name": "Test Album",
        "duration": 209,
        "license_ccurl": license_url,
        "shareurl": f"https://www.jamendo.com/track/{track_id}",
        "audiodownload_allowed": allowed,
        "audiodownload": (
            f"https://prod-1.storage.jamendo.com/download/track/{track_id}/mp32/" if allowed else ""
        ),
    }


class FakeSearchTransport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, int]] = []

    def fetch_search_response(self, query: str, *, limit: int) -> bytes:
        self.calls.append((query, limit))
        return self.payload


class FakeDownloadTransport:
    def __init__(
        self,
        payload: bytes,
        fresh_response: bytes,
        content_type: str = "audio/mpeg",
    ) -> None:
        self.payload = payload
        self.fresh_response = fresh_response
        self.content_type = content_type
        self.calls: list[str] = []
        self.refresh_calls: list[str] = []

    def fetch_track_response(self, track_id: str) -> bytes:
        self.refresh_calls.append(track_id)
        return self.fresh_response

    def download_audio(
        self,
        download_url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> object:
        assert len(self.payload) <= max_bytes
        self.calls.append(download_url)
        destination.write_bytes(self.payload)
        return jamendo_download.TransferReceipt(self.content_type, len(self.payload))


def test_search_keeps_only_artist_authorized_downloads() -> None:
    transport = FakeSearchTransport(
        _response(
            _track(track_id="10", title="Allowed Song", artist="Open Artist", allowed=True),
            _track(track_id="11", title="Stream Only", artist="Other Artist", allowed=False),
        )
    )

    candidates = jamendo_download.search_tracks(
        "  Open Artist   Allowed Song ", transport=transport
    )

    assert transport.calls == [("Open Artist Allowed Song", 20)]
    assert [(item.track_id, item.title, item.duration) for item in candidates] == [
        ("10", "Allowed Song", "03:29")
    ]


def test_search_rejects_downloadable_track_without_valid_license() -> None:
    transport = FakeSearchTransport(
        _response(
            _track(
                track_id="10",
                title="Allowed Song",
                artist="Open Artist",
                allowed=True,
                license_url="https://attacker.example/license",
            )
        )
    )

    with pytest.raises(jamendo_download.JamendoToolError, match="license_url_invalid"):
        jamendo_download.search_tracks("Allowed Song", transport=transport)


def test_search_rejects_download_url_outside_exact_storage_host_pattern() -> None:
    raw_track = _track(track_id="10", title="Allowed Song", artist="Open Artist", allowed=True)
    raw_track["audiodownload"] = "https://attacker.example/download/track/10/mp32/"

    with pytest.raises(jamendo_download.JamendoToolError, match="download_url_invalid"):
        jamendo_download.parse_search_response(_response(raw_track), limit=1)


@pytest.mark.parametrize(
    ("provider_code", "expected_error"),
    [
        (5, "provider_client_id_invalid"),
        (6, "provider_rate_limit_exceeded"),
        (11, "provider_application_suspended"),
        (99, "provider_response_failed"),
    ],
)
def test_search_reports_safe_provider_failure_code(provider_code: int, expected_error: str) -> None:
    payload = json.dumps(
        {"headers": {"status": "failed", "code": provider_code}, "results": []}
    ).encode()

    with pytest.raises(jamendo_download.JamendoToolError, match=expected_error):
        jamendo_download.parse_search_response(payload, limit=20)


def test_ranking_prefers_artist_and_exact_title() -> None:
    candidates = jamendo_download.parse_search_response(
        _response(
            _track(track_id="10", title="Morning Light", artist="Open Artist", allowed=True),
            _track(track_id="11", title="Morning", artist="Another Artist", allowed=True),
        ),
        limit=20,
    )

    selected = jamendo_download.select_track("Open Artist - Morning Light", candidates)

    assert selected.candidate.track_id == "10"
    assert selected.score > 0.8


def test_download_publishes_mp3_and_attribution_without_overwriting(tmp_path: Path) -> None:
    payload = b"ID3" + (b"a" * 4096)
    fresh_response = _response(
        _track(track_id="10", title="Morning: Light", artist="Open / Artist", allowed=True)
    )
    transport = FakeDownloadTransport(payload, fresh_response)
    candidate = jamendo_download.TrackCandidate(
        "10",
        "Morning: Light",
        "Open / Artist",
        "Test Album",
        209,
        "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "https://www.jamendo.com/track/10",
        "https://prod-1.storage.jamendo.com/download/track/10/mp32/",
    )
    ranked = jamendo_download.RankedTrack(candidate, 1.0)
    existing = tmp_path / "Open _ Artist - Morning_ Light.mp3"
    existing.write_bytes(b"existing")

    result = jamendo_download.download_track(ranked, tmp_path, transport=transport)

    assert transport.refresh_calls == ["10"]
    assert transport.calls == ["https://prod-1.storage.jamendo.com/download/track/10/mp32/"]
    assert result.audio_path.name == "Open _ Artist - Morning_ Light (2).mp3"
    assert result.audio_path.read_bytes() == payload
    attribution = json.loads(result.attribution_path.read_text(encoding="utf-8"))
    assert attribution == {
        "album": "Test Album",
        "artist": "Open / Artist",
        "download_permission": "audiodownload_allowed=true",
        "duration_seconds": 209,
        "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "provider": "Jamendo",
        "provider_api_version": "3.0",
        "provider_track_id": "10",
        "share_url": "https://www.jamendo.com/track/10",
        "title": "Morning: Light",
    }
    assert existing.read_bytes() == b"existing"
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_html_and_cleans_temporary_files(tmp_path: Path) -> None:
    fresh_response = _response(_track(track_id="10", title="Track", artist="Artist", allowed=True))
    transport = FakeDownloadTransport(
        b"<html>blocked</html>" + (b" " * 2048), fresh_response, "text/html"
    )
    candidate = jamendo_download.TrackCandidate(
        "10",
        "Track",
        "Artist",
        None,
        120,
        "https://creativecommons.org/licenses/by/4.0/",
        "https://www.jamendo.com/track/10",
        "https://prod-1.storage.jamendo.com/download/track/10/mp32/",
    )

    with pytest.raises(jamendo_download.JamendoToolError, match="downloaded_content_invalid"):
        jamendo_download.download_track(
            jamendo_download.RankedTrack(candidate, 1.0),
            tmp_path,
            transport=transport,
        )

    assert list(tmp_path.iterdir()) == []


def test_download_refuses_revoked_permission_before_requesting_audio(tmp_path: Path) -> None:
    revoked = _response(_track(track_id="10", title="Track", artist="Artist", allowed=False))
    transport = FakeDownloadTransport(b"ID3" + (b"a" * 4096), revoked)
    candidate = jamendo_download.parse_search_response(
        _response(_track(track_id="10", title="Track", artist="Artist", allowed=True)),
        limit=1,
    )[0]

    with pytest.raises(jamendo_download.JamendoToolError, match="download_permission_revoked"):
        jamendo_download.download_track(
            jamendo_download.RankedTrack(candidate, 1.0),
            tmp_path,
            transport=transport,
        )

    assert transport.refresh_calls == ["10"]
    assert transport.calls == []
    assert list(tmp_path.iterdir()) == []


def test_pair_publication_never_deletes_a_racing_foreign_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_source = tmp_path / "audio.part"
    audio_source.write_bytes(b"audio")
    attribution_source = tmp_path / "attribution.part"
    attribution_source.write_bytes(b"attribution")
    call_count = 0

    def publish_with_race(source: Path, destination: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            destination.write_bytes(b"foreign")
            raise FileExistsError(destination)
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(jamendo_download, "_publish_one", publish_with_race)

    audio_path, attribution_path = jamendo_download._publish_pair(
        audio_source,
        attribution_source,
        tmp_path,
        "Artist - Track",
    )

    assert (tmp_path / "Artist - Track.mp3").read_bytes() == b"foreign"
    assert (tmp_path / "Artist - Track.jamendo.json").read_bytes() == b"attribution"
    assert audio_path.name == "Artist - Track (2).mp3"
    assert attribution_path.name == "Artist - Track (2).jamendo.json"


def test_pair_publication_does_not_unlink_replaced_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_source = tmp_path / "audio.part"
    audio_source.write_bytes(b"audio")
    attribution_source = tmp_path / "attribution.part"
    attribution_source.write_bytes(b"attribution")
    call_count = 0

    def publish_then_replace(source: Path, destination: Path) -> None:
        nonlocal call_count
        call_count += 1
        destination.write_bytes(source.read_bytes())
        if call_count == 2:
            attribution_path = tmp_path / "Artist - Track.jamendo.json"
            attribution_path.write_bytes(b"foreign-replacement")
            raise OSError("simulated second publish failure")

    monkeypatch.setattr(jamendo_download, "_publish_one", publish_then_replace)

    with pytest.raises(jamendo_download.JamendoToolError, match="download_publish_failed"):
        jamendo_download._publish_pair(
            audio_source,
            attribution_source,
            tmp_path,
            "Artist - Track",
        )

    assert (tmp_path / "Artist - Track.jamendo.json").read_bytes() == b"foreign-replacement"


def test_copy_fallback_never_unlinks_destination_after_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.part"
    source.write_bytes(b"audio")
    destination = tmp_path / "destination.mp3"

    def link_unsupported(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    def copy_failure(_input: object, output: BinaryIO, *, length: int) -> None:
        assert length == 1024 * 1024
        output.write(b"partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(jamendo_download.os, "link", link_unsupported)
    monkeypatch.setattr(jamendo_download.shutil, "copyfileobj", copy_failure)

    with pytest.raises(OSError, match="simulated copy failure"):
        jamendo_download._publish_one(source, destination)

    assert destination.read_bytes() == b"partial"


def test_client_id_is_read_from_file_and_never_accepts_malformed_value(tmp_path: Path) -> None:
    valid = tmp_path / "valid.txt"
    valid.write_text("private-client-id", encoding="utf-8")
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("secret value with spaces", encoding="utf-8")

    assert jamendo_download.load_client_id(valid) == "private-client-id"
    with pytest.raises(jamendo_download.JamendoToolError, match="client_id_invalid"):
        jamendo_download.load_client_id(invalid)


def test_curl_transport_sends_client_id_via_stdin_not_process_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_id_file = tmp_path / "client-id.txt"
    client_id_file.write_text("private-client-id", encoding="utf-8")
    response = _response(
        _track(track_id="10", title="Allowed Song", artist="Open Artist", allowed=True)
    )

    def fake_run(arguments: list[str], **options: object) -> object:
        assert options["input"] == "private-client-id"
        assert all("private-client-id" not in argument for argument in arguments)
        assert "--location" not in arguments
        assert arguments[arguments.index("--max-redirs") + 1] == "0"
        output_index = arguments.index("--output") + 1
        Path(arguments[output_index]).write_bytes(response)
        return SimpleNamespace(stdout="application/json\n200")

    monkeypatch.setattr(jamendo_download.shutil, "which", lambda _name: "curl")
    monkeypatch.setattr(jamendo_download.subprocess, "run", fake_run)
    transport = jamendo_download.CurlTransport(client_id_file)

    assert transport.fetch_search_response("Allowed Song", limit=1) == response


def test_curl_transport_rejects_timeout_above_policy_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id_file = tmp_path / "client-id.txt"
    client_id_file.write_text("private-client-id", encoding="utf-8")
    monkeypatch.setattr(jamendo_download.shutil, "which", lambda _name: "curl")

    with pytest.raises(jamendo_download.JamendoToolError, match="timeout_invalid"):
        jamendo_download.CurlTransport(client_id_file, timeout_seconds=16)


def test_cli_reports_stable_code_for_argparse_failure(capsys: pytest.CaptureFixture[str]) -> None:
    result = jamendo_download.main(["query", "--limit", "0"])

    assert result == 2
    assert capsys.readouterr().err.strip() == "Error: arguments_invalid"


def test_curl_download_uses_exact_storage_url_without_redirect_or_client_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id_file = tmp_path / "client-id.txt"
    client_id_file.write_text("private-client-id", encoding="utf-8")
    destination = tmp_path / "audio.part"
    download_url = "https://prod-1.storage.jamendo.com/download/track/10/mp32/"

    def fake_run(arguments: list[str], **options: object) -> object:
        assert options["input"] is None
        assert "--location" not in arguments
        assert "client_id@-" not in arguments
        assert arguments[-1] == download_url
        output_index = arguments.index("--output") + 1
        Path(arguments[output_index]).write_bytes(b"ID3" + (b"a" * 4096))
        return SimpleNamespace(stdout="audio/mpeg\n200")

    monkeypatch.setattr(jamendo_download.shutil, "which", lambda _name: "curl")
    monkeypatch.setattr(jamendo_download.subprocess, "run", fake_run)
    transport = jamendo_download.CurlTransport(client_id_file)

    receipt = transport.download_audio(download_url, destination, max_bytes=8192)

    assert receipt.content_type == "audio/mpeg"
    assert receipt.byte_count == 4099
