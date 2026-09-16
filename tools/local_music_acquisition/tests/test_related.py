from __future__ import annotations

import pytest

from local_music_acquisition.models import PlaylistItem
from local_music_acquisition.normalization import normalize_query, search_variants
from local_music_acquisition.related import (
    RelatedCandidate,
    candidate_score,
    identity_fields,
    select_candidates,
)


@pytest.mark.parametrize(
    ("artist", "title", "actual_artist", "actual_title"),
    [
        ("Hello Operator", "Created a Monster", "Hello Operator", "I Created a Monster"),
        ("Tones And", "Johnny Run Away", "Tones And I", "Johnny Run Away"),
        (
            "Blue October",
            "The Girl Who Stole My Hea",
            "Blue October",
            "The Girl Who Stole My Heart",
        ),
        ("M8s3", "Safe", "M83", "Safe"),
        (
            "AnnenMayKantereit, K.1.Z",
            "Hurra die Welt geht unter (",
            "AnnenMayKantereit, K.I.Z",
            "Hurra die Welt geht unter",
        ),
    ],
)
def test_observed_ocr_and_truncation_cases_select_actual_identity(
    artist, title, actual_artist, actual_title
):
    original = PlaylistItem(1, artist, title, "Parent album", expected_duration_seconds=999)
    candidate = RelatedCandidate("fixture", actual_artist, actual_title)
    assert select_candidates(original, [candidate]) == [candidate]
    assert candidate.item(1).album is None
    assert candidate.item(1).expected_duration_seconds is None


def test_query_repairs_are_contextual_and_do_not_mutate_identity():
    title = "He \u0432\u0430\u043b\u044f\u0439 \u0434\u0443\u0440\u0430\u043a\u0430"
    assert normalize_query(title).startswith("\u041d\u0435 ")
    assert normalize_query("He Is A Rock Star") == "He Is A Rock Star"
    item = PlaylistItem(1, "Artist, Guest", "Song (feat. Gues")
    assert search_variants(item)[1] == PlaylistItem(1, "Artist", "Song")
    assert item.title == "Song (feat. Gues"
    assert normalize_query(normalize_query(title)) == normalize_query(title)


def test_similar_artist_does_not_admit_a_different_composition():
    item = PlaylistItem(1, "Anne-Sophie Versnaeyen, Gabi", "Whispers of the Soul")
    candidate = RelatedCandidate("fixture", "Anne-Sophie Versnaeyen", "Whispers of Enigma")
    assert candidate_score(item, candidate) == 0
    assert not select_candidates(item, [candidate])


def test_dedup_before_cap_and_versions_remain_distinct():
    item = PlaylistItem(1, "Artist", "The So")
    candidates = [RelatedCandidate("one", "Artist", "The Song (Live)")] * 4
    candidates += [
        RelatedCandidate("two", "ARTIST", "the song (live)"),
        RelatedCandidate("one", "Artist", "The Song (Remix)"),
        RelatedCandidate("one", "Artist", "The Song"),
        RelatedCandidate("one", "Artist", "The Song (Acoustic)"),
    ]
    selected = select_candidates(item, candidates)
    assert len(selected) == 3
    assert len({c.title.casefold() for c in selected}) == 3
    assert select_candidates(PlaylistItem(1, "Artist", "The Song"), candidates) == [candidates[-2]]


def test_short_artist_and_title_cannot_fuzzily_match_anything():
    assert not select_candidates(PlaylistItem(1, "AB", "Up"), [RelatedCandidate("x", "AC", "Up")])
    assert identity_fields({"title": "Song", "uploader": "Random Channel"}) is None
    assert identity_fields({"title": "Song (Live)", "uploader": "Artist - Topic"}) == (
        "Artist",
        "Song (Live)",
    )
