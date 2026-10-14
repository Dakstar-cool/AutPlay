package app.autplay.application.wave

import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WavePlaybackAnchorTest {
    private val anchor = WavePlaybackAnchor("room-snapshot", "room-entry", 2, 10_000, 70_000)

    @Test fun correctionsCannotCrossIntoAnOrdinaryQueueOrReplacedEntry() {
        assertTrue(anchor.owns("WAVE", "room-snapshot", "room-entry"))
        assertTrue(anchor.owns("GUEST_WAVE", "room-snapshot", "room-entry"))
        assertFalse(anchor.owns("LIBRARY", "room-snapshot", "room-entry"))
        assertFalse(anchor.owns("WAVE", "new-snapshot", "room-entry"))
        assertFalse(anchor.owns("WAVE", "room-snapshot", "new-entry"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun invalidClockLeaseCannotArmPlayback() {
        WavePlaybackAnchor("snapshot", "entry", 2, 10_000, 10_000)
    }

    @Test fun refreshingTheLeaseCannotMoveTheAcceptedMonotonicStart() {
        val renewed = anchor.renewLease(anchor.copy(startElapsedRealtimeMs = 30_000, validUntilElapsedRealtimeMs = 80_000))
        assertEquals(10_000L, renewed.startElapsedRealtimeMs)
        assertEquals(80_000L, renewed.validUntilElapsedRealtimeMs)
    }
}
