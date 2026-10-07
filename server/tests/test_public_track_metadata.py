"""Provider parsing and ambiguity are tested without depending on live catalog contents."""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from autplay.adapters.media.track_metadata import fields_from_tags
from autplay.adapters.public_track_metadata import (
    MusicBrainzMetadataProvider,
    PublicMetadataHttp,
    validate_public_url,
)
from autplay.domain.track_metadata import MetadataQuery, automatic_candidate, parse_source_metadata
from autplay.ports.track_metadata import MetadataProviderError


class Http(PublicMetadataHttp):
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.urls: list[str] = []

    def json(self, url: str, *, post: bytes | None = None) -> dict[str, Any]:
        del post
        self.urls.append(url)
        return self.document


def recording(number: int, duration: int, releases: Iterable[int]) -> dict[str, Any]:
    return {
        "id": str(UUID(int=number)),
        "title": "Song",
        "artist-credit": [{"name": "Artist"}],
        "length": duration,
        "score": 100,
        "releases": [{"id": str(UUID(int=i)), "title": "Album", "date": "1998"} for i in releases],
    }


def test_truncated_results_cannot_create_a_false_unique_match() -> None:
    provider = MusicBrainzMetadataProvider(
        Http(
            {
                "count": 3,
                "recordings": [
                    recording(1, 200000, range(10, 14)),
                    recording(2, 180000, [20]),
                    recording(3, 180000, [30]),
                ],
            }
        )
    )
    query = MetadataQuery("Song", "Artist", "Album", 180000)
    candidates = provider.search(query)
    assert len(candidates) == 5
    assert not any(item.auto_eligible for item in candidates)
    assert automatic_candidate(query, candidates) is None


def test_malformed_skipped_evidence_cannot_create_a_false_unique_match() -> None:
    http = Http({"count": 2, "recordings": [None, recording(1, 180000, [10])]})
    query = MetadataQuery("Song", "Artist", "Album", 180000)
    candidates = MusicBrainzMetadataProvider(http).search(query)
    assert len(candidates) == 1 and automatic_candidate(query, candidates) is None


def test_pagination_and_multiple_editions_remain_reviewable() -> None:
    query = MetadataQuery("Song", "Artist", "Album", 180000)
    unique = MusicBrainzMetadataProvider(
        Http({"count": 1, "recordings": [recording(1, 180000, [10])]})
    ).search(query)
    assert automatic_candidate(query, unique) is not None
    truncated = MusicBrainzMetadataProvider(
        Http({"count": 50, "recordings": [recording(1, 180000, [10])]})
    ).search(query)
    assert automatic_candidate(query, truncated) is None
    editions = MusicBrainzMetadataProvider(
        Http({"count": 1, "recordings": [recording(1, 180000, [10, 11])]})
    ).search(query)
    assert automatic_candidate(query, editions) is None


@pytest.mark.parametrize(
    "document", [{"recordings": None}, {"recordings": [None]}, {"recordings": [{"releases": None}]}]
)
def test_malformed_provider_shapes_never_escape_as_unclassified_errors(
    document: dict[str, Any],
) -> None:
    provider = MusicBrainzMetadataProvider(Http(document))
    try:
        assert provider.search(MetadataQuery("Song", "Artist")) == ()
    except MetadataProviderError as error:
        assert error.code == "metadata_response_invalid"


@pytest.mark.parametrize(
    "url",
    [
        "http://musicbrainz.org/ws/2/",
        "https://evil.test/",
        "https://musicbrainz.org@127.0.0.1/",
        "https://archive.org.evil.test/image",
        "file:///secret",
        "https://musicbrainz.org:444/a",
    ],
)
def test_provider_does_not_follow_arbitrary_or_private_urls(url: str) -> None:
    with pytest.raises(MetadataProviderError, match="metadata_url_rejected"):
        validate_public_url(url, artwork=True)


def test_allowlisted_hostname_resolving_to_private_address_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(MetadataProviderError, match="metadata_url_rejected"):
        validate_public_url("https://musicbrainz.org/ws/2/")


def test_embedded_tags_preserve_date_precision_and_ignore_invalid_values() -> None:
    assert fields_from_tags(
        {
            "ALBUM": "Record",
            "DATE": "2001-04",
            "YEAR": "2001",
            "ORIGINALDATE": "1998",
            "TRACK": "3/10",
            "DISC": "1/2",
            "genre": "Jazz",
            "musicbrainz_albumid": "bad",
            "recordingdate": "2025-02-30",
        }
    ) == {
        "album": "Record",
        "release_date": "2001-04",
        "original_release_date": "1998",
        "track_number": 3,
        "disc_number": 1,
        "genres": ["Jazz"],
    }


