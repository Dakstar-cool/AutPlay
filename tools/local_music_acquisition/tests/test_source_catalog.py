from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from local_music_acquisition import cli
from local_music_acquisition.models import PlaylistItem, ProviderFailure
from local_music_acquisition.providers import _music_sites_worker as sites
from local_music_acquisition.providers import _yt_dlp_worker as youtube
from local_music_acquisition.providers.music_sites import SoundCloudProvider
from local_music_acquisition.source_catalog import SourceCatalog, track_url


def _entry(**changes):
    return {
        "artist": "Artist, Guest",
        "title": "Song (Live)",
        "kind": "download",
        "provider": "soundcloud",
        "url": "https://soundcloud.com/artist/song-live",
        "evidence": "Reviewed artist publication",
        "duration_seconds": 120,
        **changes,
    }


def _catalog(tmp_path, entries):
    path = tmp_path / "source-catalog.json"
    path.write_text(json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8")
    return path


def _detail(**changes):
    return {
        "id": "1234",
        "extractor_key": "Soundcloud",
        "artist": "Guest vs. Artist",
        "track": "Song (Live)",
        "webpage_url": "https://soundcloud.com/artist/song-live",
        "duration": 120,
        "formats": [
            {
                "format_id": "download",
                "ext": "mp3",
                "protocol": "https",
                "vcodec": "none",
                "url": "https://cf-media.sndcdn.com/song.mp3",
            }
        ],
        **changes,
    }


def test_catalog_uses_recording_identity_without_dropping_versions(tmp_path):
    catalog = SourceCatalog(_catalog(tmp_path, [_entry()]))
    source = catalog.find("soundcloud", PlaylistItem(1, "Guest vs. Artist", "Song (Live)"))
    assert source and source.duration_seconds == 120
    assert catalog.find("soundcloud", PlaylistItem(1, "Artist, Guest", "Song")) is None
    assert catalog.find("bandcamp", PlaylistItem(1, "Artist, Guest", "Song (Live)")) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/song",
        "file:///music.mp3",
        "http://soundcloud.com/artist/song",
        "https://soundcloud.com.evil.test/artist/song",
        "https://user:pass@soundcloud.com/a/b",
        "https://soundcloud.com/a/b?secret=hidden",
        "https://soundcloud.com:443/a/b",
        "https://artist.bandcamp.com/album/release",
        "https://www.youtube.com/playlist?list=abc",
        "https://soundcloud.com/a/b\n",
        "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=abc",
    ],
)
def test_unreviewable_urls_rejected_before_network(tmp_path, url):
    for provider in ("soundcloud", "bandcamp", "yt_dlp"):
        assert track_url(url, provider) is None
    with pytest.raises(ValueError, match=r"^source_catalog_invalid$"):
        SourceCatalog(_catalog(tmp_path, [_entry(url=url)]))


@pytest.mark.parametrize(
    "entries",
    [
        [_entry(), _entry(url="https://soundcloud.com/artist/other")],
        [_entry(), _entry(title="Song (Remix)")],
        [_entry(duration_seconds=float("nan"))],
        [_entry(duration_seconds=10**400)],
        [_entry(duration_seconds=True)],
        [_entry(provider="generic")],
        [_entry(kind="unknown")],
    ],
)
def test_conflicting_or_invalid_catalog_is_rejected(tmp_path, entries):
    with pytest.raises(ValueError, match="source_catalog_invalid"):
        SourceCatalog(_catalog(tmp_path, entries))


def test_reference_pages_are_never_routed_to_downloaders(tmp_path):
    catalog = SourceCatalog(
        _catalog(tmp_path, [_entry(kind="reference", url="https://store.example/album/release")])
    )
    assert catalog.summary() == {"download_links": 0, "references": 1}
    assert catalog.find("soundcloud", PlaylistItem(1, "Artist, Guest", "Song (Live)")) is None


def test_youtube_links_are_canonicalized_without_tracking_data():
    expected = "https://www.youtube.com/watch?v=abcdefghijk"
    assert track_url("https://youtu.be/abcdefghijk", "yt_dlp") == expected
    assert track_url("https://music.youtube.com/watch?v=abcdefghijk", "yt_dlp") == expected


