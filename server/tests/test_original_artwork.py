from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.application.catalog_artwork import attach_images, inherit_catalog_images
from autplay.domain.musicbrainz_performances import PERFORMANCE
from autplay.domain.original_artwork import catalog_original, credited_original
from autplay.domain.recovery_artwork import listenbrainz_url
from autplay.domain.track_metadata import MetadataQuery


def test_only_a_unique_unversioned_catalog_recording_supplies_art():
    query = MetadataQuery("Too Close (White Cliffs Remix)", "Alex Clare", "Remixes", 182000)
    credit = credited_original(query, "REMIX")
    assert credit == ("Alex Clare", "Too Close")
    cover = "https://cdn-images.dzcdn.net/images/cover/" + "a" * 32 + "/500x500-000000-80-0-0.jpg"
    row = {
        "id": 12,
        "artist": {"id": 9, "name": "Alex Clare"},
        "title": "Too Close",
        "duration": 250,
        "album": {"id": 54, "title": "Original album", "cover_big": cover},
    }
    match = catalog_original([row], query, "REMIX", credit)
    assert match and match.cover == cover and match.title == query.title and match.portrait is None
    assert not catalog_original([row, dict(row, id=13)], query, "REMIX", credit)
    assert not catalog_original([dict(row, title_version="Live")], query, "REMIX", credit)
    assert not credited_original(replace(query, title="Too Close (Cover by Alex Clare)"), "COVER")
    assert (
        credited_original(replace(query, title="Too Close (originally by Alex Clare)"), "COVER")
        == credit
    )


def test_cover_without_ids_requires_a_unique_performance_and_reciprocal_work_link():
    derivative, work, parent, release = (str(uuid4()) for _ in range(4))
    query = MetadataQuery("Song (Cover)", "Second artist", duration_ms=201000)
    performance = {
        "type-id": PERFORMANCE,
        "target-type": "work",
        "direction": "forward",
        "work": {"id": work},
        "attributes": ["cover"],
    }

    def recording_link(identity, attrs):
        return {
            "type-id": PERFORMANCE,
            "target-type": "recording",
            "direction": "backward",
            "recording": {"id": identity, "video": False},
            "attributes": attrs,
        }

    result = {
        "id": derivative,
        "title": "Song",
        "length": 200000,
        "artist-credit": [{"name": "Second artist"}],
    }
    relations = [recording_link(derivative, ["cover"]), recording_link(parent, [])]

    def lookup(url):
        if "/recording/?query=" in url:
            return {"recordings": [result], "count": 1}
        if f"/recording/{derivative}?" in url:
            return {"id": derivative, "relations": [performance]}
        if f"/work/{work}?" in url:
            return {"id": work, "relations": relations}
        if f"/release/{release}?" in url:
            return {
                "id": release,
                "status": "Official",
                "artist-credit": [{"name": "First artist"}],
                "release-group": {"primary-type": "Album", "secondary-types": []},
            }
        assert url == listenbrainz_url([parent])
        return {
            parent: {
                "artist": {"name": "First artist"},
                "recording": {"name": "Song"},
                "release": {
                    "name": "First album",
                    "mbid": release,
                    "caa_release_mbid": release,
                    "caa_id": 1,
                },
            }
        }

    provider = MusicBrainzMetadataProvider(SimpleNamespace(json=lookup))
    match = provider.find_original_artwork(query)
    assert match and (match.track_id, match.album_id, match.artist, match.credit) == (
        parent,
        release,
        "First artist",
        "Second artist",
    )
    relations.append(recording_link(str(uuid4()), []))
    assert provider.find_original_artwork(query) is None
    assert provider.find_original_artwork(replace(query, duration_ms=160000)) is None


def test_original_artwork_yields_to_the_versions_own_cover(monkeypatch):
    from autplay.domain.catalog_artwork import ArtMatch

    monkeypatch.setattr(
        "autplay.application.catalog_artwork.store_image", lambda session, data: data.decode()
    )
    match = ArtMatch(
        1,
        "First artist",
        2,
        3,
        "cover",
        None,
        "Song (Cover)",
        "First album",
        "Performer",
        basis="original_cover",
    )
    fields = {"title": match.title, "artist": match.credit, "album": "Cover single"}
    row = SimpleNamespace(document={"fields": dict(fields)}, artwork_sha256=None)
    assert attach_images(None, row, match, b"original", b"wrong-portrait")
    assert row.document["fields"] == fields and "catalog_artist_image_v1" not in row.document
    borrowed = dict(row.document)
    assert borrowed["artwork_source"]["original_track_id"] == "2"
    direct = replace(match, album="Cover single", basis="recording_duration", album_id=7)
    assert attach_images(None, row, direct, b"own", None)
    assert not attach_images(None, row, match, b"original", None)
    assert row.artwork_sha256 == "own"
    variant = uuid4()
    evidence = [
        SimpleNamespace(
            audio_variant_id=variant,
            title=match.title,
            artist=match.credit,
            portrait=None,
            source=d["artwork_source"],
            artwork_sha256=sha,
        )
        for d, sha in [(borrowed, "original"), (row.document, "own")]
    ]
    for order in [evidence, evidence[::-1]]:
        assert (
            inherit_catalog_images(order, borrowed, "original", match.credit, variant, match.title)[
                1
            ]
            == "own"
        )
    locked = SimpleNamespace(
        document={"fields": fields, "provenance": {"album": {"locked": True}}}, artwork_sha256=None
    )
    assert not attach_images(None, locked, match, b"original", None)
