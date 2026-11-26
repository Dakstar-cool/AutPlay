package app.autplay.ui

import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Test

class ArtistNavigationTest {
    @Test fun explicitGuestsMoveTogetherWithTheTitle() {
        fun check(title: String, artist: String, expectedTitle: String, expectedArtist: String) {
            val names = app.autplay.application.artist.trackNames(title, artist)
            assertEquals(expectedTitle, names.title)
            assertEquals(expectedArtist, names.artist)
            assertEquals(names, app.autplay.application.artist.trackNames(names.title, names.artist))
        }
        check("Рядом быть feat. Хелависа&#x20;", "После 11", "Рядом быть", "После 11 feat. Хелависа")
        check("Song [Ft. A & B] (Live)", "Lead feat. A", "Song (Live)", "Lead feat. A, B")
        check("Song featuring Guest", "Lead, Guest", "Song", "Lead, Guest")
        check("death bed | Feat. beabadoobee", "Powfu", "death bed", "Powfu feat. beabadoobee")
        check("6 ft Under", "Singer", "6 ft Under", "Singer")
        check("[IKO NICE ft DJ BEN]", "NAITA", "IKO NICE", "NAITA feat. DJ BEN")
        check("[NAITA ft DJ BEN]", "NAITA", "[NAITA ft DJ BEN]", "NAITA")
        check("Be Your Friend (Vigiland Feat. Alexander Tidebrink)", "Vigiland", "Be Your Friend", "Vigiland feat. Alexander Tidebrink")
        check("Duet (Rock Remix feat. Tom)", "Singer", "Duet (Rock Remix)", "Singer feat. Tom")
        check("Duet +feat. Tom #live", "Singer", "Duet #live", "Singer feat. Tom")
        check("Song (Guest feat. B Remix)", "Lead", "Song (Guest feat. B Remix)", "Lead")
    }
    @Test fun normalizedTitlesRetainVersionsAndCreditsRetainPartners() {
        assertEquals("песня - live", app.autplay.application.artist.musicSearchKey("  Песня\u00a0—　Live "))
        assertEquals("Песня (Remix)", app.autplay.application.artist.musicDisplayTitle("Песня   (Remix)"))
        assertEquals(listOf("25/17", "AC/DC"), app.autplay.application.artist.displayArtists("25-17/AC/DC"))
        assertEquals(listOf("Хаски", "25/17"), app.autplay.application.artist.displayArtists("Хаски/25 17"))
    }

    @Test fun sourceTitlesSeparatePerformanceCreditsFromVideoLabels() {
        val cases = listOf(
            Triple("Left You At The Farm @16 тонн", "Chinawoman", "Left You At The Farm (Live at 16 тонн)"),
            Triple("Life is wasted on the living | Monitor Sessions", "Moto Boy", "Life is wasted on the living (Monitor Sessions)"),
            Triple("Play With Fire | Stabal Session", "Sam Tinnesz", "Play With Fire (Stabal Session)"),
            Triple("Play With Fire [Stabal ssesion]", "Sam Tinnesz", "Play With Fire (Stabal Session)"),
            Triple("The Session", "Artist", "The Session"),
            Triple("По разные стороны (Mood Video)", "Максим Свобода", "По разные стороны"),
            Triple("Song [Mood Video] (Acoustic)", "Artist", "Song (Acoustic)"),
            Triple("Mood Video", "Artist", "Mood Video"),
            Triple("Hello @someone", "Artist", "Hello @someone"),
            Triple("молчи speed up", "кис-кис", "молчи (Sped Up)"),
            Triple("icantbelieveiletyougetaway | High quality instrumental", "aldn", "icantbelieveiletyougetaway (Instrumental)"),
            Triple("Song [super slowed + reverb]", "Artist", "Song (Super Slowed + Reverb)"),
            Triple("Take It Slow", "Artist", "Take It Slow"),
            Triple("Speed Up", "Artist", "Speed Up"),
        )
        for ((raw, artist, expected) in cases) {
            val names = app.autplay.application.artist.trackNames(raw, artist)
            assertEquals(expected, names.title)
            assertEquals(artist, names.artist)
            assertEquals(expected, app.autplay.application.artist.trackNames(expected, artist).title)
        }
    }

