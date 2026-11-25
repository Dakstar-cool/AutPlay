from autplay.domain.track_names import project_names, search_label, track_names


def test_filename_suffix_needs_independent_evidence() -> None:
    raw = "The_Winery_Dogs_-_The_Dying_64218614.mp3"
    assert track_names(raw, None) == ("The Dying 64218614", "The Winery Dogs")
    assert track_names(raw, None, source_id="64218614") == ("The Dying", "The Winery Dogs")
    assert track_names("AC_DC_-_1984.mp3", "AC/DC") == ("AC DC - 1984", "AC/DC")
    assert track_names("Hush_(Live).flac", "Muse/Ellie Goulding") == ("Hush (Live)", "Muse/Ellie Goulding")


def test_display_style_and_search_equivalence() -> None:
    assert track_names("CAN’T GET OVER YOU", "Joji") == ("CAN’T GET OVER YOU", "Joji")
    assert search_label(" CAN’T　GET OVER YOU ") == search_label("can't get over you")
    assert search_label("25—17") == search_label("25/17")


def test_backfill_keeps_locked_names_explicit_clears_and_originals() -> None:
    doc = {"fields": {"title": "  My mix ", "artist": None},
           "provenance": {"title": {"source": "USER", "locked": True}}}
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
    doc = {"fields": {**fields, "title": "Hush"}, "provenance": {"title": proof},
           "catalog_identity_v1": {"fields": fields}}
    clean = project_names(doc, "Hush", None)
    assert clean["fields"]["title"] == "Hush (Live)"
    assert clean["name_originals_v1"]["title"]["value"] == "Hush"
    assert project_names(clean, "Hush", None) is clean
    stale = {**doc, "catalog_identity_v1": {"fields": {**fields, "mb_release_id": "other"}}}
    assert project_names(stale, "Hush", None) is stale


def test_provisional_titles_can_be_cleaned_without_an_audio_extension() -> None:
    raw = "рай_открыт_для_всех_Е_Алехин_публика_выбирает_сама"
    assert track_names(raw, "Публика Выбирает Сама") == ("рай открыт для всех", "Публика Выбирает Сама")
    assert track_names("2WEI - Catapult", "2WEI") == ("Catapult", "2WEI")
    assert track_names("Outlaw | Official Video", "Intelligency")[0] == "Outlaw"
    assert track_names("Go_A - SHUM (Official Lyric Video - Single Edit)", "Go_A") == (
        "SHUM (Single Edit)", "Go_A"
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
    member = {"mbid": "artist-1", "name": "Silver Echo", "credited_name": "Lina Ray", "join_phrase": ""}
    project = recording_credit([member])
    assert project and project.preferred == "Lina Ray"
    linked = recording_credit([{**member, "legal_name": "Lina Ray"}])
    raw = "Evening_song_Lina_Ray_Silver_Echo"
    assert track_names(raw, "Lina Ray", catalog=linked) == ("Evening song", "Silver Echo")
    assert track_names(raw, "Silver Echo (real name: Lina Ray)") == ("Evening song", "Silver Echo")
    assert track_names("Evening_song_Silver_Echo", "Silver Echo") == ("Evening song Silver Echo", "Silver Echo")
    assert track_names("A song about Silver Echo", "Silver Echo")[0] == "A song about Silver Echo"
    doc = project_names({}, raw, "Lina Ray", linked)
    assert doc["fields"] == {"artist": "Silver Echo", "title": "Evening song"}
    assert project_names(doc, raw, "Lina Ray", linked) is doc
    embedded = {"fields": {"artist": "Lina Ray (stage name: Silver Echo)"},
                "provenance": {"artist": {"source": "EMBEDDED", "source_id": "file"}}}
    normalized = project_names(embedded, raw, None)
    assert normalized["fields"] == {"artist": "Silver Echo", "title": "Evening song"}
    assert project_names(normalized, raw, None) is normalized
    guest = {"mbid": "artist-2", "name": "Lina Ray", "credited_name": "Lina Ray", "join_phrase": ""}
    duet = recording_credit([{**member, "credited_name": "Silver Echo", "join_phrase": " feat. "}, guest])
    assert duet and duet.preferred == "Silver Echo feat. Lina Ray"
    assert track_names("Duet", duet.credited, catalog=duet) == ("Duet", "Silver Echo feat. Lina Ray")
    repeated = recording_credit([{**member, "join_phrase": " / "}, {**member, "credited_name": "Silver Echo"}])
    assert repeated and repeated.preferred == "Silver Echo"


def test_title_boundaries_preserve_guest_cover_and_remixer_credits() -> None:
    assert track_names("Письмо_для_Мая.mp3", "Мая") == ("Письмо для Мая", "Мая")
    credit = "Север (настоящее имя Иван Л.) feat. Мая"
    assert track_names("Cold ft. HS87", credit) == ("Cold ft. HS87", "Север feat. Мая")
    assert track_names("Cold", "Север (настоящее имя Иван Л.) (feat. Мая)")[1] == "Север (feat. Мая)"
    assert track_names("The_End_ft._RO", "RO")[0] == "The End ft. RO"
    assert track_names("Mercy (LoSt. Remix)", "LoSt.")[0] == "Mercy (LoSt. Remix)"
    assert track_names("Song (Original Artist cover)", "Cover Band")[0] == "Song (Original Artist cover)"
    assert track_names("Quiet_song_-_AC/DC", "AC/DC") == ("Quiet song", "AC/DC")
    assert track_names("Cold", "Earth, Wind & Fire") == ("Cold", "Earth, Wind & Fire")
    assert track_names("Cover", "Group / Soloist")[1] == "Group / Soloist"
    doc = {"fields": {"composer": "Real Name"}}
    assert project_names(doc, "Piece", "Project Name") is doc
