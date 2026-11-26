import json
from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from autplay.adapters.discogs_tracklist import annotate_discogs_positions
from autplay.adapters.filesystem.catalog_child import execute
from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
from autplay.application.music_discovery import MusicDiscoveryService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.catalog_artwork import catalogue_api_url
from autplay.ports.track_metadata import MetadataProviderError


def test_musicbrainz_complete_edition_wins_and_vault_depends_on_owner() -> None:
    first, other, edition = uuid4(), uuid4(), uuid4()
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    cache = Mock(spec=CatalogPageCache)
    cache.read.return_value = {
        "items": [
            {
                "recording_id": str(identity),
                "disc_number": 2,
                "track_number": pos,
                "title": "Repeated",
                "artist": "Artist",
                "duration_ms": None,
            }
            for pos, identity in enumerate((first, other, first), 1)
        ]
    }
    cache.available_recordings.return_value = {str(first): "playable"}
    external = Mock(side_effect=AssertionError("unexpected secondary lookup"))
    service = MusicDiscoveryService(Mock(), cache, external, True)
    album = service.album_tracklist(owner, edition, None, "", "", None)
    assert album["source"] == "musicbrainz"
    assert [row["in_vault"] for row in album["tracks"]] == [True, False, True]
    cache.available_recordings.assert_called_once_with(
        owner.user_id, [str(first), str(other), str(first)]
    )
    external.assert_not_called()


def test_missing_primary_reads_linked_discogs_without_search_token() -> None:
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    edition, recording = uuid4(), uuid4()
    cache = Mock(spec=CatalogPageCache)
    cache.read.side_effect = MetadataProviderError("metadata_release_missing", retryable=False)
    cache.visible_album_positions.return_value = [
        dict(
            title="Song",
            artist="Artist",
            recording=str(recording),
            disc=1,
            sequence=1,
            number="A1",
            duration_ms=123000,
        )
    ]
    links = {
        "id": str(edition),
        "relations": [
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/master/333"}},
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/release/123"}},
        ],
    }
    release = {
        "id": 123,
        "title": "Album",
        "artists": [{"name": "Artist (2)"}],
        "tracklist": [
            {"type_": "heading", "title": "Side A"},
            {"type_": "track", "position": "A1", "title": "Song", "duration": "2:03"},
            {"type_": "index", "sub_tracks": [{"position": "B1", "title": "Other"}]},
        ],
    }
    read = Mock(side_effect=[json.dumps(doc).encode() for doc in (links, release)])
    result = MusicDiscoveryService(Mock(), cache, read).album_tracklist(
        owner, edition, None, "", "", None
    )
    assert read.call_args.args == (owner, "https://api.discogs.com/releases/123")
    assert result["source"] == "discogs"
    assert [(r["position"], r["in_vault"]) for r in result["tracks"]] == [
        ("A1", True),
        ("B1", None),
    ]


def test_search_keeps_ambiguous_editions_separate() -> None:
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    search = {
        "pagination": {"pages": 1},
        "results": [
            {
                "title": "Joji (2) - BALLADS 1",
                "type": "release",
                "id": i,
                "year": "2018",
                "format": [medium],
            }
            for i, medium in [(12, "Vinyl"), (13, "CD")]
        ],
    }
    read = Mock(side_effect=[b'{"count":0,"releases":[]}', json.dumps(search).encode()])
    result = MusicDiscoveryService(Mock(), read_public=read, discogs_search=True).album_tracklist(
        owner, None, None, "BALLADS 1", "Joji", 2018
    )
    assert [c["id"] for c in result["choices"]] == ["12", "13"]
    assert catalogue_api_url(read.call_args.args[1])
    assert not catalogue_api_url("https://api.discogs.com/releases/123?token=x")
    assert not catalogue_api_url("https://api.discogs.com.example/releases/123")


def test_position_requires_unique_recording_and_compatible_duration() -> None:
    tracks = [dict(position="2-1", title="Title", artist="Artist", duration_ms=180000)]
    base = dict(title="Title", artist="Artist", disc=2, sequence=1, number="1", duration_ms=180000)
    annotate_discogs_positions(tracks, [dict(base, recording="one"), dict(base, recording="two")])
    assert tracks[0]["in_vault"] is None
    annotate_discogs_positions(tracks, [dict(base, recording="one", duration_ms=200000)])
    assert tracks[0]["in_vault"] is None


@pytest.mark.parametrize(
    ("credential", "header"),
    [
        ("test-token", "Discogs token=test-token"),
        (
            '{"consumer_key":"example-key","consumer_secret":"example-secret"}',
            "Discogs key=example-key, secret=example-secret",
        ),
    ],
)
def test_discogs_token_only_attached_to_authenticated_search(
    monkeypatch: pytest.MonkeyPatch,
    credential: str,
    header: str,
) -> None:
    monkeypatch.setenv("AUTPLAY_DISCOGS_TOKEN", credential)
    search = (
        "https://api.discogs.com/database/search?type=release&artist=A&release_title=B&per_page=25"
    )
    for url in (
        search,
        "https://api.discogs.com/releases/123",
        "https://musicbrainz.org/ws/2/release",
    ):
        with patch("autplay.adapters.filesystem.catalog_child.PublicMetadataHttp") as factory:
            http = factory.return_value
            http.client.headers = {}
            http.get.return_value = b"{}"
            execute(
                {
                    "version": 1,
                    "execution_id": str(uuid4()),
                    "request_id": str(uuid4()),
                    "url": url,
                },
                BytesIO(),
            )
            assert http.client.headers == ({"Authorization": header} if url == search else {})
            http.get.assert_called_once_with(url)


def test_tagged_album_matches_credited_guests_and_rejects_the_longer_version() -> None:
    rows = [
        dict(
            title="Song",
            artist="Joji feat. A & B",
            disc=None,
            sequence=None,
            number=None,
            duration_ms=120580,
            recording="local",
        )
    ]
    track = dict(position="B1", title="Song", artist="Joji feat. B & A", duration_ms=120000)
    others = [dict(track, duration_ms=132000), dict(track, artist="Joji feat. B")]
    annotate_discogs_positions([track, *others], rows)
    assert [t["in_vault"] for t in [track, *others]] == [True, None, None]


def test_primary_edition_checks_owner_tags_when_recording_identifier_is_missing() -> None:
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    mbid, rec = uuid4(), uuid4()
    cache = Mock(spec=CatalogPageCache)
    cache.available_recordings.return_value = {}
    cache.read.return_value = dict(
        edition_title="Album",
        edition_artist="Artist",
        items=[
            dict(
                recording_id=str(rec),
                track_number=1,
                disc_number=2,
                title="Song",
                artist="Artist",
                duration_ms=123000,
            )
        ],
    )
    row = dict(
        title="Song",
        artist="Artist",
        recording="visible",
        disc=2,
        sequence=1,
        number="1",
        duration_ms=123800,
        mb_recording=None,
    )
    cache.visible_album_positions.return_value = [row]
    svc = MusicDiscoveryService(Mock(), cache)
    assert (
        svc.album_tracklist(owner, mbid, None, "wrong", "wrong", None)["tracks"][0]["in_vault"]
        is True
    )
    cache.visible_album_positions.assert_called_once_with(owner.user_id, "Album", "Artist", None)
    cache.visible_album_positions.return_value = [dict(row, mb_recording=str(uuid4()))]
    assert svc.album_tracklist(owner, mbid, None, "", "", None)["tracks"][0]["in_vault"] is None