def test_acoustid_keeps_ambiguity_and_bounds_candidates() -> None:
    provider = MusicBrainzMetadataProvider(
        Http(
            {
                "status": "ok",
                "results": [
                    {"score": 1, "recordings": [{"id": str(UUID(int=i))} for i in range(1, 8)]},
                    {"score": 0.5, "recordings": [{"id": str(UUID(int=99))}]},
                ],
            }
        )
    )
    assert provider.acoustid("opaque", 180, "test-key") == tuple(
        str(UUID(int=i)) for i in range(1, 6)
    )


def test_acoustid_malformed_response_is_classified() -> None:
    with pytest.raises(MetadataProviderError, match="metadata_response_invalid"):
        MusicBrainzMetadataProvider(Http({"status": "ok", "results": None})).acoustid(
            "fp", 180, "test-key"
        )


def test_release_requires_recording_membership() -> None:
    candidates = MusicBrainzMetadataProvider(
        Http({"recordings": [recording(1, 180000, [10])]})
    ).search(MetadataQuery("Song", "Artist"))
    provider = MusicBrainzMetadataProvider(
        Http({"id": str(UUID(int=10)), "title": "Album", "media": []})
    )
    with pytest.raises(MetadataProviderError, match="metadata_release_recording_missing"):
        provider.release(candidates[0])


def test_http_retry_after_and_response_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx2

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    http = PublicMetadataHttp()
    http.client.close()
    http.client = httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(503, headers={"Retry-After": "300"})
        )
    )
    with pytest.raises(MetadataProviderError) as error:
        http.get("https://musicbrainz.org/ws/2/recording")
    assert error.value.retryable and error.value.retry_after_seconds == 300
    http.client.close()
    http.client = httpx2.Client(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, content=b"x" * 1048577))
    )
    with pytest.raises(MetadataProviderError, match="metadata_response_too_large"):
        http.get("https://musicbrainz.org/ws/2/recording")
    http.client.close()


class LadderHttp(Http):
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        super().__init__({})
        self.documents = documents

    def json(self, url: str, *, post: bytes | None = None) -> dict[str, Any]:
        del post
        self.urls.append(url)
        return self.documents[min(len(self.urls) - 1, len(self.documents) - 1)]


def release_document(*, positions: tuple[int, ...] = (3,)) -> dict[str, Any]:
    return {
        "id": str(UUID(int=10)),
        "title": "Album",
        "status": "Official",
        "date": "1998-04",
        "artist-credit": [{"name": "Album Artist"}],
        "release-group": {
            "id": str(UUID(int=100)),
            "primary-type": "Album",
            "secondary-types": [],
            "first-release-date": "1997",
        },
        "genres": [{"name": "Future unknown genre"}],
        "media": [
            {
                "position": 2,
                "track-count": len(positions),
                "tracks": [
                    {
                        "position": pos,
                        "title": "Song",
                        "artist-credit": [{"name": "Artist"}],
                        "length": 180000,
                        "recording": {
                            "id": str(UUID(int=1)),
                            "title": "Song",
                            "disambiguation": "",
                        },
                    }
                    for pos in positions
                ],
            }
        ],
    }


def test_lookup_query_ladder_keeps_real_album_duration_and_escaped_artist_evidence() -> None:
    http = LadderHttp(
        [{"count": 0, "recordings": []}, {"count": 1, "recordings": [recording(1, 180000, [10])]}]
    )
    query = MetadataQuery("Artist - Song (Official Video)", "Artist", "Album", 180000)
    candidates = MusicBrainzMetadataProvider(http).search(query)
    expressions = [parse_qs(urlsplit(url).query)["query"][0] for url in http.urls]
    assert len(expressions) == 2
    assert 'recording:"Song"' in expressions[1]
    assert all(
        'release:"Album"' in expr and "dur:[178000 TO 182000]" in expr for expr in expressions
    )
    assert automatic_candidate(query, candidates)
    assert query.title == "Artist - Song (Official Video)"


def test_ladder_cannot_reset_an_earlier_truncation_veto() -> None:
    http = LadderHttp(
        [{"count": 25, "recordings": []}, {"count": 1, "recordings": [recording(1, 180000, [10])]}]
    )
    query = MetadataQuery("Song (Official Video)", "Artist", "Album", 180000)
    assert automatic_candidate(query, MusicBrainzMetadataProvider(http).search(query)) is None
    assert len(http.urls) == 2


