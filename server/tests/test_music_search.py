from __future__ import annotations

import pytest
from autplay.domain.library import LibraryCommandError
from autplay.domain.music_search import (
    MusicSearchKind,
    normalize_music_query,
    validate_music_search,
)


def test_query_normalization_retains_versions_punctuation_and_unicode() -> None:
    name = "\u041c\u043e\u0441\u043a\u0432\u0430"
    assert (
        normalize_music_query(f"  AC/DC\tLive  (Remix)\n{name}  ") == f"AC/DC Live (Remix) {name}"
    )
    assert normalize_music_query("100%_\\cover") == "100%_\\cover"
    assert normalize_music_query("a" * 200) == "a" * 200


@pytest.mark.parametrize("query", ["", " \t\n", "a" * 201, "track\x00name", "track\x7fname"])
def test_invalid_queries_cannot_become_library_scans(query: str) -> None:
    with pytest.raises(LibraryCommandError, match="search_query_invalid"):
        normalize_music_query(query)


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_programmatic_search_limits_remain_bounded(limit: int) -> None:
    with pytest.raises(LibraryCommandError, match="search_limit_invalid"):
        validate_music_search("track", limit, MusicSearchKind.ALL)


def test_programmatic_search_rejects_unsupported_kinds() -> None:
    with pytest.raises(LibraryCommandError, match="search_kind_invalid"):
        validate_music_search("track", 1, "playlist")  # type: ignore[arg-type]
