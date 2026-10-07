from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from autplay.adapters.public_music_search import MusicBrainzDiscoveryProvider
from autplay.domain.music_discovery import MusicDiscoveryKind
from autplay.ports.track_metadata import MetadataProviderError


class Http:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.urls: list[str] = []

    def json(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        return self.document


def test_artist_search_escapes_lucene_uses_existing_host_and_raw_pagination_progress() -> None:
    identity = str(uuid4())
    http = Http(
        {
            "count": 7,
            "artists": [
                {"id": identity, "name": "AC/DC", "disambiguation": "Live band"},
                {"id": identity, "name": "AC/DC"},
            ],
        }
    )
    page = (
        MusicBrainzDiscoveryProvider(http)
        .search(
            '  AC/DC  + arid:* "Live"  ',
            MusicDiscoveryKind.ARTIST,
            limit=2,
            offset=3,
        )
        .view()
    )
    url = urlsplit(http.urls[0])
    assert url.scheme == "https" and url.netloc == "musicbrainz.org" and url.path == "/ws/2/artist"
    params = parse_qs(url.query)
    assert params["query"] == [r"AC\/DC \+ arid\:\* \"Live\""]
    assert params["limit"] == ["2"] and params["offset"] == ["3"]
    assert len(page["items"]) == 1  # type: ignore[arg-type]
    assert page["total_count"] == 7 and page["next_offset"] == 5


def test_album_search_keeps_same_title_different_editions_and_partial_dates() -> None:
    http = Http(
        {
            "count": 2,
            "releases": [
                {
                    "id": str(uuid4()),
                    "title": "Album",
                    "date": "1998",
                    "artist-credit": [{"name": "First artist"}],
                },
                {
                    "id": str(uuid4()),
                    "title": "Album",
                    "date": "2020-03",
                    "artist-credit": [{"name": "Second artist"}],
                },
            ],
        }
    )
    page = MusicBrainzDiscoveryProvider(http).search(
        "Album", MusicDiscoveryKind.ALBUM, limit=2, offset=0
    )
    assert len(page.items) == 2 and page.items[0].entity_id != page.items[1].entity_id
    assert [item.release_date for item in page.items] == ["1998", "2020-03"]
    assert all(item.release_id == item.entity_id for item in page.items)
    assert all(item.download_search_query is None for item in page.items)


def test_artist_drilldown_is_uuid_browse_preserves_versions_and_unknown_duration() -> None:
    artist_id = uuid4()
    http = Http(
        {
            "recording-count": 2,
            "recordings": [
                {
                    "id": str(uuid4()),
                    "title": "Song (Live)",
                    "length": None,
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                },
                {
                    "id": str(uuid4()),
                    "title": "Song (Remix)",
                    "length": 0,
                    "artist-credit": [{"name": "Artist"}],
                },
            ],
        }
    )
    page = MusicBrainzDiscoveryProvider(http).artist_tracks(artist_id, limit=2, offset=0)
    assert [item.title for item in page.items] == ["Song (Live)", "Song (Remix)"]
    assert all(item.duration_ms is None for item in page.items)
    assert page.items[0].download_search_query == "Artist Song (Live)"
    params = parse_qs(urlsplit(http.urls[0]).query)
    assert params["artist"] == [str(artist_id)] and "query" not in params


def test_exact_release_keeps_track_occurrences_actual_positions_and_artist_credits() -> None:
    release_id, recording_id = uuid4(), str(uuid4())
    first, second, third = str(uuid4()), str(uuid4()), str(uuid4())
    http = Http(
        {
            "id": str(release_id),
            "date": "2001-04-05",
            "artist-credit": [{"name": "Various"}],
            "media": [
                {
                    "position": 2,
                    "track-count": 1,
                    "tracks": [
                        {
                            "id": third,
                            "position": 1,
                            "recording": {"id": str(uuid4()), "title": "Third"},
                        },
                    ],
                },
                {
                    "position": 1,
                    "track-count": 2,
                    "tracks": [
                        {
                            "id": second,
                            "position": 2,
                            "title": "Track reprise",
                            "length": 123000,
                            "recording": {"id": recording_id, "title": "Recording"},
                            "artist-credit": [
                                {"name": "Guest", "joinphrase": " feat. "},
                                {"name": "Artist"},
                            ],
                        },
                        {
                            "id": first,
                            "position": 1,
                            "recording": {"id": recording_id, "title": "Recording"},
                        },
                    ],
                },
            ],
        }
    )
    page = MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=2, offset=0)
    assert [str(item.entity_id) for item in page.items] == [first, second]
    assert page.items[0].recording_id == page.items[1].recording_id
    assert page.items[1].title == "Track reprise" and page.items[1].artist == "Guest feat. Artist"
    assert [(item.disc_number, item.track_number) for item in page.items] == [(1, 1), (1, 2)]
    assert page.items[0].duration_ms is None
    assert page.view()["next_offset"] == 2 and page.total_count == 3
    following = MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=2, offset=2)
    assert str(following.items[0].entity_id) == third and following.view()["next_offset"] is None
    assert urlsplit(http.urls[0]).path == f"/ws/2/release/{release_id}"


