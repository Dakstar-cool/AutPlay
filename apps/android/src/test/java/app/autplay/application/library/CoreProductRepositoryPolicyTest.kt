package app.autplay.application.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CoreProductRepositoryPolicyTest {
    @Test
    fun `available persisted content can play and revoked content cannot`() {
        assertEquals(CoreTrackAvailability.PLAYABLE_LOCAL, CoreProductDetailPolicy.availability(listOf(audio("AVAILABLE", true))))
        assertEquals(CoreTrackAvailability.PERMISSION_REVOKED, CoreProductDetailPolicy.availability(listOf(audio("PERMISSION_REVOKED", false))))

        val capabilities = CoreProductDetailPolicy.capabilities(input(audioStates = listOf(audio("PERMISSION_REVOKED", false))))
        assertFalse(capabilities.contains(CoreTrackDetailCapability.PLAY))
        assertTrue(capabilities.contains(CoreTrackDetailCapability.REAUTHORIZE_LIBRARY_ROOT))
    }

    @Test
    fun `library mutation capability reflects existing entry state`() {
        val active = CoreProductDetailPolicy.capabilities(input(libraryEntry = libraryEntry(removedAtMs = null)))
        val removed = CoreProductDetailPolicy.capabilities(input(libraryEntry = libraryEntry(removedAtMs = 10)))

        assertTrue(active.contains(CoreTrackDetailCapability.REMOVE_FROM_LIBRARY))
        assertFalse(active.contains(CoreTrackDetailCapability.RESTORE_TO_LIBRARY))
        assertTrue(removed.contains(CoreTrackDetailCapability.RESTORE_TO_LIBRARY))
        assertFalse(removed.contains(CoreTrackDetailCapability.REMOVE_FROM_LIBRARY))
    }

    @Test
    fun `unresolved identity exposes review while resolved does not`() {
        assertTrue(CoreProductDetailPolicy.capabilities(input(resolutionStatus = "UNRESOLVED")).contains(CoreTrackDetailCapability.OPEN_IDENTITY_REVIEW))
        assertFalse(CoreProductDetailPolicy.capabilities(input(resolutionStatus = "RESOLVED")).contains(CoreTrackDetailCapability.OPEN_IDENTITY_REVIEW))
    }

    @Test
    fun `download appears only when an owning variant is available`() {
        assertTrue(
            CoreProductDetailPolicy.capabilities(input(hasDownloadableVariant = true))
                .contains(CoreTrackDetailCapability.DOWNLOAD),
        )
        assertFalse(
            CoreProductDetailPolicy.capabilities(input(hasDownloadableVariant = false))
                .contains(CoreTrackDetailCapability.DOWNLOAD),
        )
    }

    private fun input(
        libraryEntry: CoreLibraryMembership? = null,
        audioStates: List<CoreAudioCapabilityState> = emptyList(),
        resolutionStatus: String = "RESOLVED",
        hasDownloadableVariant: Boolean = false,
    ) = CoreTrackCapabilityInput(
        libraryMembership = libraryEntry,
        audioStates = audioStates,
        hasDownloadableVariant = hasDownloadableVariant,
        resolutionStatus = resolutionStatus,
    )

    private fun libraryEntry(removedAtMs: Long?) = CoreLibraryMembership(removedAtMs != null)

    private fun audio(status: String, persisted: Boolean) = CoreAudioCapabilityState(status, persisted)
}
