from __future__ import annotations

import json
from uuid import uuid4

import pytest

from local_music_acquisition.source_metadata import (
    MAX_EVIDENCE_BYTES,
    bounded_source_metadata,
    native_metadata,
    yt_dlp_metadata,
)


def test_explicit_recording_tags_keep_versions_and_real_release_precision() -> None:
    recording = str(uuid4())
    result = yt_dlp_metadata(
        {
            "id": "abcdefghijk",
            "track": "Песня (Live)",
            "artist": "Artist, Guest",
            "album": "Album",
            "album_artist": "Album Artist",
            "release_year": 2021,
            "upload_date": "20241001",
            "uploader": "Uploader",
            "series": "Series",
            "mb_recording_id": recording,
            "track_number": 2,
            "disc_number": 1,
            "isrc": "USABC2100001",
            "genres": ["Jazz", "Jazz"],
        },
        provider="yt_dlp",
    )
    assert result is not None
    assert result["provider"] == "YOUTUBE"
    assert result["fields"] == {
        "title": "Песня (Live)",
        "artist": "Artist, Guest",
        "album": "Album",
        "album_artist": "Album Artist",
        "release_date": "2021",
        "track_number": 2,
        "disc_number": 1,
        "mb_recording_id": recording,
        "genres": ["Jazz"],
    }
    assert result["external_ids"] == {"isrc": "USABC2100001"}
    assert "Uploader" not in json.dumps(result)


def test_uploader_video_title_series_and_upload_date_are_not_music_tags() -> None:
    result = yt_dlp_metadata(
        {
            "id": "abcdefghijk",
            "title": "Artist - Song",
            "uploader": "Artist",
            "series": "Album",
            "upload_date": "20260101",
        },
        provider="yt_dlp",
    )
    assert result is not None
    assert result["fields"] == {}
    assert set(result) == {
        "schema_version",
        "provider",
        "source_id",
        "fields",
        "external_ids",
        "artwork",
    }


@pytest.mark.parametrize(
    "value,expected",
    [("20210102", "2021-01-02"), ("2021-02", "2021-02"), ("20210230", None), ("2021-00-00", None)],
)
def test_dates_are_validated_without_manufacturing_a_day(value: str, expected: str | None) -> None:
    result = yt_dlp_metadata({"id": "1", "release_date": value}, provider="bandcamp")
    assert result is not None
    assert result["fields"].get("release_date") == expected  # type: ignore[union-attr]


def test_release_timestamp_is_explicit_and_publication_timestamp_ignored() -> None:
    result = yt_dlp_metadata(
        {"id": "1", "release_timestamp": 1396483200, "timestamp": 1704067200},
        provider="bandcamp",
    )
    assert result is not None
    assert result["fields"] == {"release_date": "2014-04-03"}


def test_soundcloud_avatar_fallback_never_becomes_album_art() -> None:
    result = yt_dlp_metadata(
        {
            "id": "1",
            "album": "Album",
            "thumbnails": [{"url": "https://i1.sndcdn.com/avatars-fixture-large.jpg"}],
        },
        provider="soundcloud",
    )
    assert result is not None
    assert result["artwork"] == [
        {
            "kind": "thumbnail",
            "source_id": "1",
            "url": "https://i1.sndcdn.com/avatars-fixture-large.jpg",
        }
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://i1.sndcdn.com/art.jpg",
        "https://127.0.0.1/art.jpg",
        "https://i1.sndcdn.com.evil.test/art.jpg",
        "https://user:secret@i1.sndcdn.com/art.jpg",
        "https://i1.sndcdn.com/art.jpg?token=secret",
        "https://i1.sndcdn.com:443/art.jpg",
        "https://i1.sndcdn.com/art.jpg#secret",
        "file:///private/art.jpg",
    ],
)
def test_invalid_optional_artwork_keeps_valid_fields(url: str) -> None:
    result = native_metadata(
        "soundcloud",
        "1",
        {"title": "Song", "release_date": "bad"},
        artwork=[{"kind": "album", "url": url, "source_id": "1"}],
    )
    assert result is not None
    assert result["fields"] == {"title": "Song"}
    assert result["artwork"] == []
    assert "secret" not in json.dumps(result)


def test_bounded_fields_ids_and_four_artwork_hints() -> None:
    result = native_metadata(
        "jamendo",
        "1",
        {
            "title": "Song",
            "album": "x" * 501,
            "track_number": True,
            "mb_release_id": "not-uuid",
            "genres": ["Jazz"],
        },
        native_album_id="123",
        native_artist_id="456",
        artwork=[
            {
                "kind": "album",
                "source_id": "album:123",
                "url": "https://usercontent.jamendo.com?type=album&id=123&width=600",
            }
        ]
        * 8,
    )
    assert result is not None
    assert result["fields"] == {"title": "Song", "genres": ["Jazz"]}
    assert result["external_ids"] == {"native_album_id": "123", "native_artist_id": "456"}
    assert len(result["artwork"]) == 4  # type: ignore[arg-type]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= MAX_EVIDENCE_BYTES
    assert bounded_source_metadata(result, provider="bandcamp") is None


def test_too_large_evidence_is_omitted_not_truncated_into_a_false_identity() -> None:
    result = native_metadata(
        "jamendo",
        "1",
        {key: "界" * 500 for key in ("title", "artist", "album", "album_artist")}
        | {"genres": ["界" * 99 + str(n) for n in range(10)]},
        artwork=[
            {
                "kind": "album",
                "source_id": "1",
                "url": "https://usercontent.jamendo.com/" + "界" * 900,
            }
        ]
        * 4,
    )
    assert result is None


@pytest.mark.parametrize("source_id", [None, True, "https://private.test/path", "id?token=secret"])
def test_invalid_native_identity_is_omitted(source_id: object) -> None:
    assert native_metadata("jamendo", source_id, {"title": "Song"}) is None
