package app.autplay

import app.autplay.ui.UiDestination
import app.autplay.ui.shouldUseDarkStatusBarIcons
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MainAdaptiveShellChromeTest {
    @Test
    fun homeKeepsTransportAvailableWhileItsHeroScrollsAway() {
        assertTrue(shouldShowPersistentPlayerChrome(UiDestination.Home, hasMedia = true))
        assertFalse(shouldShowPersistentPlayerChrome(UiDestination.Home, hasMedia = false))
    }

    @Test
    fun persistentPlayerChromeRemainsAvailableAwayFromHomeAndExpandedPlayer() {
        assertTrue(shouldShowPersistentPlayerChrome(UiDestination.Search, hasMedia = true))
        assertTrue(shouldShowPersistentPlayerChrome(UiDestination.Library, hasMedia = true))
        assertFalse(shouldShowPersistentPlayerChrome(UiDestination.NowPlaying, hasMedia = true))
    }

    @Test
    fun statusIconsFollowTheSurfaceThemeIncludingThePlayer() {
        assertTrue(shouldUseDarkStatusBarIcons(lightTheme = true))
        assertFalse(shouldUseDarkStatusBarIcons(lightTheme = false))
    }
}
