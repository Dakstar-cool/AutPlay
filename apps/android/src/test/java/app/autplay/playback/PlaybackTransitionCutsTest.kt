package app.autplay.playback

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackTransitionCutsTest {
    @Test fun joinsLongTracksAtThreeSecondsFromEachBoundary() {
        assertEquals(PlaybackCutBounds(3_000, 37_000), bounds())
        assertEquals(PlaybackCutBounds(0, 37_000), bounds(preserveHead = true))
        assertEquals(PlaybackCutBounds(3_000), bounds(successor = false))
    }

    @Test fun shortUnknownDisabledAndRoomQueuesRetainTheirWholeTimeline() {
        for (duration in listOf(null, -1L, 0L, 1L, 3_000L, 6_000L)) {
            assertEquals(PlaybackCutBounds(), bounds(duration = duration))
        }
        assertEquals(PlaybackCutBounds(3_000, 3_001), bounds(duration = 6_001))
        assertEquals(PlaybackCutBounds(), bounds(enabled = false))
        assertEquals(PlaybackCutBounds(), bounds(seekable = false))
        for (type in listOf(null, "WAVE", "GUEST_WAVE", "FUTURE_QUEUE")) {
            assertEquals(PlaybackCutBounds(), bounds(type = type))
        }
        for (type in listOf("USER", "SEARCH", "LIBRARY", "PLAYLIST")) {
            assertEquals(PlaybackCutBounds(3_000, 37_000), bounds(type = type))
        }
    }

    @Test fun sleepTimerAndRestoredTailPlayThroughToTheOriginalEnd() {
        assertEquals(PlaybackCutBounds(3_000), bounds(stopAfter = true))
        assertEquals(PlaybackCutBounds(3_000), bounds(preserveTail = true))
        assertEquals(PlaybackCutBounds(), bounds(preserveHead = true, preserveTail = true))
    }

    @Test fun seekAndRestoreConvertSourceCoordinatesAndClampToPlayableFragment() {
        val cut = bounds()
        assertEquals(3_000L, cut.sourcePosition(0))
        assertEquals(12_345L, cut.sourcePosition(cut.playerPosition(12_345)))
        assertEquals(0L, cut.playerPosition(0))
        assertEquals(34_000L, cut.playerPosition(40_000))
        assertEquals(37_000L, cut.sourcePosition(cut.playerPosition(40_000)))
        assertEquals(37_000L, cut.sourcePosition(34_007))
        val initial = bounds(preserveHead = true)
        assertEquals(500L, initial.sourcePosition(initial.playerPosition(500)))
        assertEquals(39_000L, bounds(preserveTail = true).sourcePosition(36_000))
    }

    @Test fun skippedCoordinatesNeverCountAsPlayedTimeOrChangeFullDuration() {
        val entry = PlaybackQueueEntry(id(1), id(2), 0)
        val session = LogicalListeningSession.start(entry, id(3), 100, bounds().sourcePosition(0))
        val checkpoint = LogicalListeningSession.checkpoint(session, bounds().sourcePosition(1_000), 1_000)
        val event = LogicalListeningSession.finalizeOnce(checkpoint, 37_000, 40_000, 500).second!!
        assertEquals(1_500L, event.playedMs)
        assertEquals(40_000L, event.durationMs)
        assertEquals(37_000L, event.endPositionMs)
        assertEquals(3_000L, session.startPositionMs)
    }

    private fun bounds(
        enabled: Boolean = true, type: String? = "USER", duration: Long? = 40_000,
        seekable: Boolean = true, preserveHead: Boolean = false, successor: Boolean = true,
        stopAfter: Boolean = false, preserveTail: Boolean = false,
    ) = PlaybackTransitionCuts.bounds(enabled, type, duration, seekable, preserveHead, successor, stopAfter, preserveTail)

    private fun id(value: Int) = app.autplay.domain.LocalId("00000000-0000-0000-0000-${value.toString().padStart(12, '0')}")
}
