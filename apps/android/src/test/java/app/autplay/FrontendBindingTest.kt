package app.autplay

import app.autplay.application.wave.isCurrentWaveCallback
import app.autplay.application.wave.runWaveTransportCall
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
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

    @Test
    fun `recommendation response lease rejects same-id reconnect with a new M5 checkpoint`() {
        val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
        val user = UserId("22222222-2222-4222-8222-222222222222")
        val device = DeviceId("33333333-3333-4333-8333-333333333333")
        val binding = ClientEventBinding(user, device, profile)
        val oldCheckpoint = M5BindingCheckpoint(
            "44444444-4444-4444-8444-444444444444",
            "55555555-5555-4555-8555-555555555555",
            1,
            "a".repeat(64),
            "m5-key-old",
            "66666666-6666-4666-8666-666666666666",
            "77777777-7777-4777-8777-777777777777",
            0,
        )
        val captured = NonSecretSettings(
            activeServerProfileId = profile,
            activeUserId = user,
            deviceId = device,
            serverBaseUrl = "https://autplay.example",
            m5Binding = oldCheckpoint,
        )
        val reconnected = captured.copy(
            m5Binding = oldCheckpoint.copy(
                bindingCommitId = "88888888-8888-4888-8888-888888888888",
                sessionId = "99999999-9999-4999-8999-999999999999",
                sessionGeneration = 1,
            ),
        )

        assertTrue(isSameRecommendationBinding(captured, captured, binding))
        assertFalse(isSameRecommendationBinding(captured, reconnected, binding))
        assertTrue(
            isSameRecommendationBinding(
                captured,
                captured.copy(
                    m5Binding = oldCheckpoint.copy(
                        sessionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        sessionGeneration = 1,
                    ),
                ),
                binding,
            ),
        )
    }
}
