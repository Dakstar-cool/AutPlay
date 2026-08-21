package app.autplay.ui

import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Test

class ArtistNavigationTest {
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
