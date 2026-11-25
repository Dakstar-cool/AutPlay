package app.autplay.ui

import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Test

class ArtistNavigationTest {
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
            Triple("По разные стороны (Mood Video)", "Максим Свобода", "По разные стороны"),
            Triple("Song [Mood Video] (Acoustic)", "Artist", "Song (Acoustic)"),
            Triple("Mood Video", "Artist", "Mood Video"),
            Triple("Hello @someone", "Artist", "Hello @someone"),
        )
        for ((raw, artist, expected) in cases) {
            val names = app.autplay.application.artist.trackNames(raw, artist)
            assertEquals(expected, names.title)
            assertEquals(artist, names.artist)
            assertEquals(expected, app.autplay.application.artist.trackNames(expected, artist).title)
        }
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
        assertEquals("Duet feat. Silver Echo", app.autplay.application.artist.trackNames(
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
