from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_music_acquisition.matching import candidate_matches
from local_music_acquisition.models import PlaylistItem
from local_music_acquisition.normalization import load_catalog, normalize_items
from local_music_acquisition.queue import enqueue
from local_music_acquisition.queue_store import read_json


def test_normalization_is_idempotent_and_preserves_recording_versions() -> None:
    rows = (PlaylistItem(1, "  Artist\u00a0Name\u200b ", "Song\u2019s  (Live)"),)
    normalized, changes = normalize_items(rows)
    assert normalized[0].artist == "Artist Name"
    assert normalized[0].title == "Song's (Live)"
    assert changes[0]["original"]["title"] == rows[0].title
    assert normalize_items(normalized) == (normalized, [])


@pytest.mark.parametrize("separator", ["vs.", "VS", "versus"])
def test_vs_credits_are_reusable_and_do_not_change_song_titles(separator: str) -> None:
    row = PlaylistItem(1, f"One {separator} Two {separator} Three", "Song vs. Time (Live)")
    normalized, changes = normalize_items((row,))
    assert normalized[0].artist == "One, Two, Three"
    assert normalized[0].title == row.title
    assert len(changes) == 1
    assert normalize_items(normalized) == (normalized, [])
    assert candidate_matches(
        {"artist": row.artist, "track": row.title}, artist="Three, One, Two", title=row.title
    )
    assert not candidate_matches(
        {"artist": row.artist, "track": row.title}, artist="One, Two", title=row.title
    )


def test_catalog_is_applied_before_queue_identity_and_has_audit(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Artist - Brok...\nArtist - Full Title\n", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corrections": [
                    {
                        "source_artist": "Artist",
                        "source_title": "Brok...",
                        "artist": "Artist",
                        "title": "Full Title",
                        "evidence": "https://artist.example/release",
                    }
                ],
            }
        )
    )
    root = tmp_path / "queue"
    status = enqueue(source, root, tmp_path / "music", normalization_catalog=catalog)
    assert status["unique_tracks"] == 1
    assert status["duplicates"] == 1
    audit = read_json(root / "normalization.json")
    assert audit["changes"][0]["original"]["title"] == "Brok..."
    assert audit["changes"][0]["normalized"]["title"] == "Full Title"
    assert source.read_text() == "Artist - Brok...\nArtist - Full Title\n"


def test_conflicting_catalog_entries_are_rejected(tmp_path: Path) -> None:
    entry = {
        "source_artist": "Artist",
        "source_title": "Song",
        "artist": "Artist",
        "title": "Song",
        "evidence": "reviewed",
    }
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps({"schema_version": 1, "corrections": [entry, entry | {"title": "Other"}]})
    )
    with pytest.raises(ValueError, match="normalization_catalog_invalid"):
        load_catalog(path)


@pytest.mark.parametrize(
    "candidate",
    [
        {"title": "Artist - Song (Official Audio)"},
        {"artist": "Guest, Artist", "track": "Song"},
        {"artist": "Artist", "track": "Song (feat. Guest)"},
    ],
)
def test_feature_credit_moves_without_becoming_a_different_artist(candidate: dict) -> None:
    if "title" in candidate:
        assert candidate_matches(candidate, artist="Artist", title="Song")
    else:
        assert candidate_matches(candidate, artist="Artist, Guest", title="Song (feat. Guest)")


@pytest.mark.parametrize("label", ["Live", "Remix", "Cover", "Acoustic", "Sped Up", "Slowed"])
def test_search_never_strips_recording_version(label: str) -> None:
    assert not candidate_matches(
        {"title": f"Artist - Song ({label})"}, artist="Artist", title="Song"
    )


def test_topic_metadata_is_accepted_but_unverified_uploader_is_not() -> None:
    assert candidate_matches(
        {"title": "Song", "uploader": "Artist - Topic"}, artist="Artist", title="Song"
    )
    assert not candidate_matches(
        {"title": "Song", "uploader": "Artist"}, artist="Artist", title="Song"
    )
    assert not candidate_matches({"title": "Artist, Other - Song"}, artist="Artist", title="Song")


def test_pipe_separator_does_not_strip_live_version() -> None:
    assert candidate_matches(
        {"title": "Artist | Song (Official Music Video)"}, artist="Artist", title="Song"
    )
    assert not candidate_matches(
        {"title": "Artist | Song (Live Soundcheck)"}, artist="Artist", title="Song"
    )


def test_remixer_credits_may_move_but_cannot_disappear() -> None:
    candidate = {"title": "Artist - Song (Remixer One x Remixer Two Remix)"}
    assert candidate_matches(
        candidate, artist="Artist, Remixer One, Remixer Two", title="Song (Remix)"
    )
    assert not candidate_matches(candidate, artist="Artist", title="Song (Remix)")
    assert not candidate_matches(candidate, artist="Artist, Remixer One, Remixer Two", title="Song")
    assert not candidate_matches(candidate, artist="Artist, Other Remixer", title="Song (Remix)")
