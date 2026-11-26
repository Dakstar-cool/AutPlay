"""Descriptive metadata keeps uncertainty and explicit user choices intact."""

import json
from dataclasses import asdict, replace
from uuid import UUID

import pytest
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataDocument,
    MetadataQuery,
    album_group_document,
    album_group_for,
    automatic_candidate,
    lookup_title,
    metadata_search_terms,
    native_album_group_for,
    parse_album_group,
    parse_source_metadata,
    partial_date,
    sanitize_source_metadata,
    source_metadata_document,
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


def test_source_site_is_neither_a_search_term_nor_a_performer() -> None:
    raw = MetadataQuery("Artist - Track [muzmo.ru]", "hitmos Artist", duration_ms=180000)
    assert metadata_search_terms(raw.title, raw.artist) == (
        ("Artist - Track", "Artist"),
        ("Track", "Artist"),
    )
    candidate = MetadataCandidate(
        "one", {"title": "Track", "artist": "Artist"}, 180000, 100, "release:one"
    )
    assert candidate.exact_for(raw)
    assert not candidate.exact_for(replace(raw, artist="[muzmo.ru]"))
    assert metadata_search_terms("Track", "[muzmo.ru]") == ()


def test_featured_track_matches_both_catalogue_layouts_but_requires_the_guest() -> None:
    from autplay.domain.catalog_artwork import recording_labels_match
    from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard

    title, artist, guest = "Рядом быть", "После 11", "Хелависа"
    credit, source = f"{artist} feat. {guest}", f"{title} feat. {guest}"
    query = MetadataQuery(source, artist, duration_ms=283875)
    terms = metadata_search_terms(source, artist)
    assert (title, credit) in terms and (title, artist) in terms
    candidate = MetadataCandidate(
        "one", {"title": title, "artist": credit}, 284000, 100, "release:one"
    )
    assert candidate.exact_for(query)
    assert replace(
        candidate, fields={"title": f"{title} (ft. {guest})", "artist": artist}
    ).exact_for(query)
    assert replace(
        candidate,
        fields={"title": title, "artist": artist},
        recording_title=source,
        release_hydrated=True,
        release_status="Official",
    ).exact_for(query)
    assert recording_labels_match(artist, source, credit, title)
    mbid = UUID(int=1)
    card = CatalogueTrackCard(
        CatalogueContextEntity.RECORDING, mbid, mbid, source, artist, duration_ms=284000
    )
    assert card.corroborates(title=title, artist=credit, measured_duration_ms=283875)
    for other in (artist, artist + " feat. Другой"):
        assert not replace(candidate, fields={"title": title, "artist": other}).exact_for(query)
        assert not recording_labels_match(artist, source, other, title)
        assert not card.corroborates(title=title, artist=other, measured_duration_ms=283875)
    assert not replace(candidate, fields={"title": title + " (Live)", "artist": credit}).exact_for(
        query
    )


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


@pytest.mark.parametrize(
    ("title", "artist", "expected"),
    [
        (
            "Би-2 - Полковнику никто не пишет (Официальный клип)",
            "Би-2",
            "Полковнику никто не пишет",
        ),
        ("Beyoncé — Déjà Vu [Official Video]", "Beyoncé", "Déjà Vu"),
        ("宇多田ヒカル - 光 (Official Audio)", "宇多田ヒカル", "光"),
        ("Björk - Jóga (Live in Paris) [HD]", "Björk", "Jóga (Live in Paris)"),
        ("Song (Remix) [Official Video]", "Artist", "Song (Remix)"),
        ("Song (Cover) [HQ]", "Artist", "Song (Cover)"),
        ("Lyrics", "Artist", "Lyrics"),
        ("Other - Song", "Artist", "Other - Song"),
    ],
)
def test_lookup_normalization_is_separate_from_display_text(
    title: str, artist: str, expected: str
) -> None:
    query = MetadataQuery(title, artist)
    assert lookup_title(query.title, query.artist) == expected
    assert query.title == title


@pytest.mark.parametrize(
    "marker",
    [
        "Live",
        "Remix",
        "Cover",
        "Sped Up",
        "Slowed",
        "2011 remaster",
        "radio edit",
        "edit",
        "mono",
        "stereo",
    ],
)
def test_provider_version_disambiguation_is_a_hard_conflict(marker: str) -> None:
    candidate = MetadataCandidate(
        "one",
        {"title": "Song", "artist": "Artist"},
        180000,
        100,
        "release:one",
        recording_disambiguation=marker,
    )
    assert not candidate.exact_for(MetadataQuery("Song", "Artist", duration_ms=180000))


