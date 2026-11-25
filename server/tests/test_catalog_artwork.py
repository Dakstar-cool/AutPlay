from types import SimpleNamespace
from uuid import uuid4

from autplay.application.catalog_artwork import inherit_catalog_images
from autplay.domain.catalog_artwork import (
    catalogue_api_url,
    image_url,
    match_artwork,
    native_image_host,
    search_url,
)


def cdn(kind, key):
    return f"https://cdn-images.dzcdn.net/images/{kind}/{key * 32}/500x500-000000-80-0-0.jpg"


def track(artist_id=1, album_id=2, album="Original", cover=None):
    return {
        "id": 3,
        "title": "Song (Live)",
        "duration": 200,
        "artist": {"id": artist_id, "name": "Artist", "picture_big": cdn("artist", "a")},
        "album": {"id": album_id, "title": album, "cover_big": cover},
    }


def test_recording_artist_and_edition_must_agree():
    first = track(cover=cdn("cover", "b"))
    reissue = track(album_id=4, album="Anniversary", cover=cdn("cover", "c"))
    assert match_artwork([first], "Song", "Artist", None, 200000) is None
    assert match_artwork([first], "Song (Live)", "Artist", None, 205000).cover == cdn("cover", "b")
    assert match_artwork([first], "Song (Live)", "Artist", None, 205001) is None
    assert match_artwork([first], "Song (Live)", "Artist", "Original", 210000).cover == cdn(
        "cover", "b"
    )
    assert match_artwork(
        [first], "Song (Live)", "Artist", None, 207000, confirmed_albums={"original"}
    ).cover
    assert match_artwork([first], "Song (Live)", "Artist", "Original", 210001) is None
    assert match_artwork([first], "Song", "Artist", "Original", 207000) is None
    assert match_artwork([first, track(artist_id=7)], "Song (Live)", "Artist", None, 200000) is None
    ambiguous = match_artwork([first, reissue], "Song (Live)", "Artist", None, 200000)
    assert ambiguous.cover is None and ambiguous.portrait == cdn("artist", "a")
    other_album = match_artwork([first], "Song (Live)", "Artist", "Other album", 200000)
    assert other_album.cover is None and other_album.portrait == cdn("artist", "a")
    matched = match_artwork([first, reissue], "Song (Live)", "Artist", "Anniversary", 200000)
    assert matched.cover == cdn("cover", "c") and matched.album_id == 4
    only_cover = match_artwork([track(), reissue], "Song (Live)", "Artist", None, 200000)
    assert only_cover.cover is None and only_cover.portrait == cdn("artist", "a")
    versioned = dict(first, title="Song", title_version="(Acoustic)")
    assert match_artwork([versioned], "Song", "Artist", "Original", 200000) is None
    alive = dict(first, title="Alive", title_version="Live")
    assert match_artwork([alive], "Alive", "Artist", "Original", 200000) is None
    assert match_artwork([alive], "Alive (Live)", "Artist", "Original", 200000).cover == cdn(
        "cover", "b"
    )


def test_featured_credit_may_move_between_artist_and_title_without_losing_versions():
    single = dict(track(cover=cdn("cover", "b")), title="Song (Live) (feat. Guest)")
    hit = match_artwork([single], "Song (Live)", "Artist feat. Guest", "Original", 200000)
    assert hit and hit.cover and hit.artist == "Artist" and hit.credit == "Artist feat. Guest"
    assert match_artwork([single], "Song", "Artist feat. Guest", "Original", 200000) is None
    assert match_artwork([single], "Song (Live)", "Artist feat. Other", "Original", 200000) is None
    assert match_artwork([single], "Song (Live)", "Artist", "Original", 200000) is None


