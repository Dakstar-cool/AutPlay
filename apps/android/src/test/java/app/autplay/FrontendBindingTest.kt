package app.autplay

import app.autplay.application.wave.isCurrentWaveCallback
import app.autplay.application.wave.runWaveTransportCall
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.wave.WavePrefetchMode
import app.autplay.playback.resolveCurrentTrackRefId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FrontendBindingTest {
    @Test
    fun `wave transport calls leave the caller thread`() = kotlinx.coroutines.runBlocking {
        val callerThread = Thread.currentThread().name

        val transportThread = runWaveTransportCall { Thread.currentThread().name }

        assertNotEquals(callerThread, transportThread)
    }

    @Test
    fun `settings update failure becomes stable UI error`() = runBlocking {
        val store = object : NonSecretSettingsStore {
            override val settings: Flow<NonSecretSettings> = flowOf(NonSecretSettings())
            override suspend fun update(settings: NonSecretSettings) = error("disk unavailable")
        }

        assertEquals(
            "SETTINGS_UPDATE_UNAVAILABLE",
            updateFrontendSettings(store) { it.copy(accentPalette = "BLUE") },
        )
    }

    @Test
    fun `current-track actions resolve the active queue entry rather than first library item`() {
        val entries = listOf("queue-a" to "track-a", "queue-b" to "track-b")

        assertEquals("track-b", resolveCurrentTrackRefId("queue-b", entries))
    }

    @Test
    fun `wave callback must match both room and connection generation`() {
        assertTrue(isCurrentWaveCallback("room-b", 2, "room-b", 2))
        assertFalse(isCurrentWaveCallback("room-b", 2, "room-a", 2))
        assertFalse(isCurrentWaveCallback("room-b", 2, "room-b", 1))
    }

    @Test
    fun `wave prefetch setting is parsed for every supported mode`() {
        WavePrefetchMode.entries.forEach { mode ->
            assertEquals(mode, AutPlayRuntime.wavePrefetchMode(mode.name))
        }
        assertEquals(WavePrefetchMode.NEXT, AutPlayRuntime.wavePrefetchMode("UNKNOWN"))
    }
}