@pytest.mark.parametrize(
    ("artist", "raw", "base", "edition", "duration"),
    [
        (
            "Chinawoman",
            "Left You At The Farm @16 тонн",
            "Left You At The Farm",
            "Live at 16 тонн",
            304000,
        ),
        (
            "Moto Boy",
            "Life is wasted on the living | Monitor Sessions",
            "Life is wasted on the living",
            "Monitor Sessions",
            223000,
        ),
        ("Максим Свобода", "По разные стороны (Mood Video)", "По разные стороны", None, 191000),
        (
            "Sam Tinnesz",
            "Play With Fire | Stabal Session",
            "Play With Fire",
            "Stabal Session",
            174927,
        ),
        (
            "Sam Tinnesz",
            "Play With Fire [Stabal ssesion]",
            "Play With Fire",
            "Stabal Session",
            174927,
        ),
    ],
)
def test_source_performance_editions_and_video_wrappers(
    artist: str, raw: str, base: str, edition: str | None, duration: int
) -> None:
    from autplay.domain.catalog_artwork import artwork_search_title, recording_labels_match
    from autplay.domain.track_metadata import metadata_search_terms
    from autplay.domain.track_names import project_names, track_names

    query = MetadataQuery(raw, artist, duration_ms=duration)
    display = f"{base} ({edition})" if edition else base
    assert track_names(raw, artist) == (display, artist)
    assert project_names({}, raw, artist)["fields"]["title"] == display
    assert track_names(display, artist) == (display, artist)
    assert lookup_title(raw, artist) == display
    terms = metadata_search_terms(raw, artist)
    assert terms[0] == (raw, artist) and (base, artist) in terms
    assert artwork_search_title(raw, artist) == base
    candidate = MetadataCandidate(
        "hit", {"title": base, "artist": artist}, duration, 100, "release"
    )
    assert candidate.exact_for(query) == (edition is None)
    assert recording_labels_match(artist, raw, artist, base) == (edition is None)
    assert recording_labels_match(artist, raw, artist, display)
    assert replace(candidate, recording_disambiguation=edition).exact_for(query)
    assert not replace(candidate, fields={"title": display, "artist": "Other artist"}).exact_for(
        query
    )
    assert not replace(candidate, recording_disambiguation="Other Sessions").exact_for(query)
    if edition:
        assert not replace(candidate, recording_disambiguation=edition).exact_for(
            MetadataQuery(base, artist, duration_ms=duration)
        )


@pytest.mark.parametrize(
    ("artist", "raw", "base", "edition", "catalog_title", "duration"),
    [
        ("кис-кис", "молчи speed up", "молчи", "Sped Up", "Молчи", 152400),
        (
            "aldn",
            "icantbelieveiletyougetaway | High quality instrumental",
            "icantbelieveiletyougetaway",
            "Instrumental",
            "i can\u2019t believe i let you get away",
            135941,
        ),
        (
            "Artist",
            "Some Song [super slowed & reverb]",
            "Some Song",
            "Super Slowed + Reverb",
            "Some Song",
            186000,
        ),
    ],
)
def test_catalog_search_preserves_the_source_edition(
    artist: str, raw: str, base: str, edition: str, catalog_title: str, duration: int
) -> None:
    from autplay.domain.catalog_artwork import artwork_search_title, recording_labels_match
    from autplay.domain.track_metadata import metadata_search_terms
    from autplay.domain.track_names import project_names, track_names

    display = f"{base} ({edition})"
    assert track_names(raw, artist) == track_names(display, artist) == (display, artist)
    assert project_names({}, raw, artist)["fields"]["title"] == display
    assert artwork_search_title(raw, artist) == base
    terms = metadata_search_terms(raw, artist)
    assert terms[0] == (raw, artist) and (base, artist) in terms
    query = MetadataQuery(raw, artist, duration_ms=duration)
    candidate = MetadataCandidate(
        "hit", {"title": catalog_title, "artist": artist}, duration, 100, "release"
    )
    assert not candidate.exact_for(query)
    assert replace(candidate, recording_disambiguation=edition).exact_for(query)
    version = replace(candidate, fields={"title": f"{catalog_title} ({edition})", "artist": artist})
    assert version.exact_for(query)
    assert not replace(version, recording_disambiguation="Slowed").exact_for(query)
    assert not replace(version, duration_ms=duration + 5000).exact_for(query)
    assert not version.exact_for(replace(query, artist="Other Artist"))
    assert recording_labels_match(artist, raw, artist, f"{catalog_title} ({edition})")
    assert not recording_labels_match(artist, raw, artist, catalog_title)