def test_missing_positions_remain_unknown_and_incomplete_release_is_not_success() -> None:
    release_id = uuid4()
    medium = {"tracks": [{"id": str(uuid4()), "recording": {"id": str(uuid4()), "title": "Song"}}]}
    http = Http({"id": str(release_id), "media": [medium]})
    card = MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=1, offset=0).items[0]
    assert card.disc_number is None and card.track_number is None and card.artist is None
    assert card.download_search_query is None
    medium["track-count"] = 2  # type: ignore[assignment]
    with pytest.raises(MetadataProviderError, match="metadata_response_invalid"):
        MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=1, offset=0)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"count": 1, "artists": [{"id": "not-a-uuid", "name": "Artist"}]},
        {"count": True, "artists": []},
        {"count": 1, "artists": [{"id": str(uuid4()), "name": "a" * 501}]},
        {"count": 0, "artists": "invalid"},
    ],
)
def test_malformed_provider_responses_are_stable_errors(document: dict[str, Any]) -> None:
    with pytest.raises(MetadataProviderError) as error:
        MusicBrainzDiscoveryProvider(Http(document)).search(
            "Artist", MusicDiscoveryKind.ARTIST, limit=1, offset=0
        )
    assert error.value.code == "metadata_response_invalid" and error.value.retryable is False


def test_release_missing_and_bad_dates_do_not_fabricate_metadata() -> None:
    release_id = uuid4()
    with pytest.raises(MetadataProviderError, match="metadata_release_missing"):
        MusicBrainzDiscoveryProvider(Http({})).release_tracks(release_id, limit=1, offset=0)
    http = Http(
        {"count": 1, "releases": [{"id": str(release_id), "title": "Album", "date": "2001-02-31"}]}
    )
    with pytest.raises(MetadataProviderError, match="metadata_response_invalid"):
        MusicBrainzDiscoveryProvider(http).search(
            "Album", MusicDiscoveryKind.ALBUM, limit=1, offset=0
        )


def test_partial_release_media_and_mismatched_provider_progress_are_rejected() -> None:
    release_id = uuid4()
    with pytest.raises(MetadataProviderError, match="metadata_response_invalid"):
        MusicBrainzDiscoveryProvider(
            Http(
                {
                    "id": str(release_id),
                    "medium-count": 2,
                    "media": [{"position": 1, "tracks": []}],
                }
            )
        ).release_tracks(release_id, limit=25, offset=0)
    for document in (
        {"count": 1, "offset": 2, "artists": []},
        {"count": 0, "artists": [{"id": str(uuid4()), "name": "Artist"}]},
        {"count": 2, "artists": [{"id": str(uuid4()), "name": "Artist"}] * 2},
    ):
        with pytest.raises(MetadataProviderError, match="metadata_response_invalid"):
            MusicBrainzDiscoveryProvider(Http(document)).search(
                "Artist", MusicDiscoveryKind.ARTIST, limit=1, offset=0
            )


def test_release_track_keeps_recording_versions_without_inventing_performer() -> None:
    release_id = uuid4()
    recording: dict[str, Any] = {
        "id": str(uuid4()),
        "title": "Song (Live)",
        "disambiguation": "live at Venue",
    }
    track = {"id": str(uuid4()), "title": "Song", "position": 1, "recording": recording}
    http = Http(
        {
            "id": str(release_id),
            "artist-credit": [{"name": "Various Artists"}],
            "media": [{"position": 1, "tracks": [track]}],
        }
    )
    card = MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=1, offset=0).items[0]
    assert card.title == "Song" and card.recording_title == "Song (Live)"
    assert card.disambiguation == "live at Venue"
    assert card.artist is None and card.download_search_query is None
    recording["artist-credit"] = [{"name": "Actual Artist"}]
    card = MusicBrainzDiscoveryProvider(http).release_tracks(release_id, limit=1, offset=0).items[0]
    assert card.download_search_query == "Actual Artist Song (Live)"