    @Test fun downloadSiteTagsAreRemovedFromSourceTitlesAndCredits() {
        val names = app.autplay.application.artist.trackNames("[muzmo.ru] молчи speed up - Mp3on.net.mp3", "кис-кис | hitmos")
        assertEquals("молчи (Sped Up)", names.title)
        assertEquals("кис-кис", names.artist)
        assertEquals(null, app.autplay.application.artist.trackNames("Track", "[muzmo.ru] hitmos").artist)
        assertEquals("Hitmosaic", app.autplay.application.artist.trackNames("Hitmosaic", "AC/DC").title)
        assertEquals("Band.fm", app.autplay.application.artist.trackNames("Track", "Band.fm").artist)
    }

    @Test fun libraryFindsJoinedSpellingsInEitherDirection() {
        val spaced = "I can’t believe I let you get away"
        val joined = "icantbelieveiletyougetaway"
        val query = app.autplay.application.artist.TrackTitleQuery(spaced)
        assertEquals(true, query.matches("$joined (Instrumental)"))
        assertEquals(true, app.autplay.application.artist.TrackTitleQuery(joined).matches(spaced))
        assertEquals(false, query.matches("icantbelieveyouletmegetaway"))
        assertEquals(true, app.autplay.application.artist.TrackTitleQuery("[muzmo.ru] Play With Fire | Stabal Session")
            .matches("Play With Fire (Stabal Session)"))
    }

    @Test fun filenameLabelsKeepUnverifiedNumbersAndManualFields() {
        val raw = "The_Winery_Dogs_-_The_Dying_64218614.mp3"
        val names = app.autplay.application.artist.trackNames(raw, "Unknown artist")
        assertEquals("The Dying 64218614", names.title)
        assertEquals("The Winery Dogs", names.artist)
        val manual = app.autplay.application.artist.trackNames(raw, null, "My_Mix.mp3", true, true)
        assertEquals("My_Mix.mp3", manual.title)
        assertEquals(null, manual.artist)
        assertEquals("Muse/Ellie Goulding", app.autplay.application.artist.trackNames(
            "Hush_(Live).mp3", "Muse/Ellie Goulding").artist)
    }

    @Test fun unpackedTitlesPreserveArtistSpellingsAndEditVersions() {
        val raw = "рай_открыт_для_всех_Е_Алехин_публика_выбирает_сама"
        assertEquals("рай открыт для всех", app.autplay.application.artist.trackNames(raw, "Публика Выбирает Сама").title)
        assertEquals("SHUM (Single Edit)", app.autplay.application.artist.trackNames(
            "Go_A - SHUM (Official Lyric Video - Single Edit)", "Go_A").title)
        assertEquals("artifact_001", app.autplay.application.artist.trackNames("artifact_001", null).title)
        assertEquals("my_song", app.autplay.application.artist.trackNames("my_song", null, "my_song", hasTitle = true).title)
    }

    @Test fun stageCreditsRecognizeExplicitRolesForOtherPerformers() {
        val names = app.autplay.application.artist.trackNames(
            "Evening_song_Lina_Ray_Silver_Echo", "Lina Ray (stage name: Silver Echo)")
        assertEquals("Evening song", names.title)
        assertEquals("Silver Echo", names.artist)
        assertEquals("Evening song Silver Echo", app.autplay.application.artist.trackNames(
            "Evening_song_Silver_Echo", "Silver Echo").title)
        assertEquals("Письмо для Мая", app.autplay.application.artist.trackNames("Письмо_для_Мая.mp3", "Мая").title)
        assertEquals("Север feat. Мая", app.autplay.application.artist.trackNames(
            "Трек", "Север (настоящее имя Иван Л.) feat. Мая").artist)
        assertEquals("Mercy (LoSt. Remix)", app.autplay.application.artist.trackNames("Mercy (LoSt. Remix)", "LoSt.").title)
        assertEquals("Duet", app.autplay.application.artist.trackNames(
            "Duet_feat._Silver_Echo.mp3", "Silver Echo").title)
    }

    @Test
    fun ownerVisibleAppearancesMapOnlyToLocalDetailTargets() {
        assertEquals(
            DetailTarget(DetailKind.Track, "local-track"),
            ArtistLocalTarget.Track("local-track").toDetailTarget(),
        )
        assertEquals(
            DetailTarget(DetailKind.Release, "local-release"),
            ArtistLocalTarget.Release("local-release").toDetailTarget(),
        )
    }
}