@pytest.mark.parametrize(
    "changed",
    [
        {"track": "Song (Remix)"},
        {"artist": "Artist"},
        {"extractor_key": "Generic"},
        {"webpage_url": "https://soundcloud.com/artist/other"},
    ],
)
def test_manual_url_does_not_bypass_metadata_checks(changed):
    calls = []

    def extract(url, *, download):
        assert not download
        calls.append(url)
        return _detail(**changed)

    with pytest.raises(sites._SourceError, match="exact_match_not_found"):
        sites._select_url(
            SimpleNamespace(extract_info=extract),
            "soundcloud",
            "Artist, Guest",
            "Song (Live)",
            _entry()["url"],
        )
    assert calls == [_entry()["url"]]


def test_manual_url_avoids_search_but_still_checks_original_and_drm():
    detail = _detail()
    metadata = SimpleNamespace(extract_info=lambda url, **kwargs: detail)
    selected = sites._select_url(
        metadata, "soundcloud", "Artist, Guest", "Song (Live)", _entry()["url"]
    )
    assert selected["id"] == "1234"
    detail["has_drm"] = True
    with pytest.raises(sites._SourceError, match="drm_protected"):
        sites._select_url(metadata, "soundcloud", "Artist, Guest", "Song (Live)", _entry()["url"])


def test_youtube_manual_link_requires_correct_id_and_version():
    detail = {"id": "abcdefghijk", "extractor_key": "Youtube", "artist": "Artist", "track": "Song"}
    calls = []

    def extract(url, **kwargs):
        calls.append(url)
        return detail

    metadata = SimpleNamespace(extract_info=extract)
    url = "https://youtu.be/abcdefghijk"
    assert youtube._find_url(metadata, url, artist="Artist", title="Song") == detail
    detail["track"] = "Song (Live)"
    assert youtube._find_url(metadata, url, artist="Artist", title="Song") is None
    detail.update(track="Song", id="otherabcdef")
    assert youtube._find_url(metadata, url, artist="Artist", title="Song") is None
    assert all(c == "https://www.youtube.com/watch?v=abcdefghijk" for c in calls)


def test_provider_transports_reviewed_link_duration_and_receipt_evidence(monkeypatch, tmp_path):
    catalog = SourceCatalog(_catalog(tmp_path, [_entry()]))

    def popen(*args, **kwargs):
        def communicate(raw, timeout):
            request = json.loads(raw)
            assert request["source_url"] == _entry()["url"]
            assert request["expected_duration_seconds"] == 120
            return json.dumps({"status": "downloaded", "artifact_ref": "sha256:123456789abc"}), ""

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(subprocess, "Popen", popen)
    provider = SoundCloudProvider(source_catalog=catalog)
    result = provider.acquire(PlaylistItem(1, "Guest, Artist", "Song (Live)"), tmp_path)
    assert result.identity_version.startswith("reviewed-source-v1:")
    assert result.expected_duration_seconds == 120
    with pytest.raises(ProviderFailure, match="source_duration_conflict"):
        provider.acquire(
            PlaylistItem(1, "Artist, Guest", "Song (Live)", expected_duration_seconds=180), tmp_path
        )


def test_cli_auto_loads_catalog_and_rejects_invalid_catalog_in_preflight(tmp_path, capsys):
    path = _catalog(tmp_path, [_entry()])
    args = [
        str(tmp_path / "input.txt"),
        "--output-dir",
        str(tmp_path),
        "--disable-jamendo",
        "--disable-hitmo",
        "--disable-yt-dlp",
        "--check-runtime",
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["source_catalog"]["download_links"] == 1
    path.write_text("invalid")
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "source_catalog_invalid"}


def test_catalog_does_not_enable_provider_or_waive_rights(tmp_path, capsys):
    _catalog(tmp_path, [_entry()])
    result = cli.main(
        [
            str(tmp_path / "input.txt"),
            "--output-dir",
            str(tmp_path),
            "--disable-jamendo",
            "--disable-hitmo",
            "--disable-yt-dlp",
            "--enable-soundcloud",
        ]
    )
    assert result == 2
    assert "soundcloud_rights_confirmation_required" in capsys.readouterr().err
