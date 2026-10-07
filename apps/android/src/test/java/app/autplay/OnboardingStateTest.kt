package app.autplay

import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnboardingStateTest {
    @Test
    fun completingPersistsTheNormalizedDeviceLocalIdAndRevisionTogether() = runBlocking {
        val store = FakeSettingsStore()

        assertTrue(completeOnboarding(store, "@Local_Listener"))
        assertEquals(ONBOARDING_REVISION, store.value.onboardingRevision)
        assertEquals("local_listener", store.value.pendingPublicId)
        assertTrue(hasCompletedOnboarding(store.value))
    }

    @Test
    fun failedWriteKeepsOnboardingOpen() = runBlocking {
        val store = FakeSettingsStore(failWrites = true)

        assertFalse(completeOnboarding(store, "local_listener"))
        assertEquals(0, store.value.onboardingRevision)
        assertEquals(null, store.value.pendingPublicId)
    }

    @Test
    fun existingCompletedEducationStillRequiresAnIdAfterUpgrade() {
        assertFalse(hasCompletedOnboarding(NonSecretSettings(onboardingRevision = ONBOARDING_REVISION)))
        assertTrue(hasCompletedOnboarding(NonSecretSettings(
            onboardingRevision = ONBOARDING_REVISION,
            pendingPublicId = "local_listener",
        )))
    }

    @Test
    fun invalidPublicIdCannotCompleteOnboarding() = runBlocking {
        val store = FakeSettingsStore()
        assertFalse(completeOnboarding(store, "invalid name"))
        assertEquals(0, store.value.onboardingRevision)
        assertEquals(null, store.value.pendingPublicId)
    }

    private class FakeSettingsStore(
        private val failWrites: Boolean = false,
    ) : NonSecretSettingsStore {
        private val state = MutableStateFlow(NonSecretSettings())
        val value: NonSecretSettings get() = state.value
        override val settings: Flow<NonSecretSettings> = state

        override suspend fun update(settings: NonSecretSettings) {
            if (failWrites) error("disk unavailable")
            state.value = settings
        }
    }
}
