from __future__ import annotations

from local_music_acquisition.providers import _discovery_worker as worker


def test_search_never_downloads_and_caps_results(monkeypatch):
    calls = []

    class Search:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, *, download):
            assert download is False
            calls.append(url)
            return {"entries": [{"title": f"Artist - Song {i}"} for i in range(30)]}

    monkeypatch.setattr(worker, "YoutubeDL", Search)
    monkeypatch.setattr(worker, "_options", lambda proxy: {})
    result = worker.discover({"provider": "yt_dlp", "artist": "Artist", "title": "Song"})
    assert result["status"] == "discovered"
    assert len(result["candidates"]) == 20
    assert len(calls) == 1


def test_channel_hint_requires_metadata_and_hydration_is_bounded(monkeypatch):
    calls = []

    class Search:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, *, download):
            assert download is False
            calls.append(url)
            if url.startswith("ytsearch"):
                return {
                    "entries": [{"id": "abc123XYZ_4", "title": "Song", "uploader": "Artist"}] * 20
                }
            return {"title": "Song", "uploader": "Artist"}

    monkeypatch.setattr(worker, "YoutubeDL", Search)
    monkeypatch.setattr(worker, "_options", lambda proxy: {})
    result = worker.discover({"provider": "yt_dlp", "artist": "Artist", "title": "Song"})
    assert result["candidates"] == []
    assert len(calls) == 4