def test_video_credit_and_dated_language_tag_in_artwork_queries():
    from urllib.parse import parse_qs, urlsplit

    from autplay.domain.track_names import track_names

    artist = "GET THE SHOT"
    title = "THE COST OF DENIAL - HC WORLDWIDE (OFFICIAL HD VERSION HCWW)"
    album_track = dict(
        track(cover=cdn("cover", "b")),
        title="The Cost of Denial",
        artist={"id": 1, "name": artist},
        duration=67,
    )
    assert track_names(title, artist)[0] == "THE COST OF DENIAL"
    assert parse_qs(urlsplit(search_url(artist, title)).query)["q"] == [
        artist + " THE COST OF DENIAL"
    ]
    assert match_artwork([album_track], title, artist, None, 67000).cover
    assert match_artwork([album_track], title, artist, None, 88362) is None

    dated = "The Clowns (2012) [EN]"
    assert parse_qs(urlsplit(search_url("NUTEKI", dated)).query)["q"] == ["NUTEKI The Clowns"]
    assert track_names(dated, "NUTEKI")[0] == dated
    russian = dict(
        track(), artist={"id": 1, "name": "NUTEKI"}, title="The Clowns (2012) [RU]", duration=300
    )
    assert match_artwork([russian], dated, "NUTEKI", None, 299816) is None


def test_only_provider_image_paths_are_accepted():
    assert catalogue_api_url(search_url("A & B", "Song"))
    assert image_url(cdn("artist", "a"), "cover") is None
    assert image_url(cdn("artist", "0"), "artist") is None
    assert image_url("https://[bad-host", "cover") is None
    assert not catalogue_api_url("https://api.deezer.com/artist/123?secret=1")
    assert (
        native_image_host("https://usercontent.jamendo.com?type=album&id=123&width=600")
        == "JAMENDO"
    )
    assert not native_image_host("https://usercontent.jamendo.com/?type=album&id=123&token=")


def test_published_handles_only_copy_art_in_their_audio_or_public_catalogue():
    variant = uuid4()
    row = SimpleNamespace(
        audio_variant_id=variant,
        audio_digest="sha256:truth",
        portrait=None,
        title="Song",
        artist="Artist",
        artwork_sha256="d" * 64,
        source={"source": "EMBEDDED", "source_id": "sha256:other"},
    )
    assert inherit_catalog_images([row], {}, None, "Artist", variant, "Song") == ({}, None)
    row.source["source_id"] = row.audio_digest
    assert (
        inherit_catalog_images([row], {}, None, "Artist", variant, "Song")[1] == row.artwork_sha256
    )
    assert inherit_catalog_images([row], {}, None, "Artist", uuid4(), "Song") == ({}, None)
    manual = {"provenance": {"album": {"locked": True}}}
    assert inherit_catalog_images([row], manual, None, "Artist", variant, "Song") == (manual, None)


def test_yandex_uses_official_recording_version_album_and_performer_photo():
    from autplay.domain.web_artwork import yandex_match

    artist = {
        "id": "8",
        "name": "Artist",
        "cover": {
            "type": "from-artist-photos",
            "uri": "avatars.yandex.net/get-music-content/123/abc.p.8/%%",
        },
    }
    album = {
        "id": 19,
        "title": "Original",
        "artists": [artist],
        "releaseDate": "2020-01-15",
        "coverUri": "avatars.yandex.net/get-music-content/456/abc.a.19/%%",
    }
    reissue = {
        **album,
        "id": 29,
        "title": "Reissue",
        "releaseDate": "2023-02-01",
        "coverUri": "avatars.yandex.net/get-music-content/789/abc.a.29/%%",
    }
    row = {
        "id": 77,
        "title": "Song",
        "version": "Live",
        "durationMs": 200220,
        "artists": [artist],
        "albums": [reissue, album],
    }
    assert not yandex_match([row], "Song", "Artist", None, 200100)
    found = yandex_match([row], "Song (Live)", "Artist", None, 200100)
    assert found.album_id == 19 and found.portrait.endswith("/abc.p.8/600x600")
    assert yandex_match([row], "Song (Live)", "Artist", "Reissue", 200100).album_id == 29
    assert yandex_match([row], "Song (Live)", "Artist", "Another album", 200100).cover is None
    assert (
        yandex_match([row], "Song (Live)", "Artist", "Original", 209000).basis
        == "edition_timing_offset"
    )
    assert yandex_match([row], "Song (Live)", "Artist", None, 209000) is None
    assert yandex_match([row], "Song (Live)", "Artist", "Another album", 209000) is None


