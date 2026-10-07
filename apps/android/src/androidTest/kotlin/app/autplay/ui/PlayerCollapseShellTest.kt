package app.autplay.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.player.NowPlayingScreen
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PlayerCollapseShellTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun phonePanelReachesTheRealMiniPlayerBeforeNavigation() = verifyAlignment(390)
    @Test fun tabletPanelReachesTheRealMiniPlayerBeforeNavigation() = verifyAlignment(900)

    private fun verifyAlignment(widthDp: Int) {
        var miniTopPx = Float.NaN
        var density = 1f
        var current: UiDestination = UiDestination.NowPlaying
        compose.setContent {
            val pixels = LocalDensity.current
            density = pixels.density
            var selected by remember { mutableStateOf<UiDestination>(UiDestination.NowPlaying) }
            current = selected
            Box(Modifier.requiredSize(widthDp.dp, 600.dp)) {
                AutPlayTheme {
                    AutPlayAdaptiveShell(
                        selectedDestination = selected,
                        onDestinationSelected = { selected = it },
                        nowPlayingBar = {
                            Box(Modifier.fillMaxWidth().height(80.dp).onGloballyPositioned {
                                miniTopPx = it.positionInRoot().y
                            }) { Text("mini-target") }
                        },
                    ) { destination, padding, _ ->
                        if (destination == UiDestination.NowPlaying) {
                            NowPlayingScreen(
                                state = PlaybackPresentationState(mediaId = "fixture", title = "Fixture track"),
                                onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                                onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                                onLike = {}, onDislike = {}, feedbackEnabled = true,
                                onObservingChanged = {}, modifier = Modifier.padding(padding),
                            )
                        } else Text("collapsed")
                    }
                }
            }
        }
        compose.waitForIdle()
        val stationaryTop = compose.onNodeWithTag("now-playing-panel").getUnclippedBoundsInRoot().top.value * density
        val expectedMiniTop = miniTopPx
        assertTrue(expectedMiniTop > stationaryTop)
        compose.onNodeWithTag("player-collapse-header").performTouchInput {
            down(center)
            moveBy(Offset(0f, expectedMiniTop - stationaryTop + 100f), delayMillis = 500)
        }
        compose.waitForIdle()
        val heldTop = compose.onNodeWithTag("now-playing-panel").getUnclippedBoundsInRoot().top.value * density
        assertEquals(expectedMiniTop, heldTop, 2f)
        compose.runOnIdle { assertEquals(UiDestination.NowPlaying, current) }
        saveScreenshot("player-shell-held-$widthDp.png")
        compose.onNodeWithTag("player-collapse-header").performTouchInput { cancel() }
        compose.waitForIdle()
        compose.onNodeWithContentDescription(context.getString(R.string.player_collapse)).performClick()
        compose.onNodeWithText("collapsed").assertIsDisplayed()
        compose.runOnIdle {
            assertEquals(UiDestination.Home, current)
            assertEquals(expectedMiniTop, miniTopPx, 2f)
        }
    }

    private fun saveScreenshot(name: String) {
        val screenshot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        java.io.File(context.cacheDir, name).outputStream().use {
            check(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it))
        }
    }
}
