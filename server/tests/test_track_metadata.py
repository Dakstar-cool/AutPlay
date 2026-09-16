"""Descriptive metadata keeps uncertainty and explicit user choices intact."""

import pytest
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataDocument,
    MetadataQuery,
    automatic_candidate,
    partial_date,
    validate_fields,
)


@pytest.mark.parametrize("value", ["1998", "1998-04", "2000-02-29"])
def test_dates_preserve_their_actual_precision(value: str) -> None:
    assert partial_date(value) == value


@pytest.mark.parametrize(
    "value", ["", "2025-02-29", "2024-13", "2024-01-00", "0", "2024-1", "2024-01-01T00:00:00Z"]
)
def test_invalid_dates_do_not_become_fabricated_dates(value: str) -> None:
    with pytest.raises(ValueError):
        partial_date(value)


def test_manual_clear_and_correction_survive_late_provider_refresh() -> None:
    doc = MetadataDocument().merge(
        {"album": "Old", "release_date": "1998"},
        FieldEvidence("EMBEDDED", "sha256:test", "2026-09-16"),
    )
    corrected = doc.merge(
        {"album": "Chosen", "release_date": None},
        FieldEvidence("USER", "edit:1", "2026-09-16"),
    )
    refreshed = corrected.merge(
        {"album": "Wrong edition", "release_date": "2007", "label": "Label"},
        FieldEvidence("MUSICBRAINZ", "release:test", "2026-09-17"),
    )
    assert refreshed.fields == {"album": "Chosen", "release_date": None, "label": "Label"}
    assert refreshed.provenance["album"].source == "USER"
    assert refreshed.provenance["label"].source == "MUSICBRAINZ"


def test_auto_match_requires_unique_edition_and_duration_agreement() -> None:
    query = MetadataQuery("Song", "Artist", "Album", 180000)
    first = MetadataCandidate(
        "one", {"title": "Song", "artist": "Artist", "album": "Album"}, 181000, 100, "release:one"
    )
    second = MetadataCandidate("two", first.fields, 181000, 100, "release:two")
    assert automatic_candidate(query, (first,)) == first
    assert automatic_candidate(query, (first, second)) is None
    assert (
        automatic_candidate(MetadataQuery("Song (live)", "Artist", "Album", 180000), (first,))
        is None
    )
    assert automatic_candidate(MetadataQuery("Song", "Artist", "Album", 200000), (first,)) is None
    assert automatic_candidate(MetadataQuery("Song", "Artist", "Album"), (first,)) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"path": "private"},
        {"track_number": True},
        {"genres": ["x" * 101]},
        {"album": "x" * 501},
        {"album": "a\nb"},
        {"mb_release_id": "invalid"},
    ],
)
def test_malformed_or_locator_fields_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_fields(fields)