def test_publisher_requires_track_page_duration_and_exact_art_id():
    import html
    import json

    from autplay.domain.web_artwork import publisher_match

    url = "https://fixture.bandcamp.com/track/song"
    record = {
        "@type": "MusicRecording",
        "@id": url,
        "name": "Song",
        "byArtist": {"name": "Artist"},
        "inAlbum": {"name": "Album"},
        "image": "https://f4.bcbits.com/img/a22_10.jpg",
    }
    native = {
        "current": {"type": "track", "id": 31, "album_id": 7},
        "art_id": 22,
        "trackinfo": [{"track_id": 31, "duration": 85.5}],
    }
    body = (
        "<div data-tralbum='"
        + html.escape(json.dumps(native))
        + "'></div>"
        + '<script type="application/ld+json">'
        + json.dumps(record)
        + "</script>"
    ).encode()
    result = {"id": 31, "band_id": 2, "art_id": 22, "album_name": "Album"}
    assert publisher_match(body, result, url, "Song", "Artist", "Album", 85100).album_id == 7
    assert not publisher_match(body, result, url, "Song", "Artist", "Album", 45100)
    assert not publisher_match(body, {**result, "art_id": 55}, url, "Song", "Artist", None, 85100)


def test_review_art_does_not_mix_recordings_or_erase_version_labels():
    from autplay.domain.web_artwork import archived_editions

    rec, release, group = map(str, (uuid4(), uuid4(), uuid4()))
    fields = {
        "artist": "Artist",
        "title": "Song (Live)",
        "album": "Concert",
        "mb_recording_id": rec,
        "mb_release_id": release,
        "mb_release_group_id": group,
    }
    candidate = {
        "fields": fields,
        "duration_ms": 125000,
        "source_id": "musicbrainz:release:" + release,
    }
    matches = archived_editions([candidate], "Song (Live)", "Artist", None, 126000)
    assert [(m.entity, m.album_id) for m in matches] == [
        ("release", release),
        ("release-group", group),
    ]
    assert not archived_editions([candidate], "Song", "Artist", None, 126000)
    other = {
        **candidate,
        "fields": {**fields, "mb_recording_id": str(uuid4()), "mb_release_id": group},
        "source_id": "musicbrainz:release:" + group,
    }
    assert not archived_editions([candidate, other], "Song (Live)", "Artist", None, 126000)


def test_publisher_index_requires_single_exact_result_and_consistent_thumbnail():
    from autplay.domain.web_artwork import indexed_publisher_cover, publisher_candidates

    row = {
        "type": "t",
        "id": 42,
        "name": "Sundown",
        "band_name": "Artist",
        "band_id": 3,
        "art_id": 12,
        "album_name": "Evening",
        "album_id": 50,
        "img": "https://f4.bcbits.com/img/12_3.jpg",
        "item_url_path": "https://artist.bandcamp.com/track/sundown",
    }

    def lookup(rows, title="Sundown"):
        hits = publisher_candidates({"auto": {"results": rows}}, "Artist", title, None)
        return indexed_publisher_cover(hits, title, "Artist", None)

    result = lookup([row])
    assert (
        result.cover == "https://f4.bcbits.com/img/a12_10.jpg" and result.basis == "publisher_index"
    )
    assert lookup([row, dict(row, band_id=4)]) is None
    assert lookup([dict(row, art_id=13)]) is None
    assert lookup([dict(row, band_name="Other")]) is None
    assert lookup([dict(row, name="Intro")], title="Intro") is None


def test_archive_requires_unanimous_release_group_for_small_duration_difference():
    from autplay.domain.web_artwork import archived_editions

    rec, group = map(str, (uuid4(), uuid4()))

    def candidate(group_id):
        release = str(uuid4())
        return {
            "fields": {
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "mb_recording_id": rec,
                "mb_release_id": release,
                "mb_release_group_id": group_id,
            },
            "duration_ms": 205000,
            "source_id": "musicbrainz:release:" + release,
        }

    first = candidate(group)
    matches = archived_editions(
        [first, candidate(group)], "Song (Music Video)", "Artist", None, 200000
    )
    assert matches and all(m.basis == "recording_and_release_group" for m in matches)
    assert not archived_editions([first, candidate(str(uuid4()))], "Song", "Artist", None, 200000)
    assert not archived_editions([first], "Song (Live)", "Artist", None, 200000)
    assert not archived_editions([first], "Song", "Artist", None, 190000)


