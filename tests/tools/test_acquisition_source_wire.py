"""Offline producer -> verified queue -> bridge -> server metadata wire proof.

Run with the acquisition project's pinned environment. The server domain parser
is pure Python; no server runtime, network, credentials or persistent DB is used.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from local_music_acquisition import queue
from local_music_acquisition.models import AcquiredArtifact
from local_music_acquisition.providers import _music_sites_worker, jamendo, yandex_provider
from local_music_acquisition.providers.jamendo_provider import JamendoProvider
from local_music_acquisition.source_metadata import yt_dlp_metadata
from yandex_music import Album, Artist, Track, TrackPosition

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server/src"))
try:
    from autplay.domain.track_metadata import parse_source_metadata, source_metadata_document
finally:
    sys.path.pop(0)

_SPEC = importlib.util.spec_from_file_location(
    "acquisition_source_wire_bridge", ROOT / "tools/acquisition_vault_bridge.py"
)
assert _SPEC and _SPEC.loader
bridge = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bridge
_SPEC.loader.exec_module(bridge)


def audio() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 96000)
    return output.getvalue()


def provider(kind, payload, tmp_path, monkeypatch):
    if kind == "jamendo":
        candidate = jamendo._parse_candidate(
            {
                "id": "123",
                "name": "Song (Live)",
                "artist_name": "Artist",
                "artist_id": "8",
                "album_name": "Native Album",
                "album_id": "44",
                "duration": 12,
                "releasedate": "2020-02",
                "position": 2,
                "album_image": "https://usercontent.jamendo.com?type=album&id=44&width=600",
                "shareurl": "https://www.jamendo.com/track/123",
                "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
                "audiodownload": "https://prod-1.storage.jamendo.com/download/track/123/mp32/",
            }
        )
        result = object.__new__(JamendoProvider)
        result._transport, result._limit, result._max_bytes = object(), 20, 1024 * 1024
        monkeypatch.setattr(jamendo, "search_tracks", lambda *a, **k: (candidate,))

        def download(_ranked, output, **kwargs):
            path = output / "audio.wav"
            path.write_bytes(payload)
            return SimpleNamespace(audio_path=path, track=candidate)

        monkeypatch.setattr(jamendo, "download_track", download)
        return result
    if kind == "yandex":
        track = Track("123", title="Song", version="Live", artists=[Artist("8", name="Artist")])
        track.albums = [
            Album(
                id=44,
                title="Native Album",
                year=2020,
                genre="Jazz",
                artists=[Artist("9", name="Album Artist")],
                track_position=TrackPosition(volume=1, index=2),
            )
        ]
        result = yandex_provider.YandexProvider(tmp_path / "unused-token")
        result._client = SimpleNamespace(
            search=lambda *a, **k: SimpleNamespace(tracks=SimpleNamespace(results=[track]))
        )
        monkeypatch.setattr(
            yandex_provider.ymd_core,
            "to_downloadable_track",
            lambda *a, **k: SimpleNamespace(path=Path("download.wav"), download_info=object()),
        )
        monkeypatch.setattr(
            yandex_provider,
            "_download_track_bounded",
            lambda _c, _i, path, **k: path.write_bytes(payload),
        )
        monkeypatch.setattr(yandex_provider, "_validate_audio", lambda *a, **k: None)
        return result
    extractor = _music_sites_worker._NativeBandcampIE()
    monkeypatch.setattr(
        _music_sites_worker.BandcampIE,
        "_extract_data_attr",
        lambda *a: {"current": {"type": "track", "id": 123, "album_id": 44, "band_id": 8}},
    )

    def extract(*args):
        extractor._extract_data_attr("fixture", "song")
        return {
            "id": "123",
            "track": "Song (Live)",
            "artist": "Artist",
            "album": "Native Album",
            "album_artists": ["Album Artist"],
            "release_date": "20200203",
            "track_number": 2,
            "genres": ["Jazz"],
            "thumbnail": "https://f4.bcbits.com/img/fixture.jpg",
        }

    monkeypatch.setattr(_music_sites_worker.BandcampIE, "_real_extract", extract)
    selected = extractor._real_extract("https://artist.bandcamp.com/track/song")

    class BandcampFixture:
        name = "bandcamp"
        requires_rights_confirmation = False

        def acquire(self, item, output):
            import hashlib

            (output / "audio.wav").write_bytes(payload)
            return AcquiredArtifact(
                self.name,
                "sha256:" + hashlib.sha256(payload).hexdigest()[:12],
                source_metadata=yt_dlp_metadata(selected, provider=self.name),
            )

    return BandcampFixture()


@pytest.mark.parametrize("kind", ["jamendo", "yandex", "bandcamp"])
def test_actual_native_producer_receipt_and_server_parser_roundtrip(tmp_path, monkeypatch, kind):
    selected_provider = provider(kind, audio(), tmp_path, monkeypatch)
    playlist = tmp_path / "playlist.txt"
    playlist.write_text("Artist\tSong (Live)\n", encoding="utf-8")
    root, output = tmp_path / "queue", tmp_path / "music"
    queue.enqueue(playlist, root, output)
    rights = frozenset({"yandex"}) if kind == "yandex" else frozenset()
    assert (
        queue.run_queue(root, providers=(selected_provider,), rights_confirmed=rights)["downloaded"]
        == 1
    )
    path = next(output.glob("tracks/*/receipt.json"))
    receipt = bridge.read_receipt(path, output)
    bridge.verify_audio(receipt)
    native = receipt.source_metadata
    assert native is not None
    assert native == json.loads(path.read_text())["source_metadata"]
    assert source_metadata_document(parse_source_metadata(native)) == native
    assert native["provider"] == kind.upper()
    assert native["external_ids"]["native_album_id"] == "44"
    assert native["fields"]["title"] == "Song (Live)"
    assert native["fields"]["album"] == "Native Album"
    if kind == "jamendo":
        assert "album_artist" not in native["fields"]
        assert native["fields"]["release_date"] == "2020-02"
    else:
        assert native["fields"]["album_artist"] == "Album Artist"
        assert native["fields"]["genres"] == ["Jazz"]
    initial_identity = receipt.identity
    initial_metadata_hash = receipt.metadata_sha256
    assert (
        queue.run_queue(root, providers=(selected_provider,), rights_confirmed=rights)["downloaded"]
        == 1
    )
    repeated = bridge.read_receipt(path, output)
    assert repeated.identity == initial_identity
    assert repeated.metadata_sha256 == initial_metadata_hash