@pytest.mark.parametrize(
    "title",
    [
        "Speed",
        "Slow Down",
        "Take It Slow",
        "Never Speed Up",
        "Instrumental",
        "Slow Motion",
        "Super Slowed",
    ],
)
def test_literal_edit_words_remain_song_titles(title: str) -> None:
    from autplay.domain.catalog_artwork import artwork_search_title
    from autplay.domain.track_names import track_names

    assert lookup_title(title) == title
    assert track_names(title, None)[0] == title
    assert artwork_search_title(title, "Artist") == title


def test_joined_titles_require_all_letters_and_at_least_one_unsegmented_spelling() -> None:
    from autplay.domain.title_edits import joined_title_matches

    assert joined_title_matches("allthelittlethings", "All The Little Things")
    assert not joined_title_matches("allthelittlethings", "All The Little Thorns")
    assert not joined_title_matches("some thing else", "something else")
    assert not joined_title_matches("allthelittlethings", "All / The Little Things")
    assert not joined_title_matches("apart", "a part")


def test_full_artist_credit_diacritics_and_collisions_are_preserved() -> None:
    candidate = MetadataCandidate(
        "one", {"title": "Song", "artist": "Beyoncé feat. Jay-Z"}, 180000, 100, "release:one"
    )
    assert not candidate.exact_for(MetadataQuery("Song", "Beyoncé", duration_ms=180000))
    assert not candidate.exact_for(MetadataQuery("Song", "Beyonce feat. Jay-Z", duration_ms=180000))
    exact = MetadataQuery("Song [Official Video]", "Beyoncé feat. Jay-Z", duration_ms=180000)
    assert candidate.exact_for(exact)
    assert (
        automatic_candidate(exact, (candidate, replace(candidate, candidate_id="collision")))
        is None
    )


@pytest.mark.parametrize("types", [("Live",), ("Remix",), ("Compilation",)])
def test_release_group_types_cannot_turn_an_unqualified_track_into_a_studio_match(
    types: tuple[str, ...],
) -> None:
    candidate = MetadataCandidate(
        "one",
        {"title": "Song", "artist": "Artist"},
        180000,
        100,
        "release:one",
        release_secondary_types=types,
    )
    assert not candidate.exact_for(MetadataQuery("Song", "Artist", duration_ms=180000))


def native_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "provider": "bandcamp",
        "source_id": "track:123",
        "fields": {
            "title": "Song (Remix)",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Artist",
            "genres": ["Unknown native genre"],
            "release_date": "1998-04",
            "track_number": 3,
        },
        "external_ids": {"native_album_id": "42", "native_artist_id": "4", "isrc": "GBABC9800001"},
        "artwork": [
            {"kind": "album", "url": "https://f4.bcbits.com/img/test.jpg", "source_id": "album:42"}
        ],
    }


def test_source_metadata_canonical_roundtrip_preserves_genres_dates_and_native_identity() -> None:
    source = parse_source_metadata(native_document())
    assert parse_source_metadata(json.loads(json.dumps(source_metadata_document(source)))) == source
    group = native_album_group_for(
        source, FieldEvidence("SOURCE_NATIVE", "bandcamp:123", "2026-10-06")
    )
    assert group and group.key == "native:bandcamp:album:42" and group.release_date == "1998-04"
    assert parse_album_group(album_group_document(group)) == group
    assert native_album_group_for(replace(source, external_ids={}), group.evidence) is None
    assert native_album_group_for(replace(source, provider="yt_dlp"), group.evidence) is None


def test_explicit_native_album_identity_groups_with_unknown_album_artist() -> None:
    raw = native_document()
    raw["fields"] = {"album": "Album", "title": "Song", "genres": ["Unknown"]}
    source = parse_source_metadata(raw)
    proof = FieldEvidence("SOURCE_NATIVE", "bandcamp:123", "2026-10-06")
    group = native_album_group_for(source, proof)
    assert group and group.album_artist is None and group.key == "native:bandcamp:album:42"
    assert parse_album_group(album_group_document(group)) == group