def test_isrc_rung_is_bounded_evidence_and_cannot_merge_same_name_recordings() -> None:
    source = parse_source_metadata(
        {
            "schema_version": 1,
            "provider": "BANDCAMP",
            "source_id": "1",
            "fields": {},
            "external_ids": {"isrc": "GBABC9800001"},
        }
    )
    http = Http(
        {"count": 2, "recordings": [recording(1, 180000, [10]), recording(2, 180000, [11])]}
    )
    query = MetadataQuery("Song", "Artist", "Album", 180000, source_metadata=source)
    candidates = MusicBrainzMetadataProvider(http).search(query)
    assert "isrc:GBABC9800001" in parse_qs(urlsplit(http.urls[0]).query)["query"][0]
    assert automatic_candidate(query, candidates) is None


def test_maximum_three_rungs_do_not_remove_version_markers_or_duration() -> None:
    http = Http({"count": 0, "recordings": []})
    MusicBrainzMetadataProvider(http).search(
        MetadataQuery("Song (Remix) [Official Video]", "Artist", duration_ms=180000)
    )
    assert 1 <= len(http.urls) <= 3
    assert all(
        "Remix" in parse_qs(urlsplit(url).query)["query"][0]
        or "remix" in parse_qs(urlsplit(url).query)["query"][0]
        for url in http.urls
    )


def test_exact_known_edition_uses_direct_complete_membership_lookup() -> None:
    http = Http(release_document())
    query = MetadataQuery(
        "Song", "Artist", "Album", 180000, str(UUID(int=1)), release_mbid=str(UUID(int=10))
    )
    candidate = MusicBrainzMetadataProvider(http).search(query)[0]
    assert len(http.urls) == 1 and "/release/" in http.urls[0]
    assert candidate.release_hydrated and candidate.exact_for(query)
    assert candidate.fields["track_number"] == 3 and candidate.fields["disc_number"] == 2
    assert candidate.fields["album_artist"] == "Album Artist"
    assert candidate.fields["original_release_date"] == "1997"
    assert candidate.fields["genres"] == ["Future unknown genre"]


@pytest.mark.parametrize(
    "patch",
    [
        {"status": "Bootleg"},
        {"status": "Future status"},
        {"status": None},
        {"release-group": {"secondary-types": ["Live"]}},
        {"release-group": {"secondary-types": ["Remix"]}},
    ],
)
def test_hydrated_status_and_version_types_revalidate_automatic_choice(
    patch: dict[str, Any],
) -> None:
    query = MetadataQuery(
        "Song", "Artist", "Album", 180000, str(UUID(int=1)), release_mbid=str(UUID(int=10))
    )
    candidates = MusicBrainzMetadataProvider(Http(release_document() | patch)).search(query)
    assert automatic_candidate(query, candidates) is None
    assert candidates[0].release_status == patch.get("status", "Official")


def test_duplicate_recording_positions_do_not_claim_first_track_number() -> None:
    query = MetadataQuery(
        "Song", "Artist", "Album", 180000, str(UUID(int=1)), release_mbid=str(UUID(int=10))
    )
    candidate = MusicBrainzMetadataProvider(Http(release_document(positions=(3, 8)))).search(query)[
        0
    ]
    assert candidate.release_position_ambiguous and not candidate.auto_eligible
    assert "track_number" not in candidate.fields and "disc_number" not in candidate.fields


def test_incomplete_exact_edition_tracklist_cannot_create_first_position_proof() -> None:
    document = release_document()
    document["media"][0]["track-count"] = 20
    query = MetadataQuery(
        "Song", "Artist", "Album", 180000, str(UUID(int=1)), release_mbid=str(UUID(int=10))
    )
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.release_position_ambiguous and automatic_candidate(query, (candidate,)) is None


def test_hydrated_track_title_cannot_hide_live_recording_title() -> None:
    document = release_document()
    document["media"][0]["tracks"][0]["recording"]["title"] = "Song (Live)"
    query = MetadataQuery(
        "Song", "Artist", "Album", 180000, str(UUID(int=1)), release_mbid=str(UUID(int=10))
    )
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.fields["title"] == "Song" and not candidate.exact_for(query)


def test_release_position_and_duration_conflicts_are_not_overridden_by_exact_ids() -> None:
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        180000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        track_number=2,
    )
    candidate = MusicBrainzMetadataProvider(Http(release_document())).search(query)[0]
    assert not candidate.exact_for(query)
    assert not candidate.exact_for(
        MetadataQuery("Song", "Artist", "Album", 200000, str(UUID(int=1)))
    )


def test_cover_is_requested_only_for_exact_selected_release() -> None:
    class CoverHttp(Http):
        def get(
            self, url: str, *, artwork: bool = False, post: bytes | None = None
        ) -> bytes | None:
            del post
            assert artwork
            self.urls.append(url)
            return b"cover"

    http = CoverHttp({})
    assert MusicBrainzMetadataProvider(http).cover(str(UUID(int=10))) == b"cover"
    assert http.urls == [f"https://coverartarchive.org/release/{UUID(int=10)}/front-500"]


