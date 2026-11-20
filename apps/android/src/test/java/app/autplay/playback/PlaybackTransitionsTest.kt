package app.autplay.playback

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackTransitionsTest {
    @Test fun preloadIsBoundedAndCanBeDisabledWithoutCuttingSources() {
        assertEquals(8_000_000L, PlaybackTransitions.preloadDurationUs(true))
        assertEquals(0L, PlaybackTransitions.preloadDurationUs(false))
        assertEquals(8_000_000L, PlaybackTransitions.preloadDurationUs(false, "WAVE"))
        assertEquals(8_000_000L, PlaybackTransitions.preloadDurationUs(false, "GUEST_WAVE"))
    }
}
