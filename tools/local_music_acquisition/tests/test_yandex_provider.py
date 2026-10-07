from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from yandex_music import Album, Artist, Track, TrackPosition

from local_music_acquisition.models import PlaylistItem, ProviderFailure, ProviderMiss
from local_music_acquisition.providers import yandex_provider
from local_music_acquisition.providers.yandex_provider import YandexProvider


def _track(artist: str = "Artist", title: str = "Title", version: str | None = None) -> Track:
    return Track("1", title=title, version=version, artists=[Artist("1", name=artist)])


def _provider(client: object) -> YandexProvider:
    provider = YandexProvider(Path("unused-token"))
    provider._client = client  # type: ignore[assignment]
    return provider


def test_yandex_download_uses_pinned_library_and_publishes_exact_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    track = _track()
    # SDK annotations do not guarantee that optional upstream leaves are well formed.
    track.albums = [Album(id=22, title="Native Album", year=2020)]
    track.albums[0].release_date = 123
    track.albums[0].cover_uri = {"malformed": "optional"}
    track.albums[0].artists = None
    track.albums[0].track_position = {}
    search = SimpleNamespace(tracks=SimpleNamespace(results=[track]))
    client = SimpleNamespace(search=lambda *_args, **_kwargs: search)
    payload = b"ID3" + b"x" * 2048
    monkeypatch.setattr(
        yandex_provider.ymd_core,
        "to_downloadable_track",
        lambda *_args, **_kwargs: SimpleNamespace(
            path=Path("download.mp3"), download_info=object()
        ),
    )
    monkeypatch.setattr(
        yandex_provider,
        "_download_track_bounded",
        lambda _client, _info, destination, **_kwargs: destination.write_bytes(payload),
    )
    monkeypatch.setattr(
        yandex_provider,
        "_validate_audio",
        lambda _path, _suffix, **_kwargs: None,
    )

    artifact = _provider(client).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)

    destination = tmp_path / "Artist - Title.mp3"
    assert destination.read_bytes() == payload
    assert artifact.artifact_ref == ("sha256:" + hashlib.sha256(payload).hexdigest()[:12])
    assert artifact.source_metadata["fields"]["album"] == "Native Album"
    assert artifact.source_metadata["fields"]["release_date"] == "2020"
    assert artifact.source_metadata["external_ids"]["native_album_id"] == "22"


@pytest.mark.parametrize(
    ("requested", "candidate_title", "candidate_version", "matches"),
    [
        ("Title", "Title", None, True),
        ("Title", "Title", "Live", False),
        ("Title (Live)", "Title", "Live", True),
        ("Title (Remix)", "Title", "Live", False),
        ("Title (Instrumental)", "Title", "Instrumental", True),
    ],
)
def test_yandex_identity_keeps_recording_version_separate(
    requested: str,
    candidate_title: str,
    candidate_version: str | None,
    matches: bool,
) -> None:
    assert (
        yandex_provider._track_matches(
            _track(title=candidate_title, version=candidate_version),
            artist="Artist",
            title=requested,
        )
        is matches
    )


def test_yandex_multiple_exact_versions_are_left_ambiguous(tmp_path: Path) -> None:
    search = SimpleNamespace(tracks=SimpleNamespace(results=[_track(), _track()]))
    client = SimpleNamespace(search=lambda *_args, **_kwargs: search)

    with pytest.raises(ProviderMiss, match=r"yandex\.ambiguous_match"):
        _provider(client).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)


def test_native_album_membership_version_positions_and_cover_are_preserved():
    track = _track(title="Alive", version="Live")
    track.albums = [
        Album(
            id=22,
            title="Album",
            year=2020,
            artists=[Artist("9", name="Album Artist")],
            cover_uri="avatars.yandex.net/get-music-content/fixture/%%",
            track_position=TrackPosition(volume=2, index=3),
        )
    ]
    result = yandex_provider._native_metadata(track, None)
    assert result is not None
    assert result["fields"] == {
        "title": "Alive (Live)",
        "artist": "Artist",
        "album": "Album",
        "album_artist": "Album Artist",
        "release_date": "2020",
        "track_number": 3,
        "disc_number": 2,
    }
    assert result["external_ids"] == {"native_album_id": "22", "native_artist_id": "1"}
    assert result["artwork"][0]["kind"] == "album"


