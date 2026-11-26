from typing import Any

import pytest
from autplay.adapters.external_collections import CollectionReader
from autplay.domain.external_collections import CollectionError, parse_collection_link


def test_yandex_reconciles_like_ids_preserving_aliases_and_unavailable_rows() -> None:
    def fetch(provider: str, path: str, token: str | None) -> dict[str, Any]:
        if path.endswith("/likes/tracks"):
            return {"result": {"library": {"tracks": [{"id": str(i)} for i in range(4382)]}}}
        return {
            "result": {
                "title": "Likes",
                "kind": 3,
                "owner": {"uid": 7},
                "trackCount": 4316,
                "tracks": [
                    {
                        "track": {
                            "id": str(i),
                            "title": "Unavailable" if i == 0 else "Example",
                            "artists": [{"name": "Artist"}],
                            "available": i != 0,
                            "realId": "verified-later" if i == 0 else None,
                        }
                    }
                    for i in range(4316)
                ],
            }
        }

    page = CollectionReader(fetch).page(
        parse_collection_link("https://music.yandex.ru/users/example/likes"), True, None
    )
    assert (page.expected, page.likes_count, len(page.tracks)) == (4316, 4382, 4382)
    assert page.tracks[0].unavailable and page.tracks[0].aliases == ("verified-later",)
    assert sum(t.title is None and t.track_id is not None for t in page.tracks) == 66


def test_spotify_walks_owned_playlist_offsets_and_retains_nontrack_positions() -> None:
    link = parse_collection_link("https://open.spotify.com/playlist/" + "a" * 22)
    offsets = []

    def fetch(provider: str, path: str, token: str | None) -> dict[str, Any]:
        assert token == "fixture" and provider == "spotify"
        if path == "/v1/me":
            return {"id": "self"}
        if "fields=" in path:
            return {"name": "Long list", "snapshot_id": "first", "owner": {"id": "self"}}
        offset = int(path.partition("offset=")[2])
        offsets.append(offset)
        return {
            "items": [
                {"item": {"type": "track", "id": str(i), "name": str(i)}}
                if i != 51
                else {"item": None}
                for i in range(offset, offset + 50)
            ],
            "total": 1000,
            "next": "more" if offset < 950 else None,
        }

    reader = CollectionReader(fetch)
    rows = []
    cursor = None
    while True:
        page = reader.page(link, False, cursor, "fixture")
        rows.extend(page.tracks)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert offsets == list(range(0, 1000, 50))
    assert len(rows) == 1000 and rows[51].unavailable and rows[-1].track_id == "999"


@pytest.mark.parametrize(
    "url",
    [
        "http://music.yandex.ru/album/1",
        "https://open.spotify.com.evil.test/collection/tracks",
        "https://credentials@music.yandex.ru/album/1",
        "https://music.yandex.ru:444/album/1",
        "https://music.yandex.ru/users/../playlists/a",
        "https://soundcloud.com.evil.test/some/likes",
    ],
)
def test_only_supported_https_origins(url: str) -> None:
    with pytest.raises(CollectionError):
        parse_collection_link(url)


def test_private_spotify_playlist_needs_an_owner_or_collaborator() -> None:
    link = parse_collection_link("https://open.spotify.com/playlist/" + "a" * 22)

    def fetch(provider: str, path: str, token: str | None) -> dict[str, Any]:
        return {"id": "self"} if path == "/v1/me" else {"name": "Other", "owner": {"id": "else"}}

    with pytest.raises(CollectionError, match="playlist_ownership_required"):
        CollectionReader(fetch).page(link, False, None, "fixture")


def test_soundcloud_reads_tracks_and_sets_with_app_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.parse import parse_qs, urlsplit

    import requests
    from autplay.adapters.external_collections import read_metadata

    owner = "soundcloud:users:42"

    def get(url: str, **options: Any) -> requests.Response:
        import json

        uri = urlsplit(url)
        params = parse_qs(uri.query)
        assert options["headers"]["Authorization"] == "OAuth fixture"
        response = requests.Response()
        response.status_code = 200
        response._content_consumed = True
        if uri.path == "/resolve":
            response.status_code = 302
            response.headers["Location"] = "https://api.soundcloud.com/users/" + owner
            return response
        data: dict[str, Any]
        if uri.path.endswith("/likes/tracks"):
            assert params["linked_partitioning"] == ["true"]
            data = {"collection": [{"urn": "soundcloud:tracks:1", "title": "Track"}]}
        elif uri.path.endswith("/likes/playlists"):
            data = {
                "collection": [
                    {"permalink_url": "https://soundcloud.com/fixture/sets/one", "title": "Set"}
                ]
            }
        else:
            assert uri.path == "/users/" + owner
            data = {
                "urn": owner,
                "username": "Test",
                "public_favorites_count": 1,
                "playlist_likes_count": 1,
            }
        response._content = json.dumps(data).encode()
        return response

    monkeypatch.setattr(requests, "get", get)
    reader = CollectionReader(read_metadata)
    link = parse_collection_link("https://soundcloud.com/fixture/likes")
    first = reader.page(link, True, None, "fixture")
    second = reader.page(link, True, first.next_cursor, "fixture")
    assert first.tracks[0].track_id == "soundcloud:tracks:1" and first.next_cursor
    assert (
        second.sets == [("https://soundcloud.com/fixture/sets/one", "Set")]
        and not second.tracks
        and second.next_cursor is None
    )


@pytest.mark.parametrize("prefix", ["", "lk.", "ch."])
def test_yandex_shared_playlist_ids_keep_their_provider_prefix(prefix: str) -> None:
    key = prefix + "c9fc36cd-2ed5-42a8-a9cf-0c16d64b7cee"
    assert parse_collection_link("https://music.yandex.ru/playlists/" + key).key == key
