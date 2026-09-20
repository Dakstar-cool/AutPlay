"""Provider parsing and ambiguity are tested without depending on live catalog contents."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import pytest
from autplay.adapters.media.track_metadata import fields_from_tags
from autplay.adapters.public_track_metadata import (
    MusicBrainzMetadataProvider,
    PublicMetadataHttp,
    validate_public_url,
)
from autplay.domain.track_metadata import MetadataQuery, automatic_candidate
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
