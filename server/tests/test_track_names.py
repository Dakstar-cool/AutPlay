from autplay.domain.track_names import project_names, search_label, track_names


def test_filename_suffix_needs_independent_evidence() -> None:
    raw = "The_Winery_Dogs_-_The_Dying_64218614.mp3"
    assert track_names(raw, None) == ("The Dying 64218614", "The Winery Dogs")
    assert track_names(raw, None, source_id="64218614") == ("The Dying", "The Winery Dogs")
    assert track_names("AC_DC_-_1984.mp3", "AC/DC") == ("AC DC - 1984", "AC/DC")
    assert track_names("Hush_(Live).flac", "Muse/Ellie Goulding") == (
        "Hush (Live)",
        "Muse/Ellie Goulding",
    )


def test_display_style_and_search_equivalence() -> None:
    assert track_names("CAN’T GET OVER YOU", "Joji") == ("CAN’T GET OVER YOU", "Joji")  # noqa: RUF001
    assert search_label(" CAN’T　GET OVER YOU ") == search_label("can't get over you")  # noqa: RUF001
    assert search_label("25—17") == search_label("25/17")


def test_source_sites_are_removed_from_files_and_credits() -> None:
    assert track_names("[muzmo.ru] молчи speed up - Mp3on.net.mp3", "кис-кис | hitmos") == (
        "молчи (Sped Up)",
        "кис-кис",
    )
    assert track_names("Artist - [MUZMO.RU] - Title | www.lightaudio.ru", "Artist") == (
        "Title",
        "Artist",
    )
    assert track_names("Track (Live) (zaycev.net)", "https://hitmos.ru/ AC/DC") == (
        "Track (Live)",
        "AC/DC",
    )
    for literal in ("Hitmosaic", "my.muzmo.ru", "Band.fm", "AC/DC", "The Speed of Sound"):
        assert track_names(literal, literal) == (literal, literal)


def test_site_only_labels_stay_empty_until_real_evidence_arrives() -> None:
    proof = {"source": "EMBEDDED", "source_id": "file", "normalization_version": "display-v7"}
    doc = {"fields": {"artist": "[muzmo.ru] hitmos"}, "provenance": {"artist": proof}}
    clean = project_names(doc, "Track", None)
    assert clean["fields"]["artist"] is None
    assert clean["name_originals_v1"]["artist"]["value"] == "[muzmo.ru] hitmos"
    assert project_names(clean, "Track", None) is clean
    for site in ("[muzmo.ru]", "hitmos.mp3"):
        result = project_names({}, site, "hitmos")
        assert result["fields"] == {"title": None, "artist": None}
        assert project_names(result, site, "hitmos") is result
        assert project_names(result, "Song", "Artist")["fields"] == {
            "title": "Song",
            "artist": "Artist",
        }
    for source in ("USER", "MUSICBRAINZ"):
        protected = {"fields": doc["fields"], "provenance": {"artist": {**proof, "source": source}}}
        assert project_names(protected, "Track", None) is protected


def test_backfill_keeps_locked_names_explicit_clears_and_originals() -> None:
    doc = {
        "fields": {"title": "  My mix ", "artist": None},
        "provenance": {"title": {"source": "USER", "locked": True}},
    }
    assert project_names(doc, "Artist_-_Track.mp3", "Artist") is doc
    raw = "Artist_-_Track.mp3"
    clean = project_names({}, raw, None)
    assert clean["fields"] == {"title": "Track", "artist": "Artist"}
    assert not clean["name_originals_v1"]["artist"]["present"]
    assert clean["provenance"]["title"]["source"] == "FILENAME"
    assert project_names(clean, raw, None) is clean


def test_only_matching_confirmed_identity_replaces_an_embedded_name() -> None:
    fields = {"mb_recording_id": "rec", "mb_release_id": "edition", "title": "Hush (Live)"}
    proof = {"source": "EMBEDDED", "source_id": "audio", "locked": False, "observed_at": "t"}
    doc = {
        "fields": {**fields, "title": "Hush"},
        "provenance": {"title": proof},
        "catalog_identity_v1": {"fields": fields},
    }
    clean = project_names(doc, "Hush", None)
    assert clean["fields"]["title"] == "Hush (Live)"
    assert clean["name_originals_v1"]["title"]["value"] == "Hush"
    assert project_names(clean, "Hush", None) is clean
    stale = {**doc, "catalog_identity_v1": {"fields": {**fields, "mb_release_id": "other"}}}
    assert project_names(stale, "Hush", None) is stale


