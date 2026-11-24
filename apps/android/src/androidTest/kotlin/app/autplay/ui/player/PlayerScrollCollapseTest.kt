package app.autplay.ui.player

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class PlayerScrollCollapseTest {
    @get:Rule val compose = createComposeRule()

    @Test fun downwardDragReturnsScrolledContentBeforeCollapsingPlayer() {
        var collapsed = 0
        compose.setContent {
            AutPlayTheme {
                CompositionLocalProvider(LocalPlayerCollapse provides { collapsed++ }) {
                    Box(Modifier.size(360.dp, 230.dp)) {
                        NowPlayingScreen(PlaybackPresentationState(mediaId = "track", title = "Long track", artist = "Artist"),
                            onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                            onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {}, onLike = {}, onDislike = {},
                            feedbackEnabled = true, onObservingChanged = {})
                    }
                }
            }
        }
        val scroll = compose.onNodeWithTag("now-playing-scroll")
        scroll.performSemanticsAction(SemanticsActions.ScrollBy) { it(0f, 120f) }
        compose.waitForIdle()
        val before = scroll.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        assertTrue(before > 0f)
        val panel = compose.onNodeWithTag("now-playing-panel")
        val top = panel.getUnclippedBoundsInRoot().top
        scroll.performTouchInput {
            down(Offset(width / 2f, height * 0.15f))
            repeat(8) { moveBy(Offset(0f, height * 0.09f), delayMillis = 32) }
            up()
        }
        compose.waitForIdle()
        val after = scroll.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        assertTrue("A downward swipe must scroll back toward the top", after < before)
        assertEquals(top, panel.getUnclippedBoundsInRoot().top)
        compose.runOnIdle { assertEquals(0, collapsed) }
    }

    @Test fun continuousCoverDragFollowsEveryFrameWithoutCollapsingWhileHeld() {
        var collapsed = 0
        var density = 1f
        var touchSlopDp = 0f
        compose.setContent {
            density = LocalDensity.current.density
            touchSlopDp = LocalViewConfiguration.current.touchSlop / density
            AutPlayTheme {
                CompositionLocalProvider(LocalPlayerCollapse provides { collapsed++ }) {
                    Box(Modifier.size(360.dp, 500.dp).testTag("stationary")) {
                        NowPlayingScreen(PlaybackPresentationState(mediaId = "track", title = "Track", artist = "Artist"),
                            onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                            onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {}, onLike = {}, onDislike = {},
                            feedbackEnabled = true, onObservingChanged = {})
                    }
                }
            }
        }
        val panel = compose.onNodeWithTag("now-playing-panel")
        val originalTop = panel.getUnclippedBoundsInRoot().top.value
        compose.mainClock.autoAdvance = false
        compose.onNodeWithTag("stationary").performTouchInput { down(Offset(width / 2f, 150f * density)) }
        repeat(4) { frame ->
            compose.onNodeWithTag("stationary").performTouchInput {
                moveBy(Offset(0f, 20f * density), delayMillis = 16)
            }
            compose.mainClock.advanceTimeByFrame()
            assertEquals("Frame $frame must follow the finger", (frame + 1) * 20f - touchSlopDp,
                panel.getUnclippedBoundsInRoot().top.value - originalTop, 1f)
            compose.runOnIdle { assertEquals(0, collapsed) }
        }
        compose.onNodeWithTag("stationary").performTouchInput { cancel() }
        compose.mainClock.advanceTimeBy(500)
        assertEquals(originalTop, panel.getUnclippedBoundsInRoot().top.value, 1f)
        compose.runOnIdle { assertEquals(0, collapsed) }
    }

    @Test fun updatedCallbackDoesNotCancelAnActiveDrag() {
        val generation = mutableStateOf(0)
        var received = -1
        lateinit var motion: PlayerCollapseMotion
        compose.setContent {
            val current = generation.value
            motion = rememberPlayerCollapseMotion()
            SideEffect { motion.travel = 600f }
            Box(Modifier.fillMaxSize().playerCollapseGesture({ received = current }, motion, true)
                .testTag("stationary")) {
                Box(Modifier.fillMaxSize().graphicsLayer { translationY = motion.offset })
            }
        }
        compose.onNodeWithTag("stationary").performTouchInput {
            down(Offset(100f, 20f))
            moveBy(Offset(0f, 400f), delayMillis = 500)
        }
        compose.waitForIdle()
        compose.runOnIdle { generation.value = 1 }
        compose.waitForIdle()
        compose.runOnIdle { assertTrue("Recomposition must not return the held panel", motion.offset > 300f) }
        compose.onNodeWithTag("stationary").performTouchInput { up() }
        compose.waitForIdle()
        compose.runOnIdle { assertEquals(1, received) }
    }

    @Test fun shortFastSwipeCompletesBelowDistanceThreshold() {
        var calls = 0
        var density = 1f
        var touchSlop = 0f
        lateinit var motion: PlayerCollapseMotion
        compose.setContent {
            density = LocalDensity.current.density
            touchSlop = LocalViewConfiguration.current.touchSlop
            motion = rememberPlayerCollapseMotion()
            SideEffect { motion.travel = 600f * density }
            Box(Modifier.fillMaxSize().playerCollapseGesture({ calls++ }, motion, true).testTag("stationary")) {
                Box(Modifier.fillMaxSize().graphicsLayer { translationY = motion.offset })
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        // Keep a fast swipe in one batch: separate synchronized calls introduce real idle pauses.
        compose.onNodeWithTag("stationary").performTouchInput {
            down(Offset(100f, 20f))
            repeat(4) {
                moveBy(Offset(0f, 20f * density), delayMillis = 16)
            }
            up()
        }
        compose.runOnIdle {
            assertEquals(80f * density - touchSlop, motion.offset, 2f)
            assertTrue("Fast swipe must clear the minimum travel", motion.offset >= 40f * density)
            assertTrue("Travel must remain below the distance-only threshold", motion.offset < 96f * density)
        }
        compose.mainClock.advanceTimeBy(1000)
        compose.runOnIdle { assertEquals(1, calls) }
    }
}