def test_source_sanitizer_drops_bad_optional_leaves_without_losing_genres_album_or_ids() -> None:
    raw = native_document()
    raw["fields"] = {
        "album": "Album",
        "genres": ["Future unknown genre"],
        "release_date": "2025-02-30",
        "upload_date": "2000",
        "artist": "Artist",
    }
    raw["artwork"] = [
        {"kind": "album", "url": "https://f4.bcbits.com/art?token=secret", "source_id": "42"}
    ]
    raw["external_ids"] = {"native_album_id": "42", "isrc": "bad"}
    source = sanitize_source_metadata(raw)
    assert source.fields == {
        "album": "Album",
        "genres": ["Future unknown genre"],
        "artist": "Artist",
    }
    assert source.external_ids == {"native_album_id": "42"} and source.artwork == ()


@pytest.mark.parametrize(
    "url",
    [
        "http://f4.bcbits.com/img/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://127.1/a.jpg",
        "https://169.254.169.254/a",
        "https://server.local/art",
        "https://token@f4.bcbits.com/art",
        "file:///private",
        "https://f4.bcbits.com/art?token=secret",
        "https://f4.bcbits.com/art?X-Amz-Signature=secret",
        "https://f4.bcbits.com/art#private",
        "https://usercontent.jamendo.com/?id=3&token=secret",
    ],
)
def test_native_art_is_inert_public_bounded_non_secret_evidence(url: str) -> None:
    raw = native_document()
    raw["artwork"] = [{"kind": "album", "url": url, "source_id": "42"}]
    with pytest.raises(ValueError, match="metadata_evidence_artwork_url_invalid"):
        parse_source_metadata(raw)


def test_jamendo_image_selector_is_retained_without_download_permission() -> None:
    raw = native_document()
    raw["artwork"] = [
        {
            "kind": "album",
            "source_id": "42",
            "url": "https://usercontent.jamendo.com/?type=album&id=42&width=500",
        }
    ]
    assert parse_source_metadata(raw).artwork[0].kind == "album"


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": True},
        {"source_id": "https://private.example/a"},
        {"source_id": "D:\\private\\a"},
        {"source_id": "x" * 501},
        {"fields": {"title": "x" * 501}},
        {"external_ids": {"native_album_id": "https://provider.example/album/42"}},
        {"artwork": [{}] * 5},
        {"private_payload": "x" * 16385},
    ],
)
def test_native_evidence_bounds_reject_malformed_protocol_shapes(patch: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_source_metadata(native_document() | patch)


def test_album_group_requires_hydrated_edition_and_serializes_old_candidates_safely() -> None:
    release_id = str(UUID(int=42))
    candidate = MetadataCandidate(
        "one",
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Artist",
            "release_date": "1998",
            "mb_release_id": release_id,
        },
        180000,
        100,
        f"musicbrainz:release:{release_id}",
    )
    proof = FieldEvidence("MUSICBRAINZ", candidate.source_id, "2026-10-06")
    assert album_group_for(candidate, proof) is None
    hydrated = replace(candidate, release_hydrated=True, release_status="Official")
    group = album_group_for(hydrated, proof)
    assert group and group.key == candidate.source_id and group.track_number is None
    assert album_group_for(replace(hydrated, release_position_ambiguous=True), proof) is None
    assert album_group_for(replace(hydrated, auto_eligible=False), proof) is None
    assert album_group_for(replace(hydrated, auto_eligible=False), replace(proof, locked=True))
    restored = MetadataCandidate(
        **json.loads(json.dumps(asdict(replace(hydrated, release_secondary_types=("Unknown",)))))
    )
    assert restored.release_secondary_types == ("Unknown",)


def test_candidate_occurrence_uuid_is_bounded_and_legacy_snapshots_default_none() -> None:
    candidate = MetadataCandidate(
        "one",
        {"title": "Song", "artist": "Artist"},
        180000,
        100,
        "release:one",
        mb_release_track_id=str(UUID(int=108)),
    )
    raw = json.loads(json.dumps(asdict(candidate)))
    assert MetadataCandidate(**raw).mb_release_track_id == str(UUID(int=108))
    del raw["mb_release_track_id"]
    assert MetadataCandidate(**raw).mb_release_track_id is None
    with pytest.raises(ValueError):
        replace(candidate, mb_release_track_id="invalid")
    # UUID accepts surplus hyphens, but evidence still has a hard text bound.
    with pytest.raises(ValueError):
        replace(candidate, mb_release_track_id="-" * 501 + UUID(int=108).hex)