def test_unresolved_multiple_albums_never_choose_the_first_edition():
    track = _track(version="Remix")
    track.albums = [Album(id=22, title="Album", year=2020), Album(id=23, title="Album", year=2021)]
    result = yandex_provider._native_metadata(track, "Album")
    assert result is not None
    assert result["fields"] == {"title": "Title (Remix)", "artist": "Artist"}
    assert result["external_ids"] == {"native_artist_id": "1"}
    assert result["artwork"] == []


class _StreamingResponse:
    def __init__(self, chunks: list[bytes], content_length: str | None = None) -> None:
        self._chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}

    def __enter__(self) -> _StreamingResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        assert chunk_size == 64 * 1024
        return self._chunks


def test_yandex_stream_stops_when_byte_budget_is_crossed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    response = _StreamingResponse([b"x" * 800, b"y" * 800])
    monkeypatch.setattr(yandex_provider.requests, "get", lambda *_args, **_kwargs: response)
    destination = tmp_path / "bounded.part"

    with pytest.raises(ProviderFailure, match=r"yandex\.download_size_invalid"):
        yandex_provider._download_track_bounded(
            SimpleNamespace(request=SimpleNamespace(proxies=None)),
            SimpleNamespace(urls=["https://download.test/audio"], decryption_key=None),
            destination,
            max_bytes=1024,
            deadline=yandex_provider.time.monotonic() + 10,
            activity_timeout_seconds=5,
        )

    assert destination.stat().st_size == 800


def test_yandex_trickle_obeys_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    response = _StreamingResponse([b"x" * 512, b"y" * 512])
    monkeypatch.setattr(yandex_provider.requests, "get", lambda *_args, **_kwargs: response)
    ticks = iter([0.0, 0.2, 1.1])
    monkeypatch.setattr(yandex_provider.time, "monotonic", lambda: next(ticks))

    with pytest.raises(ProviderFailure, match=r"yandex\.download_timeout"):
        yandex_provider._download_track_bounded(
            SimpleNamespace(request=SimpleNamespace(proxies=None)),
            SimpleNamespace(urls=["https://download.test/audio"], decryption_key=None),
            tmp_path / "deadline.part",
            max_bytes=4096,
            deadline=1.0,
            activity_timeout_seconds=5,
        )


def test_yandex_audio_validation_decodes_complete_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.mp3.part"
    candidate.write_bytes(b"ID3" + b"x" * 2048)
    captured: dict[str, object] = {}

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(yandex_provider.subprocess, "run", run)
    yandex_provider._validate_audio(
        candidate,
        ".mp3",
        deadline=yandex_provider.time.monotonic() + 5,
    )

    assert captured["arguments"] == [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(candidate),
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    ]
    assert 0 < float(captured["timeout"]) <= 5


def test_yandex_rejects_truncated_audio_after_header_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "truncated.mp3.part"
    candidate.write_bytes(b"ID3" + b"x" * 2048)
    monkeypatch.setattr(
        yandex_provider.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, b"", b"decode failed"),
    )

    with pytest.raises(ProviderFailure, match=r"yandex\.audio_invalid"):
        yandex_provider._validate_audio(
            candidate,
            ".mp3",
            deadline=yandex_provider.time.monotonic() + 5,
        )


def test_yandex_rejects_non_exact_search_result(tmp_path: Path) -> None:
    search = SimpleNamespace(tracks=SimpleNamespace(results=[_track(title="Title live")]))
    client = SimpleNamespace(search=lambda *_args, **_kwargs: search)

    with pytest.raises(ProviderMiss, match=r"yandex\.exact_match_not_found"):
        _provider(client).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)


def test_yandex_token_errors_are_stable_and_do_not_echo_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "this-token-must-never-appear"
    token_file = tmp_path / "token"
    token_file.write_text(secret, encoding="utf-8")
    if yandex_provider.os.name != "nt":
        token_file.chmod(0o600)
    monkeypatch.setattr(
        yandex_provider.ymd_core,
        "init_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(ProviderFailure) as captured:
        YandexProvider(token_file)._client_or_raise()

    assert str(captured.value) == "yandex.initialization_failed"
    assert secret not in str(captured.value)