def test_provisional_titles_can_be_cleaned_without_an_audio_extension() -> None:
    raw = "рай_открыт_для_всех_Е_Алехин_публика_выбирает_сама"  # noqa: RUF001
    assert track_names(raw, "Публика Выбирает Сама") == (
        "рай открыт для всех",
        "Публика Выбирает Сама",
    )
    assert track_names("2WEI - Catapult", "2WEI") == ("Catapult", "2WEI")
    assert track_names("Outlaw | Official Video", "Intelligency")[0] == "Outlaw"
    assert track_names("Go_A - SHUM (Official Lyric Video - Single Edit)", "Go_A") == (
        "SHUM (Single Edit)",
        "Go_A",
    )
    assert track_names("Song | Official Music Video (Prod. X)", None)[0] == "Song (Prod. X)"
    assert track_names("Orbit (Official Music Video Remastered)", None)[0] == "Orbit (Remastered)"
    assert track_names("Down (Nurko Remix /Official Audio)", None)[0] == "Down (Nurko Remix)"
    assert track_names("Sugar | Official Video 2022 |", None)[0] == "Sugar (2022)"
    for literal in ("AC/DC", "artifact_001", "I AM ___", "Live @DOM_2011", "Official Video"):
        assert track_names(literal, None)[0] == literal
    assert track_names("Singer - Song", "Different singer")[0] == "Singer - Song"


def test_source_cleanup_is_repeatable_and_respects_manual_and_catalog_titles() -> None:
    raw = "Band_-_Some_Track"
    weak = project_names({}, raw, "Band")
    assert weak["fields"]["title"] == "Some Track"
    assert weak["provenance"]["title"]["source"] == "SOURCE_LABEL"
    assert project_names(weak, raw, "Band") is weak
    for source in ("USER", "MUSICBRAINZ"):
        protected = {"fields": {"title": raw}, "provenance": {"title": {"source": source}}}
        assert project_names(protected, raw, "Band") is protected


def test_stage_names_work_for_artists_outside_the_curated_table() -> None:
    from autplay.domain.artist_credits import recording_credit

    member = {
        "mbid": "artist-1",
        "name": "Silver Echo",
        "credited_name": "Lina Ray",
        "join_phrase": "",
    }
    project = recording_credit([member])
    assert project and project.preferred == "Lina Ray"
    linked = recording_credit([{**member, "legal_name": "Lina Ray"}])
    raw = "Evening_song_Lina_Ray_Silver_Echo"
    assert track_names(raw, "Lina Ray", catalog=linked) == ("Evening song", "Silver Echo")
    assert track_names(raw, "Silver Echo (real name: Lina Ray)") == ("Evening song", "Silver Echo")
    assert track_names("Evening_song_Silver_Echo", "Silver Echo") == (
        "Evening song Silver Echo",
        "Silver Echo",
    )
    assert track_names("A song about Silver Echo", "Silver Echo")[0] == "A song about Silver Echo"
    doc = project_names({}, raw, "Lina Ray", linked)
    assert doc["fields"] == {"artist": "Silver Echo", "title": "Evening song"}
    assert project_names(doc, raw, "Lina Ray", linked) is doc
    embedded = {
        "fields": {"artist": "Lina Ray (stage name: Silver Echo)"},
        "provenance": {"artist": {"source": "EMBEDDED", "source_id": "file"}},
    }
    normalized = project_names(embedded, raw, None)
    assert normalized["fields"] == {"artist": "Silver Echo", "title": "Evening song"}
    assert project_names(normalized, raw, None) is normalized
    guest = {"mbid": "artist-2", "name": "Lina Ray", "credited_name": "Lina Ray", "join_phrase": ""}
    duet = recording_credit(
        [{**member, "credited_name": "Silver Echo", "join_phrase": " feat. "}, guest]
    )
    assert duet and duet.preferred == "Silver Echo feat. Lina Ray"
    assert track_names("Duet", duet.credited, catalog=duet) == (
        "Duet",
        "Silver Echo feat. Lina Ray",
    )
    repeated = recording_credit(
        [{**member, "join_phrase": " / "}, {**member, "credited_name": "Silver Echo"}]
    )
    assert repeated and repeated.preferred == "Silver Echo"


