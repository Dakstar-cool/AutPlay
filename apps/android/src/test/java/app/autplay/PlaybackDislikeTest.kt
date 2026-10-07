package app.autplay

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PlaybackDislikeTest {
    @Test fun skipIsDispatchedBeforeFeedbackWriteAndItsFailureDoesNotUndoSkip() = runBlocking {
        val events = mutableListOf<String>()
        applyPlaybackDislike(
            advance = { events += "next" },
            record = { events += "record"; error("disk unavailable") },
            reportError = { events += it },
        )
        assertEquals(listOf("next", "record", "PREFERENCE_UNAVAILABLE"), events)
    }

    @Test fun navigationFailureStillPersistsDislike() = runBlocking {
        val events = mutableListOf<String>()
        applyPlaybackDislike(
            advance = { error("service unavailable") },
            record = { events += "record" },
            reportError = { events += it },
        )
        assertEquals(listOf("QUEUE_NAVIGATION_UNAVAILABLE", "record"), events)
    }

    @Test fun cancellationRemainsCancellation() {
        assertThrows(CancellationException::class.java) {
            runBlocking {
                applyPlaybackDislike({ throw CancellationException() }, { error("must not run") }, { error(it) })
            }
        }
    }
}
