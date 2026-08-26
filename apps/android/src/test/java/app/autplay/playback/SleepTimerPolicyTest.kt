package app.autplay.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SleepTimerPolicyTest {
    @Test
    fun guestQueueRequiresFreshProcessCommand() {
        assertFalse(GuestQueueRestorePolicy.allows("GUEST_WAVE", null))
        assertFalse(GuestQueueRestorePolicy.allows("GUEST_WAVE", ""))
        assertTrue(GuestQueueRestorePolicy.allows("GUEST_WAVE", "snapshot-id"))
        assertTrue(GuestQueueRestorePolicy.allows("WAVE", null))
    }

    @Test
    fun ordinaryRestoredQueueCanScheduleTimerButWaveAndUnknownQueuesCannot() {
        assertTrue(SleepTimerPolicy.allows("LIBRARY"))
        assertTrue(SleepTimerPolicy.allows("PLAYLIST"))
        assertFalse(SleepTimerPolicy.allows("WAVE"))
        assertFalse(SleepTimerPolicy.allows("FUTURE_QUEUE"))
        assertFalse(SleepTimerPolicy.allows(null))
    }

    @Test
    fun deadlineUsesMonotonicNowAndAcceptedDuration() {
        assertEquals(160_000L, SleepTimerPolicy.deadline(100_000L, 60_000L))
        assertEquals(
            43_300_000L,
            SleepTimerPolicy.deadline(100_000L, SleepTimerPolicy.MAX_DURATION_MS),
        )
    }

    @Test
    fun commandRejectsDurationsOutsideOneMinuteThroughTwelveHours() {
        PlaybackCommand.ScheduleSleepTimer(SleepTimerPolicy.MIN_DURATION_MS)
        PlaybackCommand.ScheduleSleepTimer(SleepTimerPolicy.MAX_DURATION_MS)

        assertThrows(IllegalArgumentException::class.java) {
            PlaybackCommand.ScheduleSleepTimer(SleepTimerPolicy.MIN_DURATION_MS - 1)
        }
        assertThrows(IllegalArgumentException::class.java) {
            PlaybackCommand.ScheduleSleepTimer(SleepTimerPolicy.MAX_DURATION_MS + 1)
        }
    }

    @Test
    fun waitIsSlicedAndRecomputedFromElapsedRealtimeDeadline() {
        assertEquals(30_000L, SleepTimerPolicy.nextDelay(1_000_000L, 100_000L))
        assertEquals(12_345L, SleepTimerPolicy.nextDelay(112_345L, 100_000L))
        assertEquals(0L, SleepTimerPolicy.nextDelay(100_000L, 100_001L))
    }

    @Test
    fun staleStopAfterTrackRequestIsRejectedInsteadOfClearingAnExistingTimer() {
        assertEquals(
            StopAfterCurrentItemDecision.REJECT_STALE,
            SleepTimerPolicy.stopAfterCurrentItemDecision("LIBRARY", "old-entry", "new-entry"),
        )
        assertEquals(
            StopAfterCurrentItemDecision.ARM,
            SleepTimerPolicy.stopAfterCurrentItemDecision("LIBRARY", "entry", "entry"),
        )
        assertEquals(
            StopAfterCurrentItemDecision.CLEAR_UNSUPPORTED_QUEUE,
            SleepTimerPolicy.stopAfterCurrentItemDecision("WAVE", "entry", "entry"),
        )
    }
}
