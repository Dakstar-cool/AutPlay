package app.autplay

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PresentationAttemptStateTest {
    @Test
    fun `failed write can retry while success remains idempotent`() {
        val state = PresentationAttemptState()

        assertTrue(state.begin())
        assertFalse(state.begin())
        state.complete(false)

        assertTrue(state.begin())
        state.complete(true)
        assertFalse(state.begin())
    }
}