def test_title_boundaries_preserve_guest_cover_and_remixer_credits() -> None:
    assert track_names("Письмо_для_Мая.mp3", "Мая") == ("Письмо для Мая", "Мая")
    credit = "Север (настоящее имя Иван Л.) feat. Мая"
    assert track_names("Cold ft. HS87", credit) == ("Cold", "Север feat. Мая, HS87")
    assert (
        track_names("Cold", "Север (настоящее имя Иван Л.) (feat. Мая)")[1] == "Север (feat. Мая)"
    )
    assert track_names("The_End_ft._RO", "RO") == ("The End", "RO")
    assert track_names("Mercy (LoSt. Remix)", "LoSt.")[0] == "Mercy (LoSt. Remix)"
    assert (
        track_names("Song (Original Artist cover)", "Cover Band")[0]
        == "Song (Original Artist cover)"
    )
    assert track_names("Quiet_song_-_AC/DC", "AC/DC") == ("Quiet song", "AC/DC")
    assert track_names("Cold", "Earth, Wind & Fire") == ("Cold", "Earth, Wind & Fire")
    assert track_names("Cover", "Group / Soloist")[1] == "Group / Soloist"
    doc = {"fields": {"composer": "Real Name"}}
    assert project_names(doc, "Piece", "Project Name") is doc


def test_featured_guests_are_projected_together_and_survive_reprojection() -> None:
    raw, primary = "Рядом быть feat. Хелависа&#x20;", "После 11"
    expected = {"title": "Рядом быть", "artist": "После 11 feat. Хелависа"}
    proof = {"source": "EMBEDDED", "source_id": "tags", "normalization_version": "display-v8"}
    artist_tag = {"fields": {"artist": primary}, "provenance": {"artist": proof}}
    before = project_names(artist_tag, raw, primary)
    replaced = project_names(before, "Другая песня feat. Другой гость", primary)
    assert replaced["fields"] == {"title": "Другая песня", "artist": "После 11 feat. Другой гость"}
    for initial, raw_title in (
        ({}, raw),
        ({"fields": {"title": raw}, "provenance": {"title": proof}}, "01.mp3"),
        (
            {
                "fields": {"title": raw, "artist": primary},
                "provenance": {"title": proof, "artist": proof},
            },
            raw,
        ),
    ):
        result = project_names(initial, raw_title, primary)
        assert result["fields"] == expected
        assert project_names(result, raw_title, primary) is result
    assert track_names("Song [Ft. A & B] (Live)", "Lead feat. A") == (
        "Song (Live)",
        "Lead feat. A, B",
    )
    assert track_names("Song featuring Guest", "Lead, Guest") == ("Song", "Lead, Guest")
    assert track_names("death bed | Feat. beabadoobee", "Powfu") == (
        "death bed",
        "Powfu feat. beabadoobee",
    )
    assert track_names("6 ft Under", "Singer") == ("6 ft Under", "Singer")
    assert track_names("[IKO NICE ft DJ BEN]", "NAITA") == ("IKO NICE", "NAITA feat. DJ BEN")
    assert track_names("[NAITA ft DJ BEN]", "NAITA") == ("[NAITA ft DJ BEN]", "NAITA")
    assert track_names("[IKO NICE ft DJ BEN]", None) == ("[IKO NICE ft DJ BEN]", None)
    assert track_names("Be Your Friend (Vigiland Feat. Alexander Tidebrink)", "Vigiland") == (
        "Be Your Friend",
        "Vigiland feat. Alexander Tidebrink",
    )
    assert track_names("Duet (Rock Remix feat. Tom)", "Singer") == (
        "Duet (Rock Remix)",
        "Singer feat. Tom",
    )
    assert track_names("Duet +feat. Tom #live", "Singer") == ("Duet #live", "Singer feat. Tom")
    assert track_names("Great feat of strength", "Lead") == ("Great feat of strength", "Lead")
    assert track_names("Song (Guest feat. B Remix)", "Lead") == (
        "Song (Guest feat. B Remix)",
        "Lead",
    )
    assert track_names(raw, None) == (raw, None)
    locked = {
        "fields": {"artist": "Chosen"},
        "provenance": {"artist": {"source": "USER", "locked": True}},
    }
    assert project_names(locked, raw, primary) is locked
