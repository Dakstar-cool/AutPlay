package app.autplay

import app.autplay.application.playback.ActiveQueueContext
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MyWaveQueuePolicyTest {
    @Test fun onlyAbsentOrKnownOrdinaryQueuesAllowPersonalPlayback() {
        assertTrue(canStartMyWave(ActiveQueueContext.Absent, false))
        for (type in listOf("USER", "LIBRARY", "SEARCH", "PLAYLIST")) {
            assertTrue(canStartMyWave(ActiveQueueContext.Loaded("queue", "entry", type), false))
        }
        for (type in listOf("WAVE", "GUEST_WAVE", "FUTURE")) {
            assertFalse(canStartMyWave(ActiveQueueContext.Loaded("queue", "entry", type), false))
        }
        assertFalse(canStartMyWave(ActiveQueueContext.Loading, false))
        assertFalse(canStartMyWave(ActiveQueueContext.Unavailable, false))
    }

    @Test fun joinedRoomBlocksPersonalPlaybackEvenBeforeItsQueueIsPrepared() {
        assertFalse(canStartMyWave(ActiveQueueContext.Absent, true))
        assertFalse(canStartMyWave(ActiveQueueContext.Loaded("queue", "entry", "USER"), true))
    }
}