def repeated_occurrence_document() -> dict[str, Any]:
    document = release_document(positions=(3, 8))
    tracks = document["media"][0]["tracks"]
    for track, identity in zip(tracks, (str(UUID(int=103)), str(UUID(int=108))), strict=True):
        track["id"] = identity
    tracks[1]["length"] = 175000
    return document


def test_exact_release_track_uuid_selects_second_recording_occurrence() -> None:
    http = Http(repeated_occurrence_document())
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        track_number=8,
        disc_number=2,
        release_track_mbid=str(UUID(int=108)),
    )
    candidate = MusicBrainzMetadataProvider(http).search(query)[0]
    assert candidate.exact_for(query) and len(http.urls) == 1
    assert not candidate.release_position_ambiguous
    assert candidate.mb_release_track_id == query.release_track_mbid
    assert candidate.fields["track_number"] == 8 and candidate.fields["disc_number"] == 2
    assert candidate.fields["mb_recording_id"] == query.recording_mbid
    assert candidate.fields["mb_release_id"] == query.release_mbid
    assert candidate.fields["release_date"] == "1998-04"
    assert candidate.duration_ms == 175000


def test_repeated_recording_stays_ambiguous_without_exact_occurrence_uuid() -> None:
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        track_number=8,
        disc_number=2,
    )
    candidate = MusicBrainzMetadataProvider(Http(repeated_occurrence_document())).search(query)[0]
    assert candidate.release_position_ambiguous and candidate.mb_release_track_id is None
    assert not candidate.exact_for(query) and "track_number" not in candidate.fields


@pytest.mark.parametrize("wrong", ["other_recording", "absent_id", "unknown_id"])
def test_exact_occurrence_must_belong_to_both_release_and_recording(wrong: str) -> None:
    document = repeated_occurrence_document()
    track = document["media"][0]["tracks"][1]
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    if wrong == "other_recording":
        track["recording"]["id"] = str(UUID(int=2))
    elif wrong == "absent_id":
        del track["id"]
    else:
        query = replace(query, release_track_mbid=str(UUID(int=999)))
    with pytest.raises(MetadataProviderError, match="metadata_release_track_missing"):
        MusicBrainzMetadataProvider(Http(document)).search(query)


def test_exact_occurrence_cannot_erase_truncated_tracklist_or_duplicate_track_id() -> None:
    document = repeated_occurrence_document()
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    document["media"][0]["track-count"] = 30
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.release_position_ambiguous and candidate.mb_release_track_id is None
    document = repeated_occurrence_document()
    document["media"][0]["tracks"][0]["id"] = str(UUID(int=108))
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.release_position_ambiguous and not candidate.exact_for(query)
    document["media"][0]["tracks"][0]["recording"]["id"] = str(UUID(int=2))
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.release_position_ambiguous and not candidate.exact_for(query)


def test_exact_occurrence_preserves_version_and_actual_duration_conflicts() -> None:
    document = repeated_occurrence_document()
    document["media"][0]["tracks"][1]["recording"]["disambiguation"] = "live"
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.mb_release_track_id == query.release_track_mbid and not candidate.exact_for(
        query
    )
    document = repeated_occurrence_document()
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert not candidate.exact_for(replace(query, duration_ms=185000))


def test_exact_occurrence_accepts_observed_live_recording_title_and_rejects_plain_title() -> None:
    document = repeated_occurrence_document()
    document["media"][0]["tracks"][1]["recording"]["title"] = "Song (Live)"
    query = MetadataQuery(
        "Song (Live)",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    candidate = MusicBrainzMetadataProvider(Http(document)).search(query)[0]
    assert candidate.fields["title"] == "Song" and candidate.recording_title == "Song (Live)"
    assert candidate.exact_for(query) and automatic_candidate(query, (candidate,)) == candidate
    assert not candidate.exact_for(replace(query, title="Song"))
    assert not candidate.exact_for(replace(query, title="Song (Remix)"))


def test_stored_occurrence_evidence_is_revalidated_on_edition_refresh() -> None:
    provider = MusicBrainzMetadataProvider(Http(repeated_occurrence_document()))
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    candidate = provider.search(query)[0]
    refreshed = provider.release(candidate)
    assert refreshed.mb_release_track_id == candidate.mb_release_track_id
    assert refreshed.fields["track_number"] == 8


def test_occurrence_lookup_needs_recording_and_edition_context() -> None:
    with pytest.raises(MetadataProviderError, match="metadata_release_track_context_invalid"):
        MusicBrainzMetadataProvider(Http({})).search(
            MetadataQuery("Song", "Artist", release_track_mbid=str(UUID(int=108)))
        )
