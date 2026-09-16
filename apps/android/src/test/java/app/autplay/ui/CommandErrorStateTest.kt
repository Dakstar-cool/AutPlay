package app.autplay.ui

import org.junit.Assert.*
import org.junit.Test

class CommandErrorStateTest {
    @Test fun navigationClearsOnlyThePriorEventAndRetainsLateFailures() {
        val state = CommandErrorState()
        var error by state
        error = "PLAYBACK_UNAVAILABLE"
        val previous = state.event
        error = "PLAYBACK_UNAVAILABLE"
        state.clearIfCurrent(previous)
        assertEquals("PLAYBACK_UNAVAILABLE", error)
        state.clearIfCurrent(state.event)
        assertNull(error)
        val beforeNavigation = state.event
        error = "DOWNLOAD_UNAVAILABLE"
        state.clearIfCurrent(beforeNavigation)
        assertEquals("DOWNLOAD_UNAVAILABLE", error)
        error = null
        assertNull(state.event)
    }
}
