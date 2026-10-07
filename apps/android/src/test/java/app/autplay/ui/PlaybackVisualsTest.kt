package app.autplay.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class PlaybackVisualsTest {
    @Test
    fun paletteIsStableForTheSameTrackAndVariesAcrossKnownSeeds() {
        assertEquals(playbackVisualPalette("Quiet Signals"), playbackVisualPalette("Quiet Signals"))
        assertNotEquals(playbackVisualPalette("Quiet Signals"), playbackVisualPalette("Northern Lights"))
    }

}