def test_listenbrainz_checks_credit_duration_release_and_homonyms():
    from autplay.domain.recovery_artwork import listenbrainz_cover

    recording, release, artist = (str(uuid4()) for _ in range(3))
    entry = {
        "recording": {"name": "Song", "length": 200000, "isrcs": ["GBABC0000001"]},
        "artist": {"name": "Artist", "artists": [{"artist_mbid": artist}]},
        "release": {"mbid": release, "name": "Album", "caa_id": 42, "caa_release_mbid": release},
    }
    records = {recording: entry}
    assert listenbrainz_cover(records, "Song", "Artist", None, 204000).album_id == release
    assert not listenbrainz_cover(records, "Song", "Artist", None, 209000)
    assert listenbrainz_cover(records, "Song", "Artist", None, 209000, isrcs=["GBABC0000001"])
    assert not listenbrainz_cover(records, "Song (Live)", "Artist", None, 200000)
    assert not listenbrainz_cover(
        records, "Song", "Artist", None, 200000, selected_release=str(uuid4())
    )
    duplicate = {**entry, "artist": {"name": "Artist", "artists": [{"artist_mbid": str(uuid4())}]}}
    assert not listenbrainz_cover(
        {**records, str(uuid4()): duplicate}, "Song", "Artist", None, 200000
    )


def test_archive_uses_only_the_release_front_and_original_audio():
    from autplay.domain.recovery_artwork import archive_cover, archive_image_url

    item = {
        "metadata": {
            "identifier": "sample-release",
            "title": "First",
            "creator": "Artist",
            "collection": "freemusicarchive",
            "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
        },
        "files": [
            {"source": "original", "name": "sample-release.jpg"},
            {"source": "original", "name": "back.jpg"},
            {
                "source": "original",
                "name": "song.flac",
                "title": "Song",
                "album": "First",
                "artist": "Artist",
                "length": "3:20",
            },
        ],
    }
    cover = archive_cover(item, "sample-release", "Song", "Artist", "First", 204500)
    assert archive_image_url(cover.cover) and cover.cover.endswith("/sample-release.jpg")
    assert cover.license_url == item["metadata"]["licenseurl"]
    assert not archive_cover(item, "sample-release", "Song", "Artist", "Second", 200000)
    assert not archive_cover(item, "sample-release", "Song (Demo)", "Artist", None, 200000)
    assert not archive_cover(item, "sample-release", "Song", "Artist", None, 250000)
    assert not archive_image_url("https://archive.org/download/sample-release/%2e%2e%2fimage.jpg")


def test_public_metadata_urls_accept_only_the_expected_catalog_endpoints():
    from autplay.domain.recovery_artwork import (
        archive_batch_search,
        archive_search,
        listenbrainz_url,
        metadata_url_allowed,
    )

    assert metadata_url_allowed(archive_search('Name "B"'))
    assert metadata_url_allowed(archive_batch_search(['Name "B"', "C & D"]))
    assert metadata_url_allowed(listenbrainz_url([str(uuid4())]))
    assert not metadata_url_allowed("https://archive.org/metadata/../download/private")
    assert not metadata_url_allowed(listenbrainz_url(["invalid"]))


def test_selected_album_group_art_identifies_the_recording_and_group():
    from autplay.domain.recovery_artwork import selected_group_art

    recording, release, group = (str(uuid4()) for _ in range(3))
    fields = {
        "mb_recording_id": recording,
        "mb_release_id": release,
        "mb_release_group_id": group,
        "album": "Original",
    }
    match = selected_group_art(fields, "Song", "Artist")
    assert match.track_id == recording and match.album_id == group
    assert match.entity == "release-group" and match.basis == "selected_release_group"
    assert not selected_group_art({**fields, "mb_release_id": None}, "Song", "Artist")
