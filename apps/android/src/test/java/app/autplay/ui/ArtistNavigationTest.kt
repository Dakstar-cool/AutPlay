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
