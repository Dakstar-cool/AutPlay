package app.autplay.ui.desktop

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

public class DesktopShellPolicyTest {
    @Test
    public fun unavailableAdapterOrLockedStateCannotEnableAnyCommand(): Unit {
        val disabled = DesktopPlaybackControls(false, false, false, false)
        assertEquals(disabled, DesktopPlaybackControls.from(readyState(), adapterAvailable = false))
        assertEquals(
            disabled,
            DesktopPlaybackControls.from(readyState().copy(connection = PlaybackConnectionState.Disconnected), true),
        )
        assertEquals(disabled, DesktopPlaybackControls.from(readyState().copy(mediaId = null), true))
        PlaybackControlLockReason.entries.forEach { reason ->
            assertEquals(disabled, DesktopPlaybackControls.from(readyState().copy(controls = PlaybackControlGate.Locked(reason)), true))
        }
    }

    @Test
    public fun connectedStateRespectsNavigationAndSeekAvailability(): Unit {
        assertEquals(DesktopPlaybackControls(true, true, true, true), DesktopPlaybackControls.from(readyState(), true))
        assertEquals(
            DesktopPlaybackControls(true, false, false, true),
            DesktopPlaybackControls.from(readyState().copy(previousMediaId = null, nextMediaId = null), true),
        )
        listOf(
            readyState().copy(isLive = true),
            readyState().copy(isSeekable = false),
            readyState().copy(seekEnabled = false),
            readyState().copy(durationMs = null),
            readyState().copy(durationMs = 0),
            readyState().copy(durationMs = -1),
        ).forEach { state ->
            val controls = DesktopPlaybackControls.from(state, true)
            assertTrue(controls.playPause)
            assertFalse(controls.seek)
        }
    }

    @Test
    public fun bothLanguagesCoverEveryVisibleCopyEntry(): Unit {
        DesktopLanguage.entries.forEach { language ->
            DesktopText.entries.forEach { text -> assertTrue(text.inLanguage(language).isNotBlank()) }
            DesktopSection.entries.forEach { section -> assertTrue(section.label(language).isNotBlank()) }
        }
        assertEquals("Библиотека", DesktopSection.Library.label(DesktopLanguage.Russian))
        assertEquals("Settings", DesktopSection.Settings.label(DesktopLanguage.English))
    }

    private fun readyState(): PlaybackPresentationState = PlaybackPresentationState(
        mediaId = "entry-current",
        durationMs = 180_000,
        isSeekable = true,
        connection = PlaybackConnectionState.Connected,
        context = ActiveQueueContext.Loaded("snapshot", "entry-current", "LIBRARY"),
        controls = PlaybackControlGate.Allowed,
        seekEnabled = true,
        previousMediaId = "entry-previous",
        nextMediaId = "entry-next",
    )
}
