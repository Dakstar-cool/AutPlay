package app.autplay.ui

import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HomePlaybackHeroStateTest {
    @Test
    fun activePlaybackWinsOverHomeFallback() {
        val result = buildHomePlaybackHeroUiState(
            homeState = homeState(
                continueListening = HomeContinueUiItem("fallback", "Fallback", "Artist", "0:12"),
            ),
            playerState = PlaybackPresentationState(
                mediaId = "queue-entry",
                title = "Active",
                artist = "Current artist",
                isPlaying = true,
                controls = PlaybackControlGate.Allowed,
            ),
            currentTrackRefId = "active-track",
            currentTrackLiked = true,
        )

        assertEquals("active-track", result.trackId)
        assertEquals("Active", result.title)
        assertTrue(result.isPlaying)
        assertTrue(result.hasActivePlayback)
        assertTrue(result.liked)
        assertTrue(result.playPauseEnabled)
    }

    @Test
    fun restoredPlaybackRemainsControllableBeforeLocalTrackMappingArrives() {
        val result = buildHomePlaybackHeroUiState(
            homeState = homeState(
                continueListening = HomeContinueUiItem("fallback", "Fallback", "Artist", "0:12"),
            ),
            playerState = PlaybackPresentationState(
                mediaId = "restored-queue-entry",
                title = "Restored playback",
                artist = "Current artist",
                isPlaying = true,
                controls = PlaybackControlGate.Allowed,
            ),
            currentTrackRefId = null,
            currentTrackLiked = false,
        )

        assertNull(result.trackId)
        assertEquals("Restored playback", result.title)
        assertTrue(result.hasActivePlayback)
        assertTrue(result.isPlaying)
        assertTrue(result.playPauseEnabled)
    }

    @Test
    fun localContinueItemProvidesAnHonestPausedFallback() {
        val result = buildHomePlaybackHeroUiState(
            homeState = homeState(
                continueListening = HomeContinueUiItem("fallback", "Fallback", "Artist", "0:12"),
            ),
            playerState = PlaybackPresentationState(),
            currentTrackRefId = null,
            currentTrackLiked = true,
        )

        assertEquals("fallback", result.trackId)
        assertEquals("Fallback", result.title)
        assertFalse(result.isPlaying)
        assertFalse(result.hasActivePlayback)
        assertFalse(result.liked)
        assertTrue(result.playPauseEnabled)
    }

    @Test
    fun lockedActivePlaybackDisablesOnlyDirectTransportControl() {
        val result = buildHomePlaybackHeroUiState(
            homeState = homeState(null),
            playerState = PlaybackPresentationState(
                mediaId = "wave-entry",
                title = "Wave track",
                isPlaying = true,
                controls = PlaybackControlGate.Locked(
                    PlaybackControlLockReason.WAVE_QUEUE,
                ),
            ),
            currentTrackRefId = "wave-track",
            currentTrackLiked = false,
        )

        assertTrue(result.hasActivePlayback)
        assertFalse(result.playPauseEnabled)
    }

    @Test
    fun temporarilyUnavailableOrdinaryPlaybackAlsoDisablesDirectTransportControl() {
        val result = buildHomePlaybackHeroUiState(
            homeState = homeState(null),
            playerState = PlaybackPresentationState(
                mediaId = "ordinary-entry",
                title = "Local track",
                isPlaying = false,
                controls = PlaybackControlGate.Locked(
                    PlaybackControlLockReason.CONTEXT_LOADING,
                ),
            ),
            currentTrackRefId = "local-track",
            currentTrackLiked = true,
        )

        assertTrue(result.hasActivePlayback)
        assertFalse(result.playPauseEnabled)
        assertTrue(result.liked)
    }

    private fun homeState(continueListening: HomeContinueUiItem?) = HomeScreenUiState(
        localMode = true,
        recommendationLoading = false,
        offlineFallback = false,
        releases = emptyList(),
        recommendations = emptyList(),
        continueListening = continueListening,
    )
}
